"""Planning pipeline of the Walk Planner: request -> validated params -> candidates -> route variants,
plus the stateless editing operations (schedule an exact order, insert a place).

  normalize_params(raw, catalog)      validate / normalise a request dict      -> PlanParams
  build_plan(params, catalog, ...)    candidates + plan_variants               -> PlanResult
  schedule(context, sequence, ...)    re-time an edited order exactly          -> VariantState
  insert_place(context, sequence, …)  best position for one more place         -> (VariantState, index)
  to_request_echo / from_request_echo the normalised request round trip of the API

`build_plan` is the dashboard's ``_walk_build`` (dashboard_app.py L4627-4713) with message codes
instead of Streamlit calls; the editing operations are its ``_walk_seq_request`` /
``_walk_add_place`` / the re-plan on render (L4716-4752, L4778-4779). Behaviour is identical (pinned
by the golden baseline) except the v1 changes: must-visit / added places obey the business-status
policy (``closed_forever`` never routable; ``temporarily_closed`` allowed but flagged), unknown
must-visit ids are reported, and personalization comes from ``walk_planner.interest`` (cold start
when no favourites — byte-identical to the dashboard's).

Editing is stateless: a variant travels as its ``sequence`` (EditStops: place_id, kind, slot_index,
activity, dwell_min, dwell_fixed) plus the normalised request echo; everything else (names,
coordinates, hours, status, interest) is re-read from the catalog, so the client never supplies
catalog facts.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from datetime import date as _date, datetime, time as dtime, timedelta
from typing import Any, Literal, Optional, Union

from .candidates import distances_from, extra_candidates, place_candidate, slot_candidates
from .catalog import CityCatalog
from .core import (
    Candidate,
    RoutingProvider,
    WalkPlan,
    WalkRequest,
    best_insertion,
    dwell_for,
    plan_sequence,
    plan_variants,
    reach_radius_km,
)
from .messages import Message, PlannerInputError  # PlannerInputError: re-exported here (contract name)
from .present import clock_label, end_label, stop_activity, stop_kind, stop_slot_index
from .slots import (
    ACTIVITY_BY_LABEL_RU,
    ACTIVITY_TYPES,
    DEFAULT_SHAPE,
    DEFAULT_SLOTS,
    DEFAULT_STYLE,
    DEFAULT_TOP_K,
    SHAPE_BY_LABEL_RU,
    SHAPES,
    STYLE_BY_LABEL_RU,
    STYLES,
)

__all__ = [                     # + PlannerInputError (defined in messages; importable from here too)
    "LIMITS", "DEFAULTS", "STOP_KINDS",
    "SlotSpec", "PlanParams", "PlanContext", "EditStop", "PlanInterest", "VariantState", "PlanResult",
    "normalize_params", "make_context", "to_request_echo", "from_request_echo", "resolve_interest", "build_plan",
    "sequence_request", "edit_stops", "sequence_candidates", "schedule", "insert_place",
]

# API limits (validated by normalize_params / the editing operations; published by /config).
# No limit may reject a plan the planner itself built: a built variant has at most max_slots slot
# stops + core._fill_extras' pool of 80 on-the-way stops + max_must_visits pinned places = 98 stops,
# every visit length within dwell_min (estimates 10..180 min, x1.3 for "chill" -> <= 234; extras 10;
# user-set 5..480), so max_edit_stops leaves room for ~50 inserted places on top of the longest plan
# (pinned by tests/test_pipeline.py).
LIMITS = {
    "window_min": (15, 1440),
    "variants": (1, 5),
    "radius_km": (0.3, 50.0),
    "max_slots": 8,
    "dwell_min": (5, 480),
    "max_must_visits": 10,
    "max_personal_ids": 500,
    "top_k": (1, 50),
    "max_edit_stops": 150,
    "personalization_strength": (0.0, 1.0),
}
DEFAULTS = {
    "start_time": "10:00",
    "window_min": 240,
    "shape": DEFAULT_SHAPE,
    "style": DEFAULT_STYLE,
    "slots": list(DEFAULT_SLOTS),
    "variants": 3,
    "radius_km": 2.5,
    "fill_window": True,
    "known_hours_only": False,
    "top_k": DEFAULT_TOP_K,
    "personalization_strength": 0.5,
}
STOP_KINDS = ("slot", "on_the_way", "pinned")


def _invalid(field_: str, reason: str, **extra) -> PlannerInputError:
    return PlannerInputError("validation_error", {"field": field_, "reason": reason, **extra})


def _shown(v: Any) -> str:
    """A rejected input value for an error's params (text, capped)."""
    return str(v)[:100]


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class SlotSpec:
    """One requested activity slot: activity code + the user's visit length (None = estimate per place)."""

    activity: str
    dwell_min: Optional[int] = None


@dataclass
class PlanParams:
    """A validated, normalised plan request (codes, not labels; ids as str; defaults applied)."""

    city: str
    date: _date
    start_time: dtime
    end_time: dtime
    end_day_offset: int                                     # 0 | 1 (the window ends after midnight)
    shape: str
    start: Union[tuple[float, float], None, Literal["city_center"]]
    start_place_id: Optional[str]
    style: str
    slots: list[SlotSpec]
    must_visit_place_ids: list[str]
    favourite_place_ids: list[str]
    want_to_go_place_ids: list[str]
    personalization_strength: float = 0.5
    variants: int = 3
    radius_km: float = 2.5
    fill_window: bool = True
    known_hours_only: bool = False
    top_k: int = DEFAULT_TOP_K                              # internal / CLI only (bench "Кандидатов на слот")

    @property
    def start_min(self) -> int:
        """Departure as minutes from 00:00."""
        return self.start_time.hour * 60 + self.start_time.minute

    @property
    def window_min(self) -> int:
        """Window length in minutes (15..1440 once validated)."""
        return self.end_day_offset * 1440 + self.end_time.hour * 60 + self.end_time.minute - self.start_min

    @property
    def slot_codes(self) -> list[str]:
        return [s.activity for s in self.slots]


