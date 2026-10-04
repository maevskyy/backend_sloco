"""Presentation of Walk Planner results: the API views (authoritative JSON) and the text formatters.

Three layers, all Streamlit-free:

  * formatters — exact ports of the dashboard's helpers: ``end_label`` (`_walk_end_label`),
    ``clock_label`` (`_walk_clock`), ``hours_label_ru`` (`_walk_hours_label`), plus the structured
    ``hours_on_day`` and the API ``time_point``;
  * API views — ``plan_response`` / ``variant_view`` / ``edit_response`` / place cards, shaped per the
    v1 contract (TimePoints, segments with from/to kinds, navigation legs by segment index, bbox,
    the editable ``sequence``, routing quality, messages with code + params + text);
  * dashboard render helpers — the Walk Planner page's metrics, captions, warnings and stop cards in
    Russian (``render_variant_ru``, ``metrics_ru``, ``stop_card_lines_ru`` ...), reproducing the page's
    strings exactly, so the page can be switched onto the package without any visible change.

Derivation rules (understand/features.md §3.3): stop kind precedence pinned > on_the_way > slot;
slot_index is null for on-the-way stops (their core slot is only the pool they came from) and for
pinned stops without a slot; hours are evaluated at the visit start for the card and at arrival for
the conflict warning (as the page does); times are minutes from the window start, the local clock
uses Python ``round`` like `_walk_clock`.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date as _date, datetime, time as dtime, timedelta
from typing import TYPE_CHECKING, Iterable, Optional

from .core import WEEK_MIN, navigation_links, visit_wait
from .messages import Message, render
from .routing import polyline6_encode
from .slots import DAYS_RU, DAYS_RU_FULL, LABEL_RU_BY_ACTIVITY
from .version import ALGORITHM_VERSION, API_VERSION, INTEREST_VERSION

if TYPE_CHECKING:  # pragma: no cover
    from .catalog import CityCatalog
    from .pipeline import PlanContext, PlanResult, VariantState

__all__ = [
    "START_LABEL", "KIND_PINNED_RU", "ON_THE_WAY_SUFFIX_RU", "EDITED_SUFFIX_RU", "STOP_CLOSED_LINE_RU",
    "TEMP_CLOSED_LINE_RU", "STOPS_HELP_RU",
    "end_label", "clock_label", "hours_label_ru", "hours_on_day", "time_point", "window_caption_ru", "places_word_ru",
    "stop_kind", "stop_slot_index", "stop_activity", "hours_status", "routing_quality", "variant_messages",
    "segment_views", "navigation_ru", "navigation_view", "bbox_view", "summary_view", "stop_views", "sequence_view",
    "variant_view", "place_cards", "versions_view", "plan_id_of", "personalization_view", "debug_view",
    "plan_response", "edit_response", "error_body", "config_view",
    "kind_label_ru", "variant_summary_ru", "variants_caption_ru", "metrics_ru", "stop_card_lines_ru",
    "stop_card_markdown_ru", "render_variant_ru",
]

START_LABEL = {"ru": "Старт", "en": "Start"}
KIND_PINNED_RU = "📍 ваше место"
ON_THE_WAY_SUFFIX_RU = " · по пути, можно пройти мимо"
EDITED_SUFFIX_RU = " · ✏️ изменён"
STOP_CLOSED_LINE_RU = "⚠️ по графику в это время может быть закрыто — маршрут построен как есть"
TEMP_CLOSED_LINE_RU = "⛔ Временно закрыто по данным Google"
STOPS_HELP_RU = "Запрошенные слоты + места по пути, чтобы занять окно + 📍 ваши места."
# Codes of variant messages and where the page shows them (render_variant_ru).
_CAPTION_CODES = ("extras_added",)
_WARNING_CODES = ("slots_dropped", "over_budget", "stop_hours_conflict", "place_temporarily_closed")
_INFO_CODES = ("route_empty",)


# --------------------------------------------------------------------------- #
# Formatters (exact ports of the dashboard's)
# --------------------------------------------------------------------------- #
def end_label(end_abs) -> str:
    """'Закончить к' label; `end_abs` = minutes from 00:00 of the departure day
    (1440 = midnight -> '00:00 (+1 день)') — `_walk_end_label`."""
    m = int(end_abs) % 1440
    return f"{m // 60:02d}:{m % 60:02d}" + (" (+1 день)" if end_abs >= 1440 else "")


def clock_label(t_start: dtime, minutes: float) -> str:
    """Wall-clock label for `minutes` after the departure `t_start` ('13:45', '00:30 (+1)') —
    `_walk_clock` (Python round, i.e. half to even)."""
    base = datetime.combine(_date(2000, 1, 1), t_start)
    t = base + timedelta(minutes=round(float(minutes)))
    days = (t.date() - base.date()).days
    return t.strftime("%H:%M") + (f" (+{days})" if days else "")


def _hm(m) -> str:
    m = int(m) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _day_parts(hours, t_week):
    """(day, t, parts) — the weekday of `t_week` and, in sorted order, each raw interval (and its
    copy one week earlier) classified as ("24h"|"opens"|"carry", o, c) the way `_walk_hours_label`
    walks them."""
    t = float(t_week) % WEEK_MIN
    day = int(t // 1440)
    d0, d1 = day * 1440, day * 1440 + 1440
    parts = []
    for o, c in sorted((o + k, c + k) for o, c in hours for k in (-WEEK_MIN, 0)):
        if c - o >= 1440 and o <= d0 and c >= d1:
            parts.append(("24h", o, c))
        elif d0 <= o < d1:                          # opens this day (may close after midnight)
            parts.append(("opens", o, c))
        elif o < d0 < c and t < c:                  # last night's opening, still running at t
            parts.append(("carry", o, c))
    return day, d0, parts


def hours_label_ru(hours, t_week) -> str:
    """Opening hours on the weekday of `t_week` ('сб 10:00–18:00', 'сб круглосуточно', 'сб до 02:00'
    for last night's opening when `t_week` falls inside it) — `_walk_hours_label`."""
    if hours is None:
        return "часы работы неизвестны"
    day, _d0, parts = _day_parts(hours, t_week)
    text = []
    for kind, o, c in parts:
        if kind == "24h":
            text.append("круглосуточно")
        elif kind == "opens":
            text.append(f"{_hm(o)}–{_hm(c)}")
        else:
            text.append(f"до {_hm(c)}")
    return f"{DAYS_RU[day]} " + (", ".join(text) if text else "закрыто")


def hours_on_day(hours, t_week) -> Optional[dict]:
    """Structured opening hours on the weekday of `t_week` (the data behind `hours_label_ru`), or
    None when the hours are unknown.

    ``{"weekday": 0..6 (Mon = 0), "open_24h": bool, "closed_all_day": bool (no opening starts
    this day and not 24 h), "intervals": [{"open": "HH:MM", "close": "HH:MM",
    "close_day_offset": 0|1}] (openings that start this day), "carryover_until": "HH:MM" | null
    (last night's opening still running at `t_week`)}``."""
    if hours is None:
        return None
    day, d0, parts = _day_parts(hours, t_week)
    open_24h = any(k == "24h" for k, _o, _c in parts)
    intervals = [{"open": _hm(o), "close": _hm(c), "close_day_offset": int((c - d0) // 1440)}
                 for k, o, c in parts if k == "opens"]
    carry = [c for k, _o, c in parts if k == "carry"]
    return {"weekday": day, "open_24h": open_24h, "closed_all_day": not open_24h and not intervals,
            "intervals": intervals, "carryover_until": _hm(max(carry)) if carry else None}


def time_point(window_start: datetime, offset_min: float) -> dict:
    """API TimePoint: minutes from the window start + the city-local clock (rounded like `_walk_clock`)."""
    t = window_start + timedelta(minutes=round(float(offset_min)))
    return {"offset_min": float(offset_min), "local": t.strftime("%Y-%m-%dT%H:%M")}


def window_caption_ru(budget_min: int, weekday: int, next_day: bool) -> str:
    """The page's window caption ('Окно: 4 ч 00 мин · суббота')."""
    return (f"Окно: {budget_min // 60} ч {budget_min % 60:02d} мин · {DAYS_RU_FULL[weekday]}"
            + (" · заканчиваю на следующий день" if next_day else ""))


def places_word_ru(n: int) -> str:
    """место / места / мест for `n`."""
    return "место" if n % 10 == 1 and n % 100 != 11 else (
        "места" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "мест")


# --------------------------------------------------------------------------- #
# Stop derivations (shared by the API views, the edit sequence and the RU render)
# --------------------------------------------------------------------------- #
def stop_kind(stop) -> str:
    """"pinned" (the user's own place) > "on_the_way" (optional extra) > "slot"."""
    if stop.pinned:
        return "pinned"
    if stop.extra:
        return "on_the_way"
    return "slot"


def stop_slot_index(stop, n_slots: int) -> Optional[int]:
    """The requested slot this stop FILLS: its core slot for slot stops and for pinned stops that
    coincided with a slot pick; None for on-the-way stops and other pinned ones."""
    if stop.extra and not stop.pinned:
        return None
    return int(stop.slot) if 0 <= stop.slot < n_slots else None


def stop_activity(stop, slot_codes: list[str], extra_activity: dict) -> Optional[str]:
    """Activity code of a stop: its slot's activity; for on-the-way stops the pool it came from."""
    if stop.extra and not stop.pinned:
        act = extra_activity.get(stop.place_id)
        if act is None and 0 <= stop.slot < len(slot_codes):
            act = slot_codes[stop.slot]
        return act
    si = stop_slot_index(stop, len(slot_codes))
    return slot_codes[si] if si is not None else None


def hours_status(stop, hours, t0: Optional[float]) -> str:
    """open | open_after_wait | unknown | closes_during_visit | closed_at_arrival | not_checked.

    No departure clock -> not_checked; unknown hours -> unknown; the core's `hours_ok` True ->
    open (or open_after_wait from a 1-minute wait, the card's threshold); False -> whether the
    place is open at arrival (`visit_wait(h, t0 + arrival, 0, 0)`, the page's rule)."""
    if t0 is None:
        return "not_checked"
    if stop.hours_ok is None:
        return "unknown"
    if stop.hours_ok:
        return "open_after_wait" if stop.wait_min >= 1 else "open"
    return "closes_during_visit" if visit_wait(hours, t0 + stop.arrival_min, 0.0, 0.0) is not None else "closed_at_arrival"


def _t0(context) -> Optional[float]:
    return float(context.t0) if context.has_hours else None


def _hours(context, catalog, place_id):
    return catalog.hours_of(place_id) if context.has_hours else None


def routing_quality(plan) -> str:
    """streets | estimate | mixed | none — the plan's own flag when the core provides it."""
    q = getattr(plan, "routing_quality", None)
    if q:
        return str(q)
    if not plan.segments:
        return "none"
    kinds = {getattr(g, "quality", "estimate") for g in plan.segments}
    return kinds.pop() if len(kinds) == 1 else "mixed"


# --------------------------------------------------------------------------- #
# Variant messages (render-time rules of the page, as codes)
# --------------------------------------------------------------------------- #
def variant_messages(context: "PlanContext", state: "VariantState", catalog: "CityCatalog") -> list[Message]:
    """Messages of one variant, in the page's order: extras_added and slots_dropped (only while the
    variant is not edited), over_budget, stop_hours_conflict per stop, then the v1 additions
    place_temporarily_closed per stop and routing_estimate; route_empty for an empty route."""
    plan = state.plan
    out: list[Message] = []
    if not plan.stops:
        out.append(Message("route_empty", {}))
        return out
    t_end = context.end_label
    slot_codes = context.slot_codes
    n_extra = sum(1 for s in plan.stops if s.extra)
    if n_extra and not state.edited:
        out.append(Message("extras_added", {"count": n_extra, "style": context.style,
                                            "window_start_label": context.start_label, "window_end_label": t_end}))
    if state.dropped_slot_indices and not state.edited:
        idx = list(state.dropped_slot_indices)
        out.append(Message("slots_dropped", {"activities": [slot_codes[i] for i in idx], "slot_indices": idx,
                                             "window_end_label": t_end}))
    if plan.over_budget:
        out.append(Message("over_budget", {"total_min": plan.total_time_min, "window_min": context.budget_min,
                                           "finish_label": context.clock(plan.total_time_min),
                                           "window_end_label": t_end,
                                           "finish_at": time_point(context.window_start, plan.total_time_min)}))
    t0 = float(context.t0)
    for s in plan.stops:
        if s.hours_ok is False:
            h = _hours(context, catalog, s.place_id)
            open_on_arrival = visit_wait(h, t0 + s.arrival_min, 0.0, 0.0) is not None
            day = hours_on_day(h, t0 + s.arrival_min)
            out.append(Message("stop_hours_conflict", {
                "reason": "closes_during_visit" if open_on_arrival else "closed_at_arrival",
                "place_id": s.place_id, "name": s.name,
                "arrival_label": context.clock(s.arrival_min), "departure_label": context.clock(s.depart_min),
                "hours_label": hours_label_ru(h, t0 + s.arrival_min),
                "closed_all_day": bool(day["closed_all_day"]) if day else False, "hours_day": day,
            }, stop_index=s.order))
    for s in plan.stops:
        if catalog.status_of(s.place_id) == "temporarily_closed":
            out.append(Message("place_temporarily_closed", {"place_id": s.place_id, "name": s.name}, stop_index=s.order))
    n_est = sum(1 for g in plan.segments if getattr(g, "quality", "estimate") == "estimate")
    if plan.segments and n_est:
        out.append(Message("routing_estimate", {"segments_estimated": n_est, "segments_total": len(plan.segments)}))
    return out


# --------------------------------------------------------------------------- #
# API views
# --------------------------------------------------------------------------- #
def _polyline6(coords_lonlat: list) -> str:
    """Google encoded polyline (1e-6) of [[lon, lat], ...] — the routing module owns the codec."""
    return polyline6_encode(coords_lonlat)


def _endpoints(context, plan) -> list[tuple[str, Optional[int]]]:
    """(kind, stop_index) of every route point: the start anchor (not for "free"), the stops, the
    return to the start for a loop — the point list `_assemble_plan` routes."""
    n = len(plan.stops)
    anchor = context.start_resolved is not None and context.shape != "free"
    pts: list[tuple[str, Optional[int]]] = [("start", None)] if anchor else []
    pts += [("stop", i) for i in range(n)]
    if context.shape == "loop" and context.start_resolved is not None:
        pts.append(("start", None))
    return pts


def segment_views(context: "PlanContext", plan, geometry: str = "geojson") -> list[dict]:
    """API segments: from/to kinds and stop indices (counted over the actual point list — correct for
    shape "free" whatever the core's from_order), walk minutes, metres, depart/arrive TimePoints,
    routing quality / provider and the geometry (GeoJSON LineString, or "polyline6")."""
    if not plan.stops:
        return []
    pts = _endpoints(context, plan)
    if len(pts) - 1 != len(plan.segments):          # unexpected shape: trust the core's indices
        pts = []
        for g in plan.segments:
            if not pts:
                pts.append(("start", None) if g.from_order < 0 else ("stop", g.from_order))
            pts.append(("start", None) if g.to_order < 0 else ("stop", g.to_order))
    out = []
    for i, g in enumerate(plan.segments):
        fk, fi = pts[i]
        tk, ti = pts[i + 1]
        depart = 0.0 if fk == "start" else float(plan.stops[fi].depart_min)
        item = {
            "index": i,
            "from": {"kind": fk, "stop_index": fi},
            "to": {"kind": tk, "stop_index": ti},
            "walk_min": float(g.walk_min),
            "distance_m": int(round(float(g.distance_km) * 1000.0)),
            "depart": time_point(context.window_start, depart),
            "arrive": time_point(context.window_start, depart + float(g.walk_min)),
            "quality": getattr(g, "quality", "estimate"),
            "provider": getattr(g, "provider", "haversine"),
        }
        coords = [[float(p[0]), float(p[1])] for p in g.geometry]
        if geometry == "polyline6":
            item["geometry_polyline6"] = _polyline6(coords)
        else:
            item["geometry"] = {"type": "LineString", "coordinates": coords}
        out.append(item)
    return out


def navigation_ru(context: "PlanContext", plan, catalog: "CityCatalog", start_label: Optional[str] = None) -> dict:
    """The page's navigation block: ``core.navigation_links`` with the Google place ids of the stops
    (``ChIJ…`` only), legs named by stop names and `start_label` (default "Старт")."""
    gpid = {}
    for s in plan.stops:
        g = catalog.google_place_id(s.place_id)
        if g:
            gpid[s.place_id] = g
    return navigation_links(plan, start=context.start_resolved, shape=context.shape, place_ids=gpid,
                            start_label=START_LABEL["ru"] if start_label is None else start_label)


def navigation_view(context: "PlanContext", plan, catalog: "CityCatalog") -> dict:
    """API navigation: Google "whole route" parts + per-leg Google / Apple links keyed by segment index."""
    if not plan.stops:
        return {"google_parts": [], "legs": []}
    nav = navigation_ru(context, plan, catalog)
    n_seg = len(plan.segments)
    return {"google_parts": list(nav["google"]),
            "legs": [{"segment_index": i if i < n_seg else None, "google": leg["google"], "apple": leg["apple"]}
                     for i, leg in enumerate(nav["legs"])]}


def bbox_view(context: "PlanContext", plan) -> Optional[list[float]]:
    """[minLon, minLat, maxLon, maxLat] over the stops, the start and the route geometry."""
    lons = [s.lon for s in plan.stops]
    lats = [s.lat for s in plan.stops]
    if context.start_resolved is not None and plan.stops:
        lats.append(context.start_resolved[0])
        lons.append(context.start_resolved[1])
    for g in plan.segments if plan.stops else ():
        for p in g.geometry:
            lons.append(p[0])
            lats.append(p[1])
    if not lons:
        return None
    return [float(min(lons)), float(min(lats)), float(max(lons)), float(max(lats))]


def summary_view(context: "PlanContext", state: "VariantState") -> dict:
    """API variant summary (counts, finish, totals, slack, over_budget, routing quality)."""
    plan = state.plan
    n_slots = len(context.slot_codes)
    kinds = [stop_kind(s) for s in plan.stops]
    return {
        "stops_total": len(plan.stops),
        "slots_requested": n_slots,
        "slots_filled": sum(1 for s in plan.stops if stop_slot_index(s, n_slots) is not None),
        "on_the_way": kinds.count("on_the_way"),
        "pinned": kinds.count("pinned"),
        "dropped_slot_indices": list(state.dropped_slot_indices),
        "finish_at": time_point(context.window_start, plan.total_time_min),
        "finish_kind": "return" if context.shape == "loop" else "finish",
        "window_min": context.budget_min,
        "total_min": float(plan.total_time_min),
        "walk_min": float(plan.total_walk_min),
        "dwell_min": float(plan.total_dwell_min),
        "wait_min": float(plan.total_wait_min),
        "distance_km": float(plan.total_distance_km),
        "slack_min": float(context.budget_min - plan.total_time_min),
        "over_budget": bool(plan.over_budget),
        "routing": routing_quality(plan),
    }


def _seq_by_stop(state: "VariantState") -> list:
    seq = list(state.sequence)
    if len(seq) == len(state.plan.stops) and all(c.place_id == s.place_id for c, s in zip(seq, state.plan.stops)):
        return seq
    by_id = {c.place_id: c for c in seq}
    return [by_id.get(s.place_id) for s in state.plan.stops]


def stop_views(context: "PlanContext", state: "VariantState", catalog: "CityCatalog") -> list[dict]:
    """API stops of a variant (kind, slot, activity, TimePoints, dwell, wait, hours, status, interest)."""
    plan = state.plan
    slot_codes = context.slot_codes
    t0 = _t0(context)
    out = []
    for s, c in zip(plan.stops, _seq_by_stop(state)):
        h = _hours(context, catalog, s.place_id)
        status = catalog.status_of(s.place_id)
        out.append({
            "index": s.order,
            "number": s.order + 1,
            "place_id": s.place_id,
            "name": s.name,
            "lat": float(s.lat),
            "lon": float(s.lon),
            "kind": stop_kind(s),
            "slot_index": stop_slot_index(s, len(slot_codes)),
            "activity": stop_activity(s, slot_codes, state.extra_activity),
            "arrival": time_point(context.window_start, s.arrival_min),
            "visit_start": time_point(context.window_start, s.arrival_min + s.wait_min),
            "departure": time_point(context.window_start, s.depart_min),
            "dwell_min": float(s.dwell_min),
            "dwell_fixed": bool(c.dwell_fixed) if c is not None else False,
            "wait_min": float(s.wait_min),
            "hours": {"status": hours_status(s, h, t0),
                      "day": hours_on_day(h, float(context.t0) + s.arrival_min + s.wait_min) if h is not None else None},
            "business_status": status or "operational",
            "interest": float(state.interest.get(s.place_id, s.interest)),
        })
    return out


def sequence_view(context: "PlanContext", state: "VariantState") -> list[dict]:
    """The variant as EditStops — what the client sends back to /schedule or /insert."""
    from .pipeline import edit_stops
    return [e.to_dict() for e in edit_stops(context, state)]


def variant_view(context: "PlanContext", state: "VariantState", catalog: "CityCatalog", *, lang: str = "ru",
                 geometry: str = "geojson") -> dict:
    """API Variant: {index, edited, summary, messages, stops, segments, navigation, bbox, sequence}."""
    plan = state.plan
    return {
        "index": state.index,
        "edited": bool(state.edited),
        "summary": summary_view(context, state),
        "messages": [m.to_dict(lang) for m in variant_messages(context, state, catalog)],
        "stops": stop_views(context, state, catalog),
        "segments": segment_views(context, plan, geometry=geometry),
        "navigation": navigation_view(context, plan, catalog),
        "bbox": bbox_view(context, plan),
        "sequence": sequence_view(context, state),
    }


def place_cards(catalog: "CityCatalog", place_ids: Iterable[str], photo_base_url: Optional[str] = None) -> dict:
    """{place_id: PlaceCard} for the given places (unknown ids skipped), in first-seen order."""
    out: dict[str, dict] = {}
    for pid in place_ids:
        pid = str(pid)
        if pid not in out and catalog.has(pid):
            out[pid] = catalog.place_card(pid, photo_base_url=photo_base_url)
    return out


def versions_view(catalog: "CityCatalog", routing_version: Optional[str] = None) -> dict:
    return {"api": API_VERSION, "algorithm": ALGORITHM_VERSION, "catalog": catalog.version,
            "interest": INTEREST_VERSION, "routing": routing_version}


def plan_id_of(request_echo: dict, versions: dict) -> str:
    """sha1 of the normalised request + versions (same request, same data -> same id)."""
    blob = json.dumps({"request": request_echo, "versions": versions}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def personalization_view(result: "PlanResult") -> dict:
    it = result.interest
    return {"mode": it.mode, "strength": float(it.strength), "favourites_used": list(it.used),
            "favourites_ignored": list(it.ignored), "profiles": int(it.profiles)}


def debug_view(result: "PlanResult") -> dict:
    """Bench-only details: search area, effective radius, the candidate pools."""
    ctx = result.context
    cands = [{"place_id": c.place_id, "kind": "slot", "slot_index": c.slot, "activity": ctx.slot_codes[c.slot],
              "interest": float(c.interest), "lat": float(c.lat), "lon": float(c.lon)} for c in result.slot_candidates]
    cands += [{"place_id": c.place_id, "kind": "on_the_way", "slot_index": None, "activity": act,
               "interest": float(c.interest), "lat": float(c.lat), "lon": float(c.lon)}
              for c, act in zip(result.extra_candidates, result.extra_candidate_activities)]
    return {"area": [float(ctx.area[0]), float(ctx.area[1])], "search_km": float(ctx.search_km), "candidates": cands}


def _stop_place_ids(states: Iterable["VariantState"]) -> list[str]:
    seen: dict[str, None] = {}
    for st in states:
        for s in st.plan.stops:
            seen.setdefault(s.place_id, None)
    return list(seen)


def plan_response(result: "PlanResult", catalog: "CityCatalog", *, lang: str = "ru",
                  photo_base_url: Optional[str] = None, geometry: str = "geojson", debug: bool = False) -> dict:
    """API response of POST /v1/walks/plan."""
    from .pipeline import to_request_echo

    ctx = result.context
    request = to_request_echo(ctx)
    versions = versions_view(catalog, result.routing_version)
    resp = {
        "plan_id": plan_id_of(request, versions),
        "versions": versions,
        "status": result.status,
        "request": request,
        "personalization": personalization_view(result),
        "messages": [m.to_dict(lang) for m in result.messages],
        "variants": [variant_view(ctx, st, catalog, lang=lang, geometry=geometry) for st in result.variants],
        "places": place_cards(catalog, _stop_place_ids(result.variants), photo_base_url),
    }
    if debug:
        resp["debug"] = debug_view(result)
    return resp


def edit_response(context: "PlanContext", state: "VariantState", catalog: "CityCatalog", *, lang: str = "ru",
                  photo_base_url: Optional[str] = None, geometry: str = "geojson",
                  inserted_index: Optional[int] = None, messages: Iterable[Message] = (),
                  routing_version: Optional[str] = None) -> dict:
    """API response of POST /v1/walks/schedule and /insert ({versions, request, variant, places,
    messages} + inserted_index for insert)."""
    from .pipeline import to_request_echo

    resp = {
        "versions": versions_view(catalog, routing_version),
        "request": to_request_echo(context),
        "variant": variant_view(context, state, catalog, lang=lang, geometry=geometry),
        "places": place_cards(catalog, _stop_place_ids([state]), photo_base_url),
        "messages": [m.to_dict(lang) for m in messages],
    }
    if inserted_index is not None:
        resp["inserted_index"] = int(inserted_index)
    return resp


def error_body(err, lang: str = "ru") -> dict:
    """HTTP error body {"error": {"code", "message", "params"}} of a PlannerInputError."""
    return {"error": err.to_dict(lang)}


def config_view(catalog: "CityCatalog") -> dict:
    """What a client needs to build the form (GET /v1/walks/config): the city, the activity / style /
    shape codes with labels, the visit-length choices, defaults, limits and versions."""
    from .pipeline import DEFAULTS, LIMITS
    from .slots import ACTIVITY_TYPES, DWELL_CHOICES, SHAPES, STYLES

    return {
        "city": catalog.city, "timezone": catalog.timezone,
        "center": {"lat": catalog.center[0], "lon": catalog.center[1]}, "bbox": list(catalog.bbox),
        "activities": [{"code": a.code, "label_ru": a.label_ru, "label_en": a.label_en, "group": a.group,
                        "base_dwell_min": a.base_dwell_min} for a in ACTIVITY_TYPES.values()],
        "styles": [{"code": c.code, "label_ru": c.label_ru, "label_en": c.label_en} for c in STYLES.values()],
        "shapes": [{"code": c.code, "label_ru": c.label_ru, "label_en": c.label_en} for c in SHAPES.values()],
        "dwell_choices": [m for m in DWELL_CHOICES if m is not None],
        "defaults": {k: (list(v) if isinstance(v, (list, tuple)) else v) for k, v in DEFAULTS.items()},
        "limits": {k: (list(v) if isinstance(v, tuple) else v) for k, v in LIMITS.items()},
        "versions": versions_view(catalog),
    }


# --------------------------------------------------------------------------- #
# Dashboard render helpers (Russian, exactly the page's strings)
# --------------------------------------------------------------------------- #
def kind_label_ru(x, slot_codes: list[str], extra_activity: dict) -> str:
    """The page's `kind_of` for a stop or a candidate: '📍 ваше место', '<type> · по пути, можно
    пройти мимо', or the slot's label (the stop's theme when it has no slot)."""
    if x.pinned:
        return KIND_PINNED_RU
    if x.extra:
        act = extra_activity.get(x.place_id)
        return f"{LABEL_RU_BY_ACTIVITY.get(act, x.theme) if act else x.theme}{ON_THE_WAY_SUFFIX_RU}"
    if 0 <= x.slot < len(slot_codes):
        return LABEL_RU_BY_ACTIVITY[slot_codes[x.slot]]
    return x.theme


def variant_summary_ru(context: "PlanContext", state: "VariantState") -> str:
    """'7 мест · 1.5 км · до 13:45' (+ ' · ✏️ изменён')."""
    p = state.plan
    n = len(p.stops)
    return (f"{n} {places_word_ru(n)} · {p.total_distance_km:.1f} км · до {context.clock(p.total_time_min)}"
            + (EDITED_SUFFIX_RU if state.edited else ""))


def variants_caption_ru(context: "PlanContext", states: list, sel: int) -> Optional[str]:
    """The variant picker caption ('**Вариант 1**: … · Вариант 2: …'); None for a single variant."""
    if len(states) <= 1:
        return None

    def vsum(i):
        txt = variant_summary_ru(context, states[i])
        return f"**Вариант {i + 1}**: {txt}" if i == sel else f"Вариант {i + 1}: {txt}"
    return "  ·  ".join(vsum(i) for i in range(len(states)))


def metrics_ru(context: "PlanContext", plan) -> dict:
    """The page's four metrics (labels, values, delta, help texts) of a non-empty plan."""
    slack = context.budget_min - plan.total_time_min
    n_extra = sum(1 for s in plan.stops if s.extra)
    n_pin = sum(1 for s in plan.stops if s.pinned)
    n_core = len(plan.stops) - n_extra - n_pin
    wm = int(round(plan.total_walk_min))
    t_end = context.end_label
    return {
        "stops_label": "Остановок",
        "stops": (f"{n_core} из {len(context.slot_codes)}" + (f" + {n_extra}" if n_extra else "")
                  + (f" + 📍{n_pin}" if n_pin else "")),
        "stops_help": STOPS_HELP_RU,
        "finish_label": "Финиш" if context.shape != "loop" else "Возвращение",
        "finish": context.clock(plan.total_time_min),
        "delta": (f"+{slack:.0f} мин запаса" if slack >= 0 else f"-{-slack:.0f} мин, не успеваю"),
        "walk_label": "Пешком",
        "walk": f"{plan.total_distance_km:.1f} км · {wm // 60}:{wm % 60:02d}",
        "walk_help": f"{wm} мин ходьбы, {plan.total_distance_km:.1f} км",
        "window_label": "Окно",
        "window": f"{context.start_label}–{t_end.split(' ')[0]}" + ("⁺¹" if context.next_day else ""),
        "window_help": f"{context.start_label}–{t_end}",
    }


def stop_card_lines_ru(context: "PlanContext", stop, kind_label: str, hours, business_status: str = "") -> list[str]:
    """A stop card's lines without markdown: title + kind, visit times (+ wait), hours at the visit
    start (when the catalog has hours), the may-be-closed line, and (v1) the temporarily-closed line."""
    lines = [f"{stop.order + 1}. {stop.name} — {kind_label}", _visit_line_ru(context, stop)]
    if context.has_hours:
        lines.append(f"🕒 {hours_label_ru(hours, float(context.t0) + stop.arrival_min + stop.wait_min)}")
    if stop.hours_ok is False:
        lines.append(STOP_CLOSED_LINE_RU)
    if business_status == "temporarily_closed":
        lines.append(TEMP_CLOSED_LINE_RU)
    return lines


def _visit_line_ru(context: "PlanContext", s) -> str:
    return (f"{context.clock(s.arrival_min + s.wait_min)}–{context.clock(s.depart_min)} · осмотр {s.dwell_min:.0f} мин"
            + (f" · ⏳ приходим в {context.clock(s.arrival_min)}, ждём открытия {s.wait_min:.0f} мин"
               if s.wait_min >= 1 else ""))


def stop_card_markdown_ru(context: "PlanContext", stop, kind_label: str, hours, business_status: str = "") -> str:
    """The exact markdown the page puts on a stop card (bold title, italic kind, '  \\n' breaks)."""
    lines = stop_card_lines_ru(context, stop, kind_label, hours, business_status)
    return f"**{stop.order + 1}. {stop.name}** — _{kind_label}_  \n" + "  \n".join(lines[1:])


def render_variant_ru(context: "PlanContext", states: list, sel: int, catalog: "CityCatalog") -> dict:
    """Everything the page's result view shows for variant `sel` as text: the variant caption, the
    four metrics, captions / warnings / infos and the stop cards (lines). The keys equal the golden
    baseline's `render` digest."""
    state = states[sel]
    plan = state.plan
    out = {"manual": bool(state.edited), "variants_caption": variants_caption_ru(context, states, sel),
           "metric_stops": None, "metric_finish_label": None, "metric_finish": None, "metric_delta": None,
           "metric_walk": None, "metric_window": None, "captions": [], "warnings": [], "infos": [], "stop_lines": []}
    msgs = variant_messages(context, state, catalog)
    if plan.stops:
        m = metrics_ru(context, plan)
        out.update(metric_stops=m["stops"], metric_finish_label=m["finish_label"], metric_finish=m["finish"],
                   metric_delta=m["delta"], metric_walk=m["walk"], metric_window=m["window"])
    for msg in msgs:
        if msg.code in _CAPTION_CODES:
            out["captions"].append(render(msg.code, msg.params, "ru"))
        elif msg.code in _WARNING_CODES:
            out["warnings"].append(render(msg.code, msg.params, "ru"))
        elif msg.code in _INFO_CODES:
            out["infos"].append(render(msg.code, msg.params, "ru"))
    for s in plan.stops:
        out["stop_lines"].append(stop_card_lines_ru(
            context, s, kind_label_ru(s, context.slot_codes, state.extra_activity),
            _hours(context, catalog, s.place_id), catalog.status_of(s.place_id)))
    return out