@dataclass
class PlanContext:
    """A request resolved against a catalog: everything the planner and the views derive from the
    params. The params' fields are readable on the context too (``context.shape``)."""

    params: PlanParams
    timezone: str
    weekday: int                       # 0 = Monday
    t0: int                            # departure as minutes from Monday 00:00 (the opening-hours clock)
    budget_min: int                    # window length
    end_abs: int                       # window end as minutes from 00:00 of the departure day
    next_day: bool                     # the window ends after midnight
    start_resolved: Optional[tuple[float, float]]   # (lat, lon); None for shape "free"
    area: tuple[float, float]          # centre of the search area (the start, else the city centre)
    center: tuple[float, float]        # the city centre (mean of the catalog's coordinates)
    search_km: float                   # the radius actually searched (<= radius_km; reach-limited)
    window_start: datetime             # city-local, naive
    catalog_version: str
    has_hours: bool = True             # the catalog has opening hours (the clock is checked)
    dwell_total: float = 0.0           # Σ slot base visit lengths (radius sizing)
    reach_km: float = 0.0
    plan_catalog_version: Optional[str] = None      # catalog version the plan was built on (edits)

    def __getattr__(self, name: str) -> Any:
        params = self.__dict__.get("params")
        if params is not None and name in getattr(type(params), "__dataclass_fields__", {}):
            return getattr(params, name)
        raise AttributeError(name)

    @property
    def slot_codes(self) -> list[str]:
        return self.params.slot_codes

    @property
    def start_label(self) -> str:
        """Departure 'HH:MM'."""
        return f"{self.params.start_time:%H:%M}"

    @property
    def end_label(self) -> str:
        """Window end label ('14:00', '00:00 (+1 день)')."""
        return end_label(self.end_abs)

    @property
    def window_end(self) -> datetime:
        return self.window_start + timedelta(minutes=self.budget_min)

    def clock(self, minutes: float) -> str:
        """Clock label of `minutes` after the departure ('13:45', '00:30 (+1)')."""
        return clock_label(self.params.start_time, minutes)


@dataclass
class EditStop:
    """One stop of an editable variant (the API ``sequence`` item)."""

    place_id: str
    kind: str = "pinned"                      # slot | on_the_way | pinned
    slot_index: Optional[int] = None
    activity: Optional[str] = None
    dwell_min: Optional[float] = None
    dwell_fixed: bool = False

    @classmethod
    def from_any(cls, x: Any, i: int = 0) -> "EditStop":
        """An EditStop from an EditStop or an API dict, with the JSON types checked: place_id a
        non-empty string (never a JSON number — see `_parse_ids`), kind / activity a string or null,
        slot_index an integer or null, dwell_min a finite number, dwell_fixed a boolean or null.
        Values (kinds, ranges, places) are checked by `sequence_candidates`."""
        if isinstance(x, EditStop):
            return x
        if not isinstance(x, dict):
            raise _invalid(f"sequence[{i}]", "must be an object")
        pid = x.get("place_id")
        if not isinstance(pid, str) or not pid.strip():
            raise _invalid(f"sequence[{i}].place_id", _ID_REASON)
        kind = x.get("kind")
        if kind is not None and not isinstance(kind, str):
            raise _invalid(f"sequence[{i}].kind", "expected one of " + ", ".join(STOP_KINDS))
        si = x.get("slot_index")
        if si is not None and (isinstance(si, bool) or not isinstance(si, int)):
            raise _invalid(f"sequence[{i}].slot_index", "must be an integer or null")
        act = x.get("activity")
        if act is not None and not isinstance(act, str):
            raise _invalid(f"sequence[{i}].activity", "must be an activity code or null")
        dw = x.get("dwell_min")
        dwell = None if dw is None else _finite(dw, f"sequence[{i}].dwell_min")
        fixed = x.get("dwell_fixed")
        return cls(place_id=pid.strip(), kind=kind or "pinned", slot_index=si, activity=act, dwell_min=dwell,
                   dwell_fixed=False if fixed is None else _parse_bool(fixed, f"sequence[{i}].dwell_fixed"))

    def to_dict(self) -> dict:
        return {"place_id": self.place_id, "kind": self.kind, "slot_index": self.slot_index,
                "activity": self.activity, "dwell_min": self.dwell_min, "dwell_fixed": bool(self.dwell_fixed)}


@dataclass
class PlanInterest:
    """The interest map one build used (place_id -> interest in [0, 1]) and how it was made: the
    fields of ``walk_planner.interest.InterestResult`` (the taste model's own result; its ``version``
    is in the response's ``versions``) plus ``error``. A plan-level record, so the cold-start path
    never calls the taste model and a failure of it never fails a plan.

    mode "popularity" = cold start (no usable favourites); "favourites" = the taste blend
    (used / ignored seed ids, number of taste profiles, per-place taste percentile and the most
    similar seed). `error` = why personalization was unavailable (then the map is the cold start)."""

    map: dict
    mode: str = "popularity"
    used: list = field(default_factory=list)
    ignored: list = field(default_factory=list)
    profiles: int = 0
    strength: float = 0.0
    taste_pct: Optional[dict] = None
    similar_to: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class VariantState:
    """One route variant: the plan, the candidate sequence behind it (the editable state), the
    slots the solver left out (build only), whether the user edited it, the activity of each
    on-the-way stop and each stop's original (un-penalised) interest."""

    index: int
    plan: WalkPlan
    sequence: list[Candidate]
    dropped_slot_indices: list[int] = field(default_factory=list)
    edited: bool = False
    extra_activity: dict = field(default_factory=dict)
    interest: dict = field(default_factory=dict)


@dataclass
class PlanResult:
    """Outcome of `build_plan`. status: "ok" | "no_candidates" | "no_route"."""

    params: PlanParams
    context: PlanContext
    status: str
    messages: list[Message]
    variants: list[VariantState]
    extra_activity: dict
    interest: PlanInterest
    slot_candidates: list[Candidate]
    extra_candidates: list[Candidate]
    extra_candidate_activities: list[str] = field(default_factory=list)
    must_candidates: list[Candidate] = field(default_factory=list)
    request: Optional[WalkRequest] = None
    routing_version: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# --------------------------------------------------------------------------- #
# Request validation / normalisation
# --------------------------------------------------------------------------- #
# Strict formats, ASCII digits only (``\d`` would also take e.g. Arabic-Indic digits). The date is
# matched BEFORE ``date.fromisoformat``: Python 3.11+ also accepts "20261003", "2026-W40-6" ...,
# which 3.10 rejects — what the API accepts must not depend on the interpreter.
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIME_RE = re.compile(r"([0-9]{2}):([0-9]{2})")
_ID_REASON = "place ids are non-empty strings (a JSON number cannot carry a CID: it exceeds 2^53)"


def _parse_date(v: Any, field_: str) -> _date:
    """A calendar date: a ``date`` / ``datetime`` (Python callers) or the string "YYYY-MM-DD"
    (surrounding whitespace ignored; an impossible date such as 2026-02-30 is refused)."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, _date):
        return v
    if isinstance(v, str):
        s = v.strip()
        if _DATE_RE.fullmatch(s):
            try:
                return _date.fromisoformat(s)
            except ValueError:
                pass
    raise _invalid(field_, "expected a date YYYY-MM-DD", value=_shown(v))


def _parse_time(v: Any, field_: str) -> dtime:
    """A wall-clock time: a ``time`` (Python callers; seconds dropped) or the string "HH:MM" — two
    digits each, 00:00..23:59 (surrounding whitespace ignored)."""
    if isinstance(v, dtime):
        return dtime(v.hour, v.minute)
    if isinstance(v, str):
        m = _TIME_RE.fullmatch(v.strip())
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            return dtime(int(m.group(1)), int(m.group(2)))
    raise _invalid(field_, "expected a time HH:MM", value=_shown(v))


def _parse_bool(v: Any, field_: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    raise _invalid(field_, "expected true or false")


def _finite(v: Any, field_: str) -> float:
    """A finite number as float. Refused (validation_error, never an exception of another type):
    booleans, strings, NaN / ±Infinity (Python's json accepts ``NaN`` / ``Infinity``) and integers
    beyond the float range."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise _invalid(field_, "expected a number")
    try:
        x = float(v)
    except OverflowError:                          # an integer of 309+ digits
        x = math.inf
    if not math.isfinite(x):
        raise _invalid(field_, "expected a finite number")
    return x


def _parse_int(v: Any, field_: str, lo: int, hi: int) -> int:
    """An integer in [lo, hi]: a JSON integer or an integral float (120.0). Booleans, strings,
    fractions and NaN / ±Infinity are refused."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise _invalid(field_, "expected an integer")
    if isinstance(v, float):
        if not math.isfinite(v) or not v.is_integer():
            raise _invalid(field_, "expected an integer")
        v = int(v)
    if not lo <= v <= hi:
        raise _invalid(field_, f"must be between {lo} and {hi}", value=v)
    return int(v)


def _parse_float(v: Any, field_: str, lo: float, hi: float) -> float:
    """A finite number in [lo, hi] (see `_finite`)."""
    x = _finite(v, field_)
    if not lo <= x <= hi:
        raise _invalid(field_, f"must be between {lo:g} and {hi:g}", value=x)
    return x


def _parse_enum(v: Any, allowed: dict, by_label_ru: dict, field_: str) -> str:
    """A code of `allowed` (or its dashboard RU label). Any other value — another JSON type
    included — is a validation_error."""
    code = by_label_ru.get(v, v) if isinstance(v, str) else None
    if code not in allowed:
        raise _invalid(field_, "expected one of " + ", ".join(allowed), value=_shown(v))
    return code


def _parse_ids(v: Any, field_: str) -> list[str]:
    """A list of place ids, each a non-empty string (the Google CID in decimal), de-duplicated
    (order kept). JSON numbers are refused with validation_error: a CID exceeds 2^53 (and int64),
    so a number has already lost its precision in the client (JavaScript doubles) before it
    reaches the server — it would silently name another place, or none."""
    if v is None:
        return []
    if not isinstance(v, (list, tuple)):
        raise _invalid(field_, "expected a list of place ids")
    out: dict[str, None] = {}
    for i, x in enumerate(v):
        if not isinstance(x, str) or not x.strip():
            raise _invalid(f"{field_}[{i}]", _ID_REASON)
        out.setdefault(x.strip(), None)
    return list(out)


def _get(raw: dict, *names: str, default: Any = None) -> Any:
    for n in names:
        if n in raw and raw[n] is not None:
            return raw[n]
    return default


def _parse_slots(v: Any) -> list[SlotSpec]:
    if v is None:
        v = list(DEFAULTS["slots"])
    if not isinstance(v, (list, tuple)):
        raise _invalid("slots", "expected a list of slots")
    if len(v) > LIMITS["max_slots"]:
        raise _invalid("slots", f"at most {LIMITS['max_slots']} slots", value=len(v))
    out: list[SlotSpec] = []
    seen: set[str] = set()
    lo, hi = LIMITS["dwell_min"]
    for i, item in enumerate(v):
        if isinstance(item, str):
            act, dwell = item, None
        elif isinstance(item, dict):
            act, dwell = item.get("activity"), item.get("dwell_min")
        else:
            raise _invalid(f"slots[{i}]", "expected an activity code or {activity, dwell_min}")
        if not isinstance(act, str):                   # a missing activity / another JSON type
            raise _invalid(f"slots[{i}].activity", "expected an activity code")
        code = ACTIVITY_BY_LABEL_RU.get(act, act)
        if code not in ACTIVITY_TYPES:
            raise PlannerInputError("unknown_activity", {"activity": act, "slot_index": i,
                                                         "allowed": list(ACTIVITY_TYPES)})
        if code in seen:
            raise PlannerInputError("duplicate_activity", {"activity": code, "slot_index": i})
        seen.add(code)
        out.append(SlotSpec(code, None if dwell is None else _parse_int(dwell, f"slots[{i}].dwell_min", lo, hi)))
    return out


def _parse_start(raw: dict, shape: str, catalog: CityCatalog):
    """(start, start_place_id): "city_center" | (lat, lon) | None (shape "free" forces None)."""
    v = raw.get("start")
    if shape == "free":
        return None, None
    if v is None:
        raise PlannerInputError("start_required", {"shape": shape})
    if isinstance(v, str):
        if v == "city_center":
            return "city_center", None
        raise _invalid("start", 'expected "city_center", {lat, lon} or {place_id}')
    if isinstance(v, (list, tuple)) and len(v) == 2:
        v = {"lat": v[0], "lon": v[1]}
    if not isinstance(v, dict):
        raise _invalid("start", 'expected "city_center", {lat, lon} or {place_id}')
    pid = v.get("place_id")
    if pid is not None and not isinstance(pid, str):
        raise _invalid("start.place_id", _ID_REASON)
    pid = (pid or "").strip() or None
    if v.get("lat") is not None or v.get("lon") is not None:
        lat = _parse_float(v.get("lat"), "start.lat", -90.0, 90.0)
        lon = _parse_float(v.get("lon"), "start.lon", -180.0, 180.0)
        return (lat, lon), pid
    if pid is None:
        raise _invalid("start", "needs lat/lon or a place_id")
    if not catalog.has(pid):
        raise PlannerInputError("unknown_place", {"place_id": pid, "field": "start.place_id"})
    return catalog.coord_of(pid), pid


def normalize_params(raw: dict, catalog: CityCatalog) -> PlanParams:
    """Validate a plan request (API JSON as a dict, or the request echo of an earlier plan) and apply
    the page's defaults. Raises PlannerInputError (validation_error, unknown_city, invalid_window,
    start_required, unknown_activity, duplicate_activity, no_slots_or_must_visits,
    too_many_must_visits, unknown_place).

    Fields: city, date ("YYYY-MM-DD", city-local), start_time ("HH:MM", default 10:00), end_time +
    end_day_offset (0|1; default: 4 h after the start; without an offset an end at or before the
    start means the next day), shape (loop | one_way | free), start ("city_center" |
    {lat, lon[, place_id]} | {place_id}; required unless shape "free", which forces none), style,
    slots ([{activity, dwell_min}] or codes; ordered, unique, <= 8; default sight/coffee/park/food),
    must_visit_place_ids (<= 10), favourite_place_ids, want_to_go_place_ids (<= 500 together),
    personalization_strength (0..1), variants (1..5), radius_km (0.3..50), fill_window,
    known_hours_only, top_k (bench). Place ids are strings (JSON numbers are refused: a CID
    exceeds 2^53); numbers must be finite; a value of the wrong JSON type is a validation_error
    (never another exception). Unknown keys are ignored (e.g. the echo's derived fields)."""
    if not isinstance(raw, dict):
        raise _invalid("body", "expected an object")
    # city (a catalog without a city column accepts any name)
    city = _get(raw, "city", default=catalog.city)
    if city is not None and not isinstance(city, str):
        raise _invalid("city", "expected a city name", value=_shown(city))
    if catalog.city is not None:
        if city is None or city.casefold() != str(catalog.city).casefold():
            raise PlannerInputError("unknown_city", {"city": city, "available": [catalog.city]})
        city = catalog.city
    city = city or ""
    # window
    if raw.get("date") is None:
        raise _invalid("date", "required")
    day = _parse_date(raw.get("date"), "date")
    t_start = _parse_time(_get(raw, "start_time", default=DEFAULTS["start_time"]), "start_time")
    start_min = t_start.hour * 60 + t_start.minute
    if raw.get("end_time") is None:
        end_abs = start_min + min(DEFAULTS["window_min"], 1440)
        t_end = dtime((end_abs % 1440) // 60, end_abs % 60)
        offset = 1 if end_abs >= 1440 else 0
    else:
        t_end = _parse_time(raw.get("end_time"), "end_time")
        end_min = t_end.hour * 60 + t_end.minute
        if raw.get("end_day_offset") is None:
            offset = 1 if end_min <= start_min else 0
        else:
            offset = _parse_int(raw.get("end_day_offset"), "end_day_offset", 0, 1)
    window = offset * 1440 + t_end.hour * 60 + t_end.minute - start_min
    lo, hi = LIMITS["window_min"]
    if not lo <= window <= hi:
        raise PlannerInputError("invalid_window", {"window_min": window, "min": lo, "max": hi})
    # shape / style / start
    shape = _parse_enum(_get(raw, "shape", default=DEFAULTS["shape"]), SHAPES, SHAPE_BY_LABEL_RU, "shape")
    style = _parse_enum(_get(raw, "style", default=DEFAULTS["style"]), STYLES, STYLE_BY_LABEL_RU, "style")
    start, start_pid = _parse_start(raw, shape, catalog)
    # slots, places
    slots = _parse_slots(raw.get("slots"))
    musts = _parse_ids(raw.get("must_visit_place_ids"), "must_visit_place_ids")
    if len(musts) > LIMITS["max_must_visits"]:
        raise PlannerInputError("too_many_must_visits", {"count": len(musts), "max": LIMITS["max_must_visits"]})
    favs = _parse_ids(_get(raw, "favourite_place_ids", "favorite_place_ids"), "favourite_place_ids")
    wtg = _parse_ids(raw.get("want_to_go_place_ids"), "want_to_go_place_ids")
    if len(favs) + len(wtg) > LIMITS["max_personal_ids"]:
        raise _invalid("favourite_place_ids", f"at most {LIMITS['max_personal_ids']} favourite + want-to-go ids",
                       value=len(favs) + len(wtg))
    strength = _parse_float(_get(raw, "personalization_strength", default=DEFAULTS["personalization_strength"]),
                            "personalization_strength", *LIMITS["personalization_strength"])
    # numbers / flags
    variants = _parse_int(_get(raw, "variants", default=DEFAULTS["variants"]), "variants", *LIMITS["variants"])
    radius = _parse_float(_get(raw, "radius_km", default=DEFAULTS["radius_km"]), "radius_km", *LIMITS["radius_km"])
    fill = _parse_bool(_get(raw, "fill_window", default=DEFAULTS["fill_window"]), "fill_window")
    known = _parse_bool(_get(raw, "known_hours_only", default=DEFAULTS["known_hours_only"]), "known_hours_only")
    top_k = _parse_int(_get(raw, "top_k", default=DEFAULTS["top_k"]), "top_k", *LIMITS["top_k"])
    if not slots and not musts:
        raise PlannerInputError("no_slots_or_must_visits", {})
    return PlanParams(city=city, date=day, start_time=t_start, end_time=t_end, end_day_offset=offset, shape=shape,
                      start=start, start_place_id=start_pid, style=style, slots=slots, must_visit_place_ids=musts,
                      favourite_place_ids=favs, want_to_go_place_ids=wtg, personalization_strength=strength,
                      variants=variants, radius_km=radius, fill_window=fill, known_hours_only=known, top_k=top_k)


def make_context(params: PlanParams, catalog: CityCatalog) -> PlanContext:
    """Resolve validated params against the catalog: the clock (t0 = weekday·1440 + departure;
    page L4517-4521), the start (city centre / place / coordinates; none for "free"), the search
    area and the reach-limited search radius (`_walk_build` L4639-4643)."""
    window = params.window_min
    start_min = params.start_min
    end_abs = start_min + window
    center = catalog.center
    if params.shape == "free" or params.start is None:
        start = None
    elif params.start == "city_center":
        start = center
    else:
        start = (float(params.start[0]), float(params.start[1]))
    area = start if start is not None else center
    # radius sizing: type base visit lengths of the slots (not per place, not style-scaled)
    dwell_total = sum(s.dwell_min or dwell_for(ACTIVITY_TYPES[s.activity].dwell_key) for s in params.slots)
    reach_km = reach_radius_km(window, dwell_total, shape=params.shape)
    search_km = min(float(params.radius_km), reach_km)
    return PlanContext(params=params, timezone=catalog.timezone, weekday=params.date.weekday(),
                       t0=params.date.weekday() * 1440 + start_min, budget_min=window, end_abs=end_abs,
                       next_day=end_abs >= 24 * 60, start_resolved=start, area=area, center=center,
                       search_km=search_km, window_start=datetime.combine(params.date, params.start_time),
                       catalog_version=catalog.version, has_hours=catalog.has_hours, dwell_total=dwell_total,
                       reach_km=reach_km)


def to_request_echo(context: PlanContext) -> dict:
    """The normalised request as API JSON: every PlanParams field (start resolved to
    {lat, lon, place_id} | null) + timezone, weekday, window_min, window_start, window_end,
    search_radius_km and catalog_version. Sent back by the client with /schedule and /insert."""
    p = context.params
    start = None
    if context.start_resolved is not None:
        start = {"lat": float(context.start_resolved[0]), "lon": float(context.start_resolved[1]),
                 "place_id": p.start_place_id}
    return {
        "city": p.city,
        "date": p.date.isoformat(),
        "start_time": f"{p.start_time:%H:%M}",
        "end_time": f"{p.end_time:%H:%M}",
        "end_day_offset": p.end_day_offset,
        "shape": p.shape,
        "start": start,
        "style": p.style,
        "slots": [{"activity": s.activity, "dwell_min": s.dwell_min} for s in p.slots],
        "must_visit_place_ids": list(p.must_visit_place_ids),
        "favourite_place_ids": list(p.favourite_place_ids),
        "want_to_go_place_ids": list(p.want_to_go_place_ids),
        "personalization_strength": float(p.personalization_strength),
        "variants": p.variants,
        "radius_km": float(p.radius_km),
        "fill_window": p.fill_window,
        "known_hours_only": p.known_hours_only,
        "top_k": p.top_k,
        "timezone": context.timezone,
        "weekday": context.weekday,
        "window_min": context.budget_min,
        "window_start": context.window_start.strftime("%Y-%m-%dT%H:%M"),
        "window_end": context.window_end.strftime("%Y-%m-%dT%H:%M"),
        "search_radius_km": float(context.search_km),
        "catalog_version": context.catalog_version,
    }


def from_request_echo(echo: dict, catalog: CityCatalog) -> PlanContext:
    """PlanContext from a request echo (re-validated; derived fields recomputed). The echoed
    catalog_version is kept to tell "place missing because the catalog changed" (409) from an
    unknown place (404)."""
    if not isinstance(echo, dict):
        raise _invalid("request", "expected the request object of the plan response")
    ctx = make_context(normalize_params(echo, catalog), catalog)
    v = echo.get("catalog_version")
    ctx.plan_catalog_version = str(v) if v else None
    return ctx


# --------------------------------------------------------------------------- #
# Interest
# --------------------------------------------------------------------------- #
def _catalog_themes(rows) -> list:
    """Row-aligned catalog theme of every place (theme_group when the catalog has no theme) — the
    groups the taste blend re-orders within."""
    for col in ("theme", "theme_group"):
        if col in rows.columns:
            return list(rows[col])
    return [""] * len(rows)


def resolve_interest(params: PlanParams, catalog: CityCatalog, taste: Any = None) -> tuple[PlanInterest, list[Message]]:
    """The interest map of a build: the catalog's cold start when there are no favourites
    (``interest.cold_start`` — byte-identical to the dashboard's), else
    ``walk_planner.interest.interest_map`` over the taste artifacts `taste`
    (``walk_planner.interest.TasteArtifacts``, loaded by the caller). Without artifacts, or on any
    failure, the cold start with `personalization_unavailable` (``params.reason``:
    taste_unavailable | interest_module_unavailable | interest_error:<type>)."""
    favs, wtg = list(params.favourite_place_ids), list(params.want_to_go_place_ids)
    strength = float(params.personalization_strength)
    cold = catalog.cold_interest()
    if not favs and not wtg:
        return PlanInterest(map=cold, mode="popularity", strength=strength), []
    given = favs + [w for w in wtg if w not in set(favs)]
    reason = None
    try:
        if taste is None:
            reason = "taste_unavailable"
            raise LookupError("no taste artifacts loaded")
        try:
            from .interest import interest_map
        except ImportError:
            reason = "interest_module_unavailable"
            raise
        res = interest_map(catalog.place_ids, _catalog_themes(catalog.rows), cold, taste,
                           favourite_ids=favs, want_to_go_ids=wtg, strength=strength)
        imap = res if isinstance(res, dict) else getattr(res, "map")
        if not isinstance(imap, dict):
            raise TypeError("interest_map returned no map")
    except Exception as exc:
        reason = reason or f"interest_error:{type(exc).__name__}"
        return (PlanInterest(map=cold, mode="popularity", ignored=given, strength=strength, error=reason),
                [Message("personalization_unavailable", {"reason": reason})])
    return PlanInterest(
        map={str(k): float(v) for k, v in imap.items()},
        mode=str(getattr(res, "mode", "favourites") or "favourites"),
        used=[str(x) for x in (getattr(res, "used", None) or [])],
        ignored=[str(x) for x in (getattr(res, "ignored", None) or [])],
        profiles=int(getattr(res, "profiles", 0) or 0),
        strength=float(getattr(res, "strength", strength)),
        taste_pct=getattr(res, "taste_pct", None),
        similar_to=getattr(res, "similar_to", None),
    ), []


def _context_interest(context: PlanContext, catalog: CityCatalog, taste: Any = None) -> dict:
    """Interest map for re-hydrating an edited variant (cold start unless taste artifacts are given)."""
    if taste is None or not (context.params.favourite_place_ids or context.params.want_to_go_place_ids):
        return catalog.cold_interest()
    return resolve_interest(context.params, catalog, taste)[0].map


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def _provider(provider) -> RoutingProvider:
    return provider if provider is not None else RoutingProvider()


def _routing_version(provider) -> Optional[str]:
    v = getattr(provider, "data_version", None)
    if callable(v):
        try:
            v = v()
        except Exception:
            v = None
    return str(v) if v else None


def _variant_state(index: int, plan: WalkPlan, context: PlanContext, catalog: CityCatalog,
                   extra_label: dict, interest: dict) -> VariantState:
    """The editable state of a built variant — the dashboard's ``variant(plan)`` (L4702-4707): the
    candidates rebuilt from the plan's stops (scaled / capped dwell, hours from the catalog). Plus
    the informational dwell_fixed of user-set slot lengths, the on-the-way activities and the
    original interest of every stop."""
    n_slots = len(context.slot_codes)
    slots = context.params.slots
    seq = []
    for s in plan.stops:
        fixed = (not s.extra and 0 <= s.slot < n_slots and slots[s.slot].dwell_min is not None)
        seq.append(Candidate(place_id=s.place_id, name=s.name, lat=s.lat, lon=s.lon, slot=s.slot, theme=s.theme,
                             interest=s.interest, dwell_min=s.dwell_min,
                             open_hours=catalog.hours_of(s.place_id) if context.has_hours else None,
                             extra=s.extra, pinned=s.pinned, dwell_fixed=fixed))
    extra_activity = {s.place_id: extra_label[s.place_id] for s in plan.stops
                      if s.extra and s.place_id in extra_label}
    orig = {s.place_id: (0.0 if s.pinned and not 0 <= s.slot < n_slots else float(interest.get(s.place_id, 0.0)))
            for s in plan.stops}
    return VariantState(index=index, plan=plan, sequence=seq, dropped_slot_indices=list(plan.dropped_slots),
                        edited=False, extra_activity=extra_activity, interest=orig)


def build_plan(params: PlanParams, catalog: CityCatalog, provider: Optional[RoutingProvider] = None,
               taste: Any = None) -> PlanResult:
    """Pick candidates and plan up to `params.variants` different routes (`_walk_build`).

    Steps: shrink the search radius to what the window can reach (message radius_shrunk); the
    interest map (cold start, or the favourites' taste blend); slot candidates per slot (message
    slots_no_candidates for empty ones); must-visit places through the business-status policy
    (closed_forever left out with must_visit_closed_forever; unknown ids -> unknown_place_ids);
    nothing at all -> status "no_candidates"; on-the-way candidates; ``core.plan_variants``
    (nothing -> "no_route"; fewer -> fewer_variants). `provider` routes the final legs (default:
    the straight-line estimate); the solver always uses the estimate."""
    context = make_context(params, catalog)
    provider = _provider(provider)
    rows = catalog.rows
    slot_codes = context.slot_codes
    messages: list[Message] = []
    if context.search_km < float(params.radius_km) - 1e-9:
        messages.append(Message("radius_shrunk", {
            "radius_km": float(params.radius_km), "search_radius_km": float(context.search_km),
            "reason": "overpacked" if context.dwell_total >= context.budget_min else "reach",
            "dwell_total_min": context.dwell_total, "window_min": context.budget_min,
            "window_start_label": context.start_label, "window_end_label": context.end_label,
            "anchor": "start" if context.start_resolved is not None else "center",
            "loop": params.shape == "loop",
        }))
    interest, imsgs = resolve_interest(params, catalog, taste)
    messages.extend(imsgs)
    imap = interest.map

    window = (context.t0, context.t0 + context.budget_min)
    text = catalog.text_series()
    dist = distances_from(rows, context.area)
    all_cands: list[Candidate] = []
    missing: list[int] = []
    for idx, spec in enumerate(params.slots):
        cs = slot_candidates(rows, imap, idx, spec.activity, context.area, context.search_km, top_k=params.top_k,
                             window=window, known_hours_only=params.known_hours_only, dwell_override=spec.dwell_min,
                             text=text, dist=dist)
        if cs:
            all_cands.extend(cs)
        else:
            missing.append(idx)
    if missing:
        messages.append(Message("slots_no_candidates", {"activities": [slot_codes[i] for i in missing],
                                                        "slot_indices": missing}))

    musts: list[Candidate] = []
    unknown: list[str] = []
    closed = []
    for pid in params.must_visit_place_ids:
        pc = place_candidate(catalog, pid, allow_temporarily_closed=True)
        if pc.accepted:
            musts.append(pc.candidate)
        elif pc.reason == "unknown_place":
            unknown.append(pc.place_id)
        else:
            closed.append(pc)
    if unknown:
        messages.append(Message("unknown_place_ids", {"place_ids": unknown, "field": "must_visit_place_ids"}))
    if closed:
        messages.append(Message("must_visit_closed_forever", {"place_ids": [c.place_id for c in closed],
                                                              "names": [c.name for c in closed]}))

    def result(status: str, variants=(), extras=None, req=None) -> PlanResult:
        return PlanResult(params=params, context=context, status=status, messages=messages, variants=list(variants),
                          extra_activity=dict(extras.activity_of) if extras else {}, interest=interest,
                          slot_candidates=all_cands, extra_candidates=list(extras.candidates) if extras else [],
                          extra_candidate_activities=list(extras.activities) if extras else [],
                          must_candidates=musts, request=req, routing_version=_routing_version(provider))

    if not all_cands and not musts:
        messages.append(Message("no_candidates", {}))
        return result("no_candidates")

    # Optional stops the planner may add to USE a roomy window: more of the non-food types the user
    # asked for (no extra meals); "scenic" adds parks & squares instead.
    extras = extra_candidates(rows, imap, slot_codes, params.style, params.fill_window, context.area,
                              context.search_km, window, params.known_hours_only, text=text, dist=dist)
    req = WalkRequest(candidates=all_cands + extras.candidates, mode="slots", n_slots=len(slot_codes),
                      start=context.start_resolved, shape=params.shape, style=params.style,
                      time_budget_min=float(context.budget_min), provider=provider,
                      start_week_min=float(context.t0) if context.has_hours else None,
                      fill_window=params.fill_window, must_visit=musts)
    plans = plan_variants(req, params.variants)
    if not plans:
        messages.append(Message("no_route", {}))
        return result("no_route", extras=extras, req=req)
    if len(plans) < params.variants:
        messages.append(Message("fewer_variants", {"built": len(plans), "requested": params.variants}))
    states = [_variant_state(i, p, context, catalog, extras.activity_of, imap) for i, p in enumerate(plans)]
    return result("ok", states, extras=extras, req=req)


# --------------------------------------------------------------------------- #
# Editing (stateless)
# --------------------------------------------------------------------------- #
def sequence_request(context: PlanContext, seq: list[Candidate], provider: Optional[RoutingProvider] = None) -> WalkRequest:
    """The WalkRequest an edited order is scheduled / inserted with (`_walk_seq_request`)."""
    return WalkRequest(candidates=seq, mode="slots", start=context.start_resolved, shape=context.shape,
                       style=context.style, time_budget_min=float(context.budget_min), provider=_provider(provider),
                       start_week_min=float(context.t0) if context.has_hours else None)


def edit_stops(context: PlanContext, state: VariantState) -> list[EditStop]:
    """A variant as its editable sequence (kind, slot_index, activity, dwell as scheduled)."""
    n_slots = len(context.slot_codes)
    by_id = {c.place_id: c for c in state.sequence}
    out = []
    for s in state.plan.stops:
        c = by_id.get(s.place_id)
        out.append(EditStop(place_id=s.place_id, kind=stop_kind(s), slot_index=stop_slot_index(s, n_slots),
                            activity=stop_activity(s, context.slot_codes, state.extra_activity),
                            dwell_min=float(c.dwell_min if c is not None else s.dwell_min),
                            dwell_fixed=bool(c.dwell_fixed) if c is not None else False))
    return out


def _missing_place(context: PlanContext, catalog: CityCatalog, pid: str, field_: str) -> PlannerInputError:
    if context.plan_catalog_version and context.plan_catalog_version != catalog.version:
        return PlannerInputError("catalog_changed", {"place_id": pid, "catalog_version": catalog.version,
                                                     "plan_catalog_version": context.plan_catalog_version})
    return PlannerInputError("unknown_place", {"place_id": pid, "field": field_})


def sequence_candidates(context: PlanContext, sequence: list, catalog: CityCatalog,
                        interest: Optional[dict] = None) -> tuple[list[Candidate], list[EditStop]]:
    """Re-hydrate an edited sequence into the candidates the dashboard kept for it: name,
    coordinates and hours from the catalog; slot / theme / interest from the stop's kind (slot ->
    the slot's type; on_the_way -> its pool's type, slot = that type's index among the requested
    slots or -1; pinned without a slot -> the place's own theme and interest 0); dwell as sent.
    Refuses unknown places (404, or 409 catalog_changed), closed_forever places (422), duplicates,
    bad kinds / slot indices / activities and dwell outside 5..480 (422)."""
    if not isinstance(sequence, (list, tuple)):
        raise _invalid("sequence", "expected a list of stops")
    if len(sequence) > LIMITS["max_edit_stops"]:
        raise _invalid("sequence", f"at most {LIMITS['max_edit_stops']} stops", value=len(sequence))
    items = [EditStop.from_any(x, i) for i, x in enumerate(sequence)]
    imap = interest if interest is not None else catalog.cold_interest()
    slots = context.params.slots
    codes = context.slot_codes
    lo, hi = LIMITS["dwell_min"]
    seen: set[str] = set()
    out: list[Candidate] = []
    for i, e in enumerate(items):
        if e.place_id in seen:
            raise _invalid(f"sequence[{i}].place_id", "duplicate place in the route", place_id=e.place_id)
        seen.add(e.place_id)
        if not catalog.has(e.place_id):
            raise _missing_place(context, catalog, e.place_id, f"sequence[{i}].place_id")
        if catalog.status_of(e.place_id) == "closed_forever":
            raise PlannerInputError("place_closed_forever", {"place_id": e.place_id, "name": catalog.name_of(e.place_id)})
        if e.kind not in STOP_KINDS:
            raise _invalid(f"sequence[{i}].kind", "expected one of " + ", ".join(STOP_KINDS))
        if e.dwell_min is None or not math.isfinite(e.dwell_min) or not lo <= e.dwell_min <= hi:
            raise _invalid(f"sequence[{i}].dwell_min", f"must be between {lo} and {hi}")
        if e.slot_index is not None and not 0 <= e.slot_index < len(slots):
            raise _invalid(f"sequence[{i}].slot_index", f"must be between 0 and {len(slots) - 1}")
        row = catalog.row(e.place_id)
        if e.kind == "slot" or (e.kind == "pinned" and e.slot_index is not None):
            if e.slot_index is None:
                raise _invalid(f"sequence[{i}].slot_index", "required for a slot stop")
            slot, theme = e.slot_index, ACTIVITY_TYPES[codes[e.slot_index]].dwell_key
            name = str(row.get("name", row["place_id"]))
            interest_v = float(imap.get(e.place_id, 0.0))
        elif e.kind == "on_the_way":
            if e.activity not in ACTIVITY_TYPES:
                raise _invalid(f"sequence[{i}].activity", "an on-the-way stop needs its activity code")
            slot = codes.index(e.activity) if e.activity in codes else -1
            theme = ACTIVITY_TYPES[e.activity].dwell_key
            name = str(row.get("name", row["place_id"]))
            interest_v = float(imap.get(e.place_id, 0.0))
        else:                                          # pinned, no slot: the user's own place
            slot, theme = -1, str(row.get("theme") or row.get("theme_group") or "")
            name = str(row.get("name") or row["place_id"])
            interest_v = 0.0
        out.append(Candidate(place_id=e.place_id, name=name, lat=float(row["latitude"]), lon=float(row["longitude"]),
                             slot=slot, theme=theme, interest=interest_v, dwell_min=float(e.dwell_min),
                             open_hours=catalog.hours_of(e.place_id) if context.has_hours else None,
                             extra=e.kind == "on_the_way", pinned=e.kind == "pinned", dwell_fixed=bool(e.dwell_fixed)))
    return out, items


def _edited_state(index: int, plan: WalkPlan, seq: list[Candidate], items: list[EditStop]) -> VariantState:
    return VariantState(index=index, plan=plan, sequence=list(seq), dropped_slot_indices=[], edited=True,
                        extra_activity={e.place_id: e.activity for e in items if e.kind == "on_the_way" and e.activity},
                        interest={c.place_id: float(c.interest) for c in seq})


def schedule(context: PlanContext, sequence: list, catalog: CityCatalog, provider: Optional[RoutingProvider] = None,
             *, index: int = 0, taste: Any = None) -> VariantState:
    """Schedule and route the stops EXACTLY in the given order (reorder / remove / restore / change
    minutes in the client). Nothing is dropped or swapped: a stop that may be closed at its new time
    is kept and flagged, and over_budget says when the order overruns the window (`plan_sequence`)."""
    provider = _provider(provider)
    seq, items = sequence_candidates(context, sequence, catalog, _context_interest(context, catalog, taste))
    plan = plan_sequence(seq, sequence_request(context, seq, provider))
    return _edited_state(index, plan, seq, items)


def insert_place(context: PlanContext, sequence: list, place_id, catalog: CityCatalog,
                 provider: Optional[RoutingProvider] = None, allow_temporarily_closed: bool = False, *,
                 index: int = 0, dwell_min: Optional[float] = None, taste: Any = None) -> tuple[VariantState, int]:
    """Add a catalog place where it costs the least time (`best_insertion`: every stop open on arrival
    and no leg over 45 min if possible, else the shortest position) and re-time the route
    (`_walk_add_place`). The new stop is the user's own place (pinned, dwell estimated from the
    place unless `dwell_min`). Errors: validation_error (`place_id` not a non-empty string — never
    a number, see `_parse_ids`), unknown_place (404), place_already_in_route (409),
    place_closed_forever (422), place_temporarily_closed (422 unless `allow_temporarily_closed`).
    Returns the new state and the inserted stop's index."""
    if not isinstance(place_id, str) or not place_id.strip():
        raise _invalid("place_id", _ID_REASON)
    provider = _provider(provider)
    seq, items = sequence_candidates(context, sequence, catalog, _context_interest(context, catalog, taste))
    pid = place_id.strip()
    if not catalog.has(pid):
        raise PlannerInputError("unknown_place", {"place_id": pid, "field": "place_id"})
    if pid in {c.place_id for c in seq}:
        raise PlannerInputError("place_already_in_route", {"place_id": pid})
    pc = place_candidate(catalog, pid, allow_temporarily_closed=allow_temporarily_closed)
    if not pc.accepted:
        raise PlannerInputError(pc.reason or "unknown_place", {"place_id": pid, "name": pc.name})
    cand = pc.candidate
    if dwell_min is not None:
        lo, hi = LIMITS["dwell_min"]
        cand = replace(cand, dwell_min=_parse_float(dwell_min, "dwell_min", lo, hi), dwell_fixed=True)
    new_seq = best_insertion(seq, cand, sequence_request(context, seq, provider))
    pos = next(i for i, c in enumerate(new_seq) if c is cand)
    plan = plan_sequence(new_seq, sequence_request(context, new_seq, provider))
    items = items[:pos] + [EditStop(place_id=pid, kind="pinned", dwell_min=cand.dwell_min,
                                    dwell_fixed=cand.dwell_fixed)] + items[pos:]
    return _edited_state(index, plan, new_seq, items), pos
