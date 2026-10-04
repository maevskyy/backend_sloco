"""Walk Planner — sequence interest-matched places into a comfortable walking route.

This is the *sequencing / optimization* layer that sits on top of the existing
``LocationRecommender`` (which selects interest-matched candidate places). It is a
small, dependency-light core (numpy only) so it can run both inside the Streamlit
dashboard and, later, behind a backend endpoint for the mobile app.

Problem framing — Tourist Trip Design Problem (TTDP) / Orienteering:
  * "slots" (default) — the user fixes an ORDERED list of activity types
    (e.g. sight -> coffee -> park -> dinner). We pick the best place per slot and
    order it geometrically, via a distinctness-constrained beam search over the
    per-slot candidate lists — **each place is used at most once** (no repeats),
    and a slot with no unused candidate is left empty rather than duplicated.
  * "auto" — free subset + order ("see as many good places as fit in the budget").
    Solved with a greedy interest/budget selection + nearest-neighbour + 2-opt tour.

The travel-cost model lives behind ``RoutingProvider`` so the estimator can be
swapped without touching the algorithms. The optimizer always uses the offline
straight-line estimate (``RoutingProvider.walk_minutes``); only the final assembly
asks ``route_legs`` for street geometry/times. The production chain (self-hosted OSRM
-> ORS cloud -> estimate, ``ChainProvider``) and the old ``ORSProvider`` live in
``routing.py``; every leg says where it came from (``Segment.quality`` / ``provider``).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Optional

import numpy as np

__all__ = [
    "EARTH_R_KM", "DEFAULT_WALK_KMH", "DEFAULT_DETOUR", "DWELL_MINUTES", "DEFAULT_DWELL", "STYLE_PRESETS",
    "MIN_WALK_WEIGHT", "EXTRA_DWELL_MIN", "EXTRA_INTEREST_POWER", "EXTRA_MIN_INTEREST", "MUST_VISIT_WALK_MIN",
    "VARIANT_REUSE_FACTOR", "SCENIC_THEMES", "SCENIC_SUBTYPES", "SKIP_SLOT_PENALTY", "WEEK_MIN",
    "GMAPS_MAX_WAYPOINTS",
    "dwell_for", "week_intervals_from_timetable", "visit_wait", "open_within", "estimate_dwell_min",
    "haversine_km", "RoutingProvider", "Candidate", "WalkRequest", "Stop", "Segment", "WalkPlan",
    "reach_radius_km", "plan_walk", "best_insertion", "plan_variants", "plan_sequence", "navigation_links",
]   # + ORSProvider, resolved lazily from routing (module __getattr__ below; exported by routing)

EARTH_R_KM = 6371.0088
DEFAULT_WALK_KMH = 4.5          # comfortable city walking pace
DEFAULT_DETOUR = 1.35          # straight-line -> street-network inflation factor

# Visit duration (minutes) keyed by activity theme / subtype. Tune freely.
DWELL_MINUTES: dict[str, float] = {
    "food_drink": 60.0, "restaurant": 75.0, "cafe": 30.0, "coffee": 30.0, "bar": 60.0,
    "culture_sights": 45.0, "museum": 60.0, "art_gallery": 45.0,
    "religious_sights": 20.0, "church": 20.0, "mosque": 20.0,
    "markets_walks": 40.0, "market": 40.0, "town_square": 20.0,
    "nature_outdoors": 40.0, "park": 40.0, "garden": 40.0, "viewpoint": 15.0,
    "performing_arts": 90.0, "leisure_active": 60.0, "shopping_souvenirs": 30.0,
    "things_to_do": 45.0,
}
DEFAULT_DWELL = 40.0

# Style presets:
#   interest_weight : walking minutes one unit of interest is worth
#   max_stops       : hard cap on stops (auto mode)
#   fill            : slots mode — share of the time window to fill by adding optional stops
#   dwell_scale     : visit length multiplier (an unhurried walk stays longer)
#   walk_scale      : multiplier on the cost of a walking minute (scenic: walking IS the point)
STYLE_PRESETS = {
    "max":    {"interest_weight": 8.0,  "max_stops": None, "fill": 0.95, "dwell_scale": 1.0, "walk_scale": 1.0},
    "chill":  {"interest_weight": 16.0, "max_stops": 4,    "fill": 0.75, "dwell_scale": 1.3, "walk_scale": 1.0},
    "scenic": {"interest_weight": 12.0, "max_stops": None, "fill": 0.90, "dwell_scale": 1.0, "walk_scale": 0.5},
}
# A walking minute never gets cheaper than this share of a "tight window" minute.
MIN_WALK_WEIGHT = 0.05
# Stops "on the way" (optional, added to use a roomy window) are a look / pass-by, not a full
# visit: at most this many minutes each. Their value grows with interest squared so real
# highlights beat merely-good places next door; repeats are fine (it's what you walk past).
EXTRA_DWELL_MIN = 10.0
EXTRA_INTEREST_POWER = 2.0
EXTRA_MIN_INTEREST = 0.3          # an optional stop must be at least this interesting
MUST_VISIT_WALK_MIN = 10.0        # walking reserved per must-visit place when sizing the slots
VARIANT_REUSE_FACTOR = 0.35       # plan_variants: interest multiplier for places earlier variants used
SCENIC_THEMES = {"nature_outdoors", "markets_walks"}
SCENIC_SUBTYPES = {"park", "garden", "botanical_garden", "promenade", "waterfront", "viewpoint", "river"}


def dwell_for(theme: Optional[str], subtype: Optional[str] = None) -> float:
    """Visit-duration in minutes for a stop, by subtype then theme, else default."""
    for key in (subtype, theme):
        if key and key in DWELL_MINUTES:
            return DWELL_MINUTES[key]
    return DEFAULT_DWELL


# --------------------------------------------------------------------------- #
# Opening hours
# --------------------------------------------------------------------------- #
# Hours are a list of [open, close] minutes from Monday 00:00 (close may pass the end of the
# day, e.g. a bar 18:00-02:00). None = unknown -> treated as always open (and labelled so).
WEEK_MIN = 7 * 24 * 60
_DFS_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def week_intervals_from_timetable(timetable) -> Optional[list[list[int]]]:
    """DataForSEO ``work_time.work_hours.timetable`` -> sorted ``[[open, close], ...]`` in minutes
    from Monday 00:00, or None when there is no timetable (hours unknown). A weekday that is
    missing or null is closed; ``close <= open`` means after midnight (00:00 = end of the day,
    00:00-00:00 = open 24 h)."""
    if not timetable:
        return None
    out = []
    for di, day in enumerate(_DFS_DAYS):
        for iv in timetable.get(day) or []:
            try:
                o = int(iv["open"]["hour"]) * 60 + int(iv["open"]["minute"])
                c = int(iv["close"]["hour"]) * 60 + int(iv["close"]["minute"])
            except (KeyError, TypeError, ValueError):
                continue
            if c <= o:
                c += 24 * 60
            out.append([di * 1440 + o, di * 1440 + c])
    return sorted(out)


@lru_cache(maxsize=65536)
def _merged_week(hours: tuple) -> tuple:
    """Opening intervals repeated for the previous / next week and merged, so Sunday-night
    openings cover early Monday and back-to-back days (Mon 00-24, Tue 00-24) form one span."""
    spans = sorted((o + k, c + k) for o, c in hours for k in (-WEEK_MIN, 0, WEEK_MIN))
    merged: list[list[float]] = []
    for o, c in spans:
        if merged and o <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], c)
        else:
            merged.append([o, c])
    return tuple((o, c) for o, c in merged)


def visit_wait(hours, t_week: float, dwell: float, max_wait: float = 30.0) -> Optional[float]:
    """Minutes to wait at the door so that the WHOLE visit (``dwell``) fits one opening span,
    arriving at ``t_week`` (minutes from Monday 00:00, any week). 0 when hours are unknown;
    None when the place is closed then and won't open within ``max_wait``."""
    if hours is None:
        return 0.0
    t = float(t_week) % WEEK_MIN
    for o, c in _merged_week(tuple(tuple(iv) for iv in hours)):
        start = max(t, o)
        if start + dwell <= c + 1e-9 and start - t <= max_wait + 1e-9:
            return start - t
    return None


def open_within(hours, t_from: float, t_to: float, dwell: float) -> bool:
    """True when a ``dwell``-minute visit fits somewhere inside [t_from, t_to] (week minutes) —
    a cheap pre-filter that drops places closed for the whole time window."""
    if hours is None:
        return True
    a = float(t_from) % WEEK_MIN
    b = a + (float(t_to) - float(t_from))
    return any(min(b, c) - max(a, o) >= dwell - 1e-9
               for o, c in _merged_week(tuple(tuple(iv) for iv in hours)))


# Visit length per PLACE, not per type: a courtyard park with 2 reviews is a 10-minute stop, a
# city park with 30k reviews an hour; a bell tower or a statue is a look, not a 45-minute visit.
_QUICK_LOOK = re.compile(
    r"\b(monument|memorial|statue|sculpture|fountain|bell tower|clock tower|tower|bridge|obelisk|"
    r"triumphal arch|arch|gate|bust|plaque|mural|viewpoint|square|plaza)\b", re.I)
_BRIEF_HINT = re.compile(r"\b(brief|quick|short stop|short visit|small|tiny|pocket|courtyard)\b", re.I)
_FOOD_DWELL_THEMES = {"food_drink", "restaurant", "cafe", "coffee", "bar"}


def estimate_dwell_min(theme: Optional[str], subtype: Optional[str] = None, n_reviews=None,
                       kind_text: str = "", summary: str = "") -> float:
    """Minutes a typical visitor spends at THIS place.

    Starts from the type (``dwell_for``; any "...museum" type = museum), caps quick-look places
    (monument, statue, bell tower, square ... in the Google type or the AI place type) at 15 min,
    scales by size with the review count as a proxy (<20 ×0.5, <200 ×0.75, <2k ×1, <10k ×1.25,
    more ×1.5) and shortens by 40 % when the description calls it brief / small / a courtyard.
    Food & drink keep their base — how long you sit does not grow with fame. Rounded to 5 min,
    10..180."""
    base = dwell_for(theme, subtype)
    if subtype and "museum" in subtype:
        base = max(base, DWELL_MINUTES["museum"])
    if theme not in _FOOD_DWELL_THEMES:
        if _QUICK_LOOK.search(f"{subtype or ''} {kind_text or ''}".replace("_", " ")):
            base = min(base, 15.0)
        try:
            n = float(n_reviews)
        except (TypeError, ValueError):
            n = float("nan")
        if n == n:                                   # not NaN
            base *= 0.5 if n < 20 else 0.75 if n < 200 else 1.0 if n < 2000 else 1.25 if n < 10000 else 1.5
        if _BRIEF_HINT.search(f"{kind_text or ''} {summary or ''}"):
            base *= 0.6
    return float(min(180.0, max(10.0, 5 * round(base / 5))))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


# --------------------------------------------------------------------------- #
# Routing providers (swappable travel-cost + geometry model)
# --------------------------------------------------------------------------- #
@dataclass
class RoutingProvider:
    """Offline great-circle estimator. ``walk_minutes`` powers optimization;
    ``route`` returns straight-line display geometry. Coordinates are (lat, lon)."""

    walk_kmh: float = DEFAULT_WALK_KMH
    detour: float = DEFAULT_DETOUR

    def walk_minutes(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        km = haversine_km(a[0], a[1], b[0], b[1]) * self.detour
        return km / max(self.walk_kmh, 1e-6) * 60.0

    def matrix(self, coords: list[tuple[float, float]]) -> np.ndarray:
        n = len(coords)
        m = np.zeros((n, n), dtype=float)
        for i in range(n):
            for j in range(n):
                if i != j:
                    m[i, j] = self.walk_minutes(coords[i], coords[j])
        return m

    def route_legs(self, coords: list[tuple[float, float]]) -> list[dict]:
        """``route`` for every consecutive pair of points (one dict per leg)."""
        return [self.route([coords[i], coords[i + 1]]) for i in range(len(coords) - 1)]

    def route(self, coords: list[tuple[float, float]]) -> dict:
        """Geometry + duration + distance for an ordered leg/path of (lat, lon) points.

        Returns ``{"geometry": [[lon, lat], ...], "duration_min", "distance_km", "quality",
        "provider"}`` (geometry is [lon, lat] to match GeoJSON / plotting conventions). This
        offline estimator draws straight lines, so its legs are labelled ``quality="estimate"``,
        ``provider="haversine"`` (street routers in ``routing.py`` say ``"streets"``)."""
        geom = [[c[1], c[0]] for c in coords]
        dur = sum(self.walk_minutes(coords[i], coords[i + 1]) for i in range(len(coords) - 1))
        dist = sum(haversine_km(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1]) * self.detour
                   for i in range(len(coords) - 1))
        return {"geometry": geom, "duration_min": dur, "distance_km": dist,
                "quality": "estimate", "provider": "haversine"}


def __getattr__(name: str):
    """``ORSProvider`` moved to ``routing.py`` (strict ORS router + the provider chain); it stays
    importable from here for old callers. Resolved lazily so the core never imports the routing
    module (and its HTTP stack) itself."""
    if name == "ORSProvider":
        from .routing import ORSProvider
        return ORSProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    place_id: str
    name: str
    lat: float
    lon: float
    slot: int = 0                     # which activity slot this candidate can fill
    theme: str = ""
    subtype: str = ""
    interest: float = 0.0             # 0..1 interest score (from the recommender)
    dwell_min: Optional[float] = None  # derived from theme/subtype when None
    open_hours: Optional[list] = None  # [[open, close], ...] week minutes; None = unknown
    extra: bool = False               # optional stop: only used to fill a roomy time window
    pinned: bool = False              # the user's own place (must visit / added by hand)
    dwell_fixed: bool = False         # the user set this visit length: styles don't rescale it

    def __post_init__(self):
        if self.dwell_min is None:
            self.dwell_min = dwell_for(self.theme, self.subtype)

    def coord(self) -> tuple[float, float]:
        return (self.lat, self.lon)


@dataclass
class WalkRequest:
    candidates: list[Candidate]
    mode: str = "slots"               # "slots" (fixed-order) | "auto" (free subset/order)
    n_slots: int = 0                  # slots mode; inferred from candidates when 0
    start: Optional[tuple[float, float]] = None  # (lat, lon)
    shape: str = "one_way"            # "one_way" | "loop" | "free"
    style: str = "max"                # "max" | "chill" | "scenic"
    time_budget_min: float = 180.0
    fit_budget: bool = True           # slots mode: drop slots / prefer nearer places to fit the budget
    start_week_min: Optional[float] = None       # departure as minutes from Monday 00:00 -> opening
                                                 # hours are enforced (slots mode); None = ignored
    max_wait_min: float = 30.0        # longest wait at a door for a place to open
    fill_window: bool = True          # slots mode: add `extra` candidates up to the style's fill share
    must_visit: list = field(default_factory=list)  # places the user wants in the route, wherever fits best
    walk_weight: Optional[float] = None          # cost of a walking minute; None = adapt to the window
    max_leg_min: Optional[float] = 45.0          # longest single walk between two points (fit mode)
    interest_weight: Optional[float] = None      # overrides the style preset when set
    max_stops: Optional[int] = None              # overrides the style preset when set
    provider: RoutingProvider = field(default_factory=RoutingProvider)


@dataclass
class Stop:
    order: int
    place_id: str
    name: str
    lat: float
    lon: float
    slot: int
    theme: str
    interest: float
    dwell_min: float
    arrival_min: float
    depart_min: float
    wait_min: float = 0.0             # waiting at the door before it opens (visit starts at arrival + wait)
    hours_ok: Optional[bool] = None   # None = no hours / no clock; False = closes before the visit ends
    extra: bool = False               # added to fill the window (not one of the requested slots)
    pinned: bool = False              # the user's own place (must visit / added by hand)


@dataclass
class Segment:
    from_order: int          # -1 = start anchor
    to_order: int            # -1 = return to start anchor
    walk_min: float
    distance_km: float
    geometry: list           # [[lon, lat], ...]
    quality: str = "estimate"     # "streets" (a street router) | "estimate" (straight line x detour)
    provider: str = "haversine"   # "osrm" | "ors" | "haversine"


@dataclass
class WalkPlan:
    stops: list[Stop]
    segments: list[Segment]
    total_time_min: float
    total_walk_min: float
    total_dwell_min: float
    total_distance_km: float
    over_budget: bool
    note: str = ""
    dropped_slots: list[int] = field(default_factory=list)  # slots mode: slots left empty to fit the budget
    total_wait_min: float = 0.0
    routing_quality: str = "none"   # "streets" (every segment) | "estimate" (every one) | "mixed" | "none" (no segments)


def reach_radius_km(time_budget_min: float, dwell_total_min: float, shape: str = "loop",
                    walk_kmh: float = DEFAULT_WALK_KMH, detour: float = DEFAULT_DETOUR,
                    min_walk_min: float = 15.0) -> float:
    """How far from the start a stop can be and still fit the time window.

    Walking time left after the visits (``budget - dwell``) bounds the route length; a loop
    has to come back, so a stop can be at most half of it away (one-way: all of it; "free"
    has no start, so the whole route must fit a disk of that diameter around the district
    centre). Straight-line distance, deflated by the street ``detour`` factor. Never below
    ``min_walk_min`` of walking, so an over-packed window still yields a small search area."""
    walk_min = max(float(time_budget_min) - float(dwell_total_min), min_walk_min)
    km = walk_min / 60.0 * walk_kmh / max(detour, 1e-6)
    return km if shape == "one_way" else km / 2.0


# --------------------------------------------------------------------------- #
# Sequencing — fixed-order slots (exact DP)
# --------------------------------------------------------------------------- #
def _group_by_slot(candidates: list[Candidate], n_slots: int) -> list[list[Candidate]]:
    if n_slots <= 0:
        n_slots = (max((c.slot for c in candidates), default=-1) + 1)
    groups: list[list[Candidate]] = [[] for _ in range(n_slots)]
    for c in candidates:
        if 0 <= c.slot < n_slots:
            groups[c.slot].append(c)
    return groups


# Cost of leaving a slot empty when the time budget is enforced: larger than any walking /
# interest trade-off, so a plan that fills more slots within the budget always wins.
SKIP_SLOT_PENALTY = 1e4


def _solve_slots(by_slot: list[list[Candidate]], start, shape: str,
                 interest_weight: float, provider: RoutingProvider,
                 beam_width: int = 48, time_budget_min: Optional[float] = None,
                 start_week_min: Optional[float] = None, max_wait_min: float = 30.0,
                 walk_weight: float = 1.0, max_leg_min: Optional[float] = None) -> list[Candidate]:
    """Fill each slot (in fixed order) with its best place, minimizing
    ``Σ walk_minutes - interest_weight * Σ interest``, subject to **each place used at
    most once** (no repeats — a place that qualifies for several slots takes only one).

    A plain layered DP would happily reuse the same place across slots because a
    self-transition costs 0 walking; the all-distinct constraint couples the layers, so
    we carry the used-place set through a beam search. For the tiny sizes here (a handful
    of slots × top-K candidates) the beam is effectively exact. When a slot has no unused
    candidate left it is skipped (fewer stops) instead of duplicating a place.

    With ``time_budget_min`` the route must also fit the window (walking + visits, plus the
    walk back for a loop): extensions that cannot fit are pruned, and a slot may be left empty
    (at ``SKIP_SLOT_PENALTY``) so the planner keeps as many slots as the time allows — e.g. a
    far high-interest place loses to a nearer one once it would overrun the window.

    With ``start_week_min`` (the departure clock) a place must be open for the whole visit when
    the route reaches it; waiting up to ``max_wait_min`` for it to open is allowed and costs like
    walking. Places with unknown hours are treated as open.

    ``walk_weight`` scales what a walking minute costs against interest: < 1 when the window is
    roomy, so the route may reach better places further out instead of hugging the start."""
    slots = [s for s in by_slot if s]           # skip empty slots gracefully
    if not slots:
        return []
    has_start = start is not None
    budget = float(time_budget_min) if time_budget_min is not None else None
    back_to = start if (shape == "loop" and has_start) else None
    if budget is not None:
        beam_width = max(beam_width, 256)       # pruning by time needs more states kept alive

    # beam entries: (cost, chosen[], used_place_ids, elapsed_min)
    beam: list[tuple[float, list[Candidate], frozenset, float]] = [(0.0, [], frozenset(), 0.0)]
    for cur in slots:
        nxt: list[tuple[float, list[Candidate], frozenset, float]] = []
        for cost, chosen, used, elapsed in beam:
            prev = start if (not chosen and has_start) else (chosen[-1].coord() if chosen else None)
            added = False
            for c in cur:
                if c.place_id in used:
                    continue
                step = provider.walk_minutes(prev, c.coord()) if prev is not None else 0.0
                if max_leg_min is not None and step > max_leg_min:
                    continue                                  # no marathon legs between two stops
                wait = 0.0
                if start_week_min is not None:
                    wait = visit_wait(c.open_hours, start_week_min + elapsed + step, c.dwell_min, max_wait_min)
                    if wait is None:
                        continue                              # closed when we'd get there
                t = elapsed + step + wait + c.dwell_min
                if budget is not None:
                    back = provider.walk_minutes(c.coord(), back_to) if back_to is not None else 0.0
                    if t + back > budget + 1e-6:
                        continue                              # cannot fit, even going straight back
                nxt.append((cost + walk_weight * step + wait - interest_weight * c.interest,
                            chosen + [c], used | {c.place_id}, t))
                added = True
            if budget is not None or not added:
                # leave this slot empty: forced (no usable candidate) or to stay within the budget
                nxt.append((cost + (SKIP_SLOT_PENALTY if budget is not None else 0.0), chosen, used, elapsed))
        nxt.sort(key=lambda x: x[0])
        beam = nxt[:beam_width]

    if back_to is not None:
        beam = [((cost + walk_weight * provider.walk_minutes(chosen[-1].coord(), back_to)) if chosen else cost,
                 chosen, used, elapsed) for cost, chosen, used, elapsed in beam]
        beam.sort(key=lambda x: x[0])
    return beam[0][1] if beam else []


# --------------------------------------------------------------------------- #
# Sequencing — free order (nearest-neighbour + 2-opt) and auto selection
# --------------------------------------------------------------------------- #
def _nn_order(coords: list[tuple[float, float]], provider: RoutingProvider, start_idx: int = 0) -> list[int]:
    unvisited = set(range(len(coords)))
    unvisited.discard(start_idx)
    order, cur = [start_idx], start_idx
    while unvisited:
        nxt = min(unvisited, key=lambda k: provider.walk_minutes(coords[cur], coords[k]))
        order.append(nxt)
        unvisited.discard(nxt)
        cur = nxt
    return order


def _two_opt(order: list[int], coords, provider: RoutingProvider, closed: bool = False,
             fix_first: bool = True) -> list[int]:
    def leg(a, b):
        return provider.walk_minutes(coords[a], coords[b])

    def length(o):
        total = sum(leg(o[i], o[i + 1]) for i in range(len(o) - 1))
        return total + (leg(o[-1], o[0]) if closed and len(o) > 1 else 0.0)

    best = order[:]
    start_i = 1 if fix_first else 0
    improved = True
    while improved:
        improved = False
        for i in range(start_i, len(best) - 1):
            for k in range(i + 1, len(best)):
                cand = best[:i] + best[i:k + 1][::-1] + best[k + 1:]
                if length(cand) + 1e-9 < length(best):
                    best = cand
                    improved = True
    return best


def _order_set(cands: list[Candidate], req: WalkRequest, provider: RoutingProvider) -> list[Candidate]:
    if len(cands) <= 1:
        return list(cands)
    if req.start is not None:
        coords = [req.start] + [c.coord() for c in cands]
        order = _two_opt(_nn_order(coords, provider, 0), coords, provider,
                         closed=(req.shape == "loop"), fix_first=True)
        return [cands[i - 1] for i in order if i != 0]
    coords = [c.coord() for c in cands]
    order = _two_opt(_nn_order(coords, provider, 0), coords, provider, closed=False, fix_first=False)
    return [cands[i] for i in order]


def _tour_minutes(seq: list[Candidate], req: WalkRequest, provider: RoutingProvider) -> float:
    pts = ([req.start] if req.start is not None and req.shape != "free" else []) + [c.coord() for c in seq]
    if req.shape == "loop" and req.start is not None:
        pts = pts + [req.start]
    walk = sum(provider.walk_minutes(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    return walk + sum(c.dwell_min for c in seq)


def _auto_select(req: WalkRequest, provider: RoutingProvider, max_stops: Optional[int]) -> list[Candidate]:
    """Orienteering-lite: greedily add the highest-interest place that still fits the
    time budget, re-ordering the tour each step. ``scenic`` boosts green/waterfront."""
    pool = [Candidate(**{**c.__dict__}) for c in req.candidates]  # shallow copies (safe to reweight)
    if req.style == "scenic":
        for c in pool:
            if c.theme in SCENIC_THEMES or c.subtype in SCENIC_SUBTYPES:
                c.interest = min(1.0, c.interest * 1.25 + 0.05)
    pool.sort(key=lambda c: c.interest, reverse=True)

    cap = max_stops if max_stops is not None else len(pool)
    chosen: list[Candidate] = []
    used: set[str] = set()
    for c in pool:
        if len(chosen) >= cap:
            break
        if c.place_id in used:                      # never place the same venue twice
            continue
        ordered = _order_set(chosen + [c], req, provider)
        if _tour_minutes(ordered, req, provider) <= req.time_budget_min:
            chosen = ordered
            used.add(c.place_id)
    return chosen


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def _routing_quality(segments: list[Segment]) -> str:
    """``WalkPlan.routing_quality``: "streets" when every segment came from a street router,
    "estimate" when none did, "mixed" in between, "none" for a plan without segments."""
    if not segments:
        return "none"
    n_streets = sum(s.quality == "streets" for s in segments)
    return "streets" if n_streets == len(segments) else "estimate" if n_streets == 0 else "mixed"


def _assemble_plan(chosen: list[Candidate], req: WalkRequest, provider: RoutingProvider) -> WalkPlan:
    pts: list[tuple[str, tuple[float, float]]] = []
    if chosen:      # an empty plan has no points: no router call, no phantom start -> start segment
        if req.start is not None and req.shape != "free":
            pts.append(("start", req.start))
        for c in chosen:
            pts.append(("stop", c.coord()))
        if req.shape == "loop" and req.start is not None:
            pts.append(("start", req.start))

    segments: list[Segment] = []
    legs = provider.route_legs([p[1] for p in pts]) if len(pts) >= 2 else []
    # Stop.order of every point (-1 = the start anchor), counted over the ACTUAL point list: a route
    # without a start anchor (shape "free", or no start given) must not be shifted by one.
    order_of: list[int] = []
    n_stops = 0
    for kind, _coord in pts:
        if kind == "stop":
            order_of.append(n_stops)
            n_stops += 1
        else:
            order_of.append(-1)
    for i, leg in enumerate(legs):
        segments.append(Segment(
            from_order=order_of[i],
            to_order=order_of[i + 1],
            walk_min=leg["duration_min"], distance_km=leg["distance_km"], geometry=leg["geometry"],
            quality=leg.get("quality", "estimate"), provider=leg.get("provider", "haversine"),
        ))

    stops: list[Stop] = []
    clock = 0.0
    total_walk = 0.0
    total_dwell = 0.0
    total_wait = 0.0
    order = 0
    for i, (kind, _coord) in enumerate(pts):
        if i > 0:
            clock += segments[i - 1].walk_min
            total_walk += segments[i - 1].walk_min
        if kind == "stop":
            c = chosen[order]
            arrival = clock
            wait, hours_ok = 0.0, None
            if req.start_week_min is not None and c.open_hours is not None:
                # re-check with the REAL leg times (ORS may differ from the estimate the solver used)
                w = visit_wait(c.open_hours, req.start_week_min + arrival, c.dwell_min, req.max_wait_min + 60.0)
                hours_ok = w is not None
                wait = w or 0.0
            depart = arrival + wait + c.dwell_min
            stops.append(Stop(order=order, place_id=c.place_id, name=c.name, lat=c.lat, lon=c.lon,
                              slot=c.slot, theme=c.theme, interest=c.interest, dwell_min=c.dwell_min,
                              arrival_min=arrival, depart_min=depart, wait_min=wait, hours_ok=hours_ok,
                              extra=c.extra, pinned=c.pinned))
            total_dwell += c.dwell_min
            total_wait += wait
            clock = depart
            order += 1

    total_dist = sum(s.distance_km for s in segments)
    total_time = total_walk + total_dwell + total_wait
    over = total_time > req.time_budget_min + 1e-6
    note = (f"Route needs ~{total_time:.0f} min but the budget is {req.time_budget_min:.0f} min."
            if over else "")
    return WalkPlan(stops=stops, segments=segments, total_time_min=total_time,
                    total_walk_min=total_walk, total_dwell_min=total_dwell,
                    total_distance_km=total_dist, over_budget=over, note=note, total_wait_min=total_wait,
                    routing_quality=_routing_quality(segments))


def _adaptive_walk_weight(by_slot: list[list[Candidate]], req: WalkRequest, preset: dict) -> float:
    """What a walking minute costs relative to interest, given how roomy the window is.

    The requested visits (shortest option per slot) plus ~15 min of walking each is what the
    route NEEDS; the rest of the window is spare. A tight window keeps the full weight (hug the
    start); a roomy one makes walking cheap — (need / window)², floor MIN_WALK_WEIGHT — so real
    highlights further out win over so-so places next door; then the style's ``walk_scale``
    applies (scenic walks more)."""
    filled = [s for s in by_slot if s]
    need = sum(min(c.dwell_min for c in s) for s in filled) + 15.0 * len(filled)
    w = min(1.0, max(MIN_WALK_WEIGHT, (need / max(float(req.time_budget_min), 1.0)) ** 2))
    return w * float(preset.get("walk_scale", 1.0))


def _route_minutes(seq: list[Candidate], req: WalkRequest, provider: RoutingProvider,
                   strict: bool = True) -> Optional[float]:
    """Estimated length of visiting ``seq`` in this order (walk + waits + visits, plus the walk
    back for a loop), or None when a stop would be closed when the route gets there or a leg
    is longer than ``req.max_leg_min``. ``strict=False`` ignores hours and the leg cap (just time)."""
    prev = req.start if req.shape != "free" else None
    cap = req.max_leg_min if (strict and req.max_leg_min is not None) else float("inf")
    t = 0.0
    for c in seq:
        if prev is not None:
            leg = provider.walk_minutes(prev, c.coord())
            if leg > cap:
                return None
            t += leg
        if strict and req.start_week_min is not None:
            w = visit_wait(c.open_hours, req.start_week_min + t, c.dwell_min, req.max_wait_min)
            if w is None:
                return None
            t += w
        t += c.dwell_min
        prev = c.coord()
    if req.shape == "loop" and req.start is not None and prev is not None:
        leg = provider.walk_minutes(prev, req.start)
        if leg > cap:
            return None
        t += leg
    return t


def _fill_extras(chosen: list[Candidate], extras: list[Candidate], req: WalkRequest,
                 provider: RoutingProvider, target_min: float, max_pool: int = 80,
                 walk_weight: float = 1.0) -> list[Candidate]:
    """Orienteering-style fill of a roomy window: repeatedly insert the optional stop with the
    best value per added minute at its best position (the requested slots keep their order),
    while the route stays within ``target_min``, no leg exceeds ``req.max_leg_min`` and every
    stop is open on arrival.

    value = interest ** EXTRA_INTEREST_POWER (repeats of a kind are fine — it's what the walk
    passes by);
    added minutes = the visit + ``walk_weight`` × the extra walking, so in a roomy window a
    highlight a few km away can beat a so-so place next door."""
    seq = list(chosen)
    used = {c.place_id for c in seq}
    pool: list[Candidate] = []
    for c in sorted(extras, key=lambda c: -c.interest):
        if c.interest < EXTRA_MIN_INTEREST:
            break
        if c.place_id not in used and len(pool) < max_pool:
            pool.append(c)
            used.add(c.place_id)
    total = _route_minutes(seq, req, provider)
    if total is None:
        return seq
    while pool and total < target_min:
        best = None
        for c in pool:
            if total + c.dwell_min > target_min:
                continue
            for i in range(len(seq) + 1):
                t = _route_minutes(seq[:i] + [c] + seq[i:], req, provider)
                if t is None or t > target_min + 1e-6:
                    continue
                value = c.interest ** EXTRA_INTEREST_POWER + 0.01
                cost = c.dwell_min + walk_weight * max(t - total - c.dwell_min, 0.0)
                score = value / max(cost, 1.0)
                if best is None or score > best[0]:
                    best = (score, i, c, t)
        if best is None:
            break
        _, i, c, total = best
        seq.insert(i, c)
        pool.remove(c)
    return seq


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def plan_walk(req: WalkRequest) -> WalkPlan:
    """Build an ordered walking route from interest-matched candidates.

    ``mode="slots"`` fills each ordered activity slot with its best-value place
    (beam search); with ``fit_budget`` the route must fit ``time_budget_min`` and slots
    that cannot fit are left empty (``plan.dropped_slots``); with ``start_week_min`` every
    stop must be open for its whole visit (``auto`` ignores hours). ``mode="auto"`` greedily
    selects a budget-fitting subset and tours it (nearest-neighbour + 2-opt).
    Style/shape are honoured in both.

    ``must_visit`` places are always in the route (``best_insertion``: where they cost the least
    time and are open; the slots get the rest of the window).

    Slots mode uses the time window, not just fits into it: the cost of a walking minute
    shrinks as the window gets roomier (``_adaptive_walk_weight``) so the fixed slots may spread
    over the search area, then optional ``extra`` candidates are inserted
    (``_fill_extras``) until the style's ``fill`` share of the window is used — "max" packs in
    places, "chill" stays longer at fewer, "scenic" walks more between green stops."""
    provider = req.provider
    preset = STYLE_PRESETS.get(req.style, STYLE_PRESETS["max"])
    interest_weight = req.interest_weight if req.interest_weight is not None else preset["interest_weight"]
    max_stops = req.max_stops if req.max_stops is not None else preset["max_stops"]

    if req.mode == "slots":
        scale = float(preset.get("dwell_scale", 1.0))
        cands = [replace(c, dwell_min=min(c.dwell_min, EXTRA_DWELL_MIN)) if c.extra
                 else c if c.dwell_fixed else replace(c, dwell_min=c.dwell_min * scale) for c in req.candidates]
        by_slot = _group_by_slot([c for c in cands if not c.extra], req.n_slots)
        extras = [c for c in cands if c.extra]
        walk_weight = req.walk_weight if req.walk_weight is not None else _adaptive_walk_weight(by_slot, req, preset)
        kw = dict(start_week_min=req.start_week_min, max_wait_min=req.max_wait_min, walk_weight=walk_weight)
        # the user's must-visit places come first: the slots get the window minus their time
        musts = [replace(c, pinned=True, extra=False) for c in req.must_visit]
        must_min = sum(c.dwell_min + MUST_VISIT_WALK_MIN for c in musts)
        chosen = _solve_slots(by_slot, req.start, req.shape, interest_weight, provider,
                              time_budget_min=max(req.time_budget_min - must_min, 0.0) if req.fit_budget else None,
                              max_leg_min=req.max_leg_min if req.fit_budget else None, **kw)
        if not chosen and req.fit_budget and not musts:
            # not even one stop fits the window -> show the best full plan, flagged over budget
            chosen = _solve_slots(by_slot, req.start, req.shape, interest_weight, provider, **kw)
        filled = {c.slot for c in chosen}
        for m in musts:
            if m.place_id in {c.place_id for c in chosen}:
                chosen = [replace(c, pinned=True) if c.place_id == m.place_id else c for c in chosen]
            else:
                chosen = best_insertion(chosen, m, req)
        if req.fill_window and req.fit_budget and extras and chosen:
            chosen = _fill_extras(chosen, extras, req, provider, preset.get("fill", 0.0) * req.time_budget_min,
                                  walk_weight=walk_weight)
        plan = _assemble_plan(chosen, req, provider)
        plan.dropped_slots = [i for i, s in enumerate(by_slot) if s and i not in filled]
        return plan

    return _assemble_plan(_auto_select(req, provider, max_stops), req, provider)


def best_insertion(seq: list[Candidate], c: Candidate, req: WalkRequest) -> list[Candidate]:
    """``seq`` with ``c`` inserted where the route gets the shortest while every stop is open on
    arrival and no leg is too long; if no position satisfies that, where it is shortest anyway
    (the plan then flags the stop — ``Stop.hours_ok`` — instead of refusing the user's place)."""
    best = None
    for strict in (True, False):
        for i in range(len(seq) + 1):
            trial = seq[:i] + [c] + seq[i:]
            t = _route_minutes(trial, req, req.provider, strict=strict)
            if t is not None and (best is None or t < best[0]):
                best = (t, trial)
        if best is not None:
            return best[1]
    return list(seq) + [c]


def plan_variants(req: WalkRequest, n: int = 3) -> list[WalkPlan]:
    """Up to ``n`` DIFFERENT routes for the same request, best first. Each next variant plans with
    the places earlier variants used made less attractive (interest × VARIANT_REUSE_FACTOR), so it
    prefers other places but may still reuse one when nothing else fits; the user's must-visit
    places are never penalised. Identical routes are dropped."""
    plans: list[WalkPlan] = []
    seen: set[tuple] = set()
    used: set[str] = set()
    for _ in range(max(1, int(n))):
        cands = [replace(c, interest=c.interest * VARIANT_REUSE_FACTOR) if c.place_id in used else c
                 for c in req.candidates]
        plan = plan_walk(replace(req, candidates=cands))
        key = tuple(s.place_id for s in plan.stops)
        if plan.stops and key not in seen:
            plans.append(plan)
            seen.add(key)
        used |= {s.place_id for s in plan.stops if not s.pinned}
    return plans


def plan_sequence(seq: list[Candidate], req: WalkRequest) -> WalkPlan:
    """Schedule and route the stops EXACTLY in the given order — the user rearranged them by hand.

    Nothing is dropped or swapped: a stop that would be closed at its new time is kept and flagged
    (``Stop.hours_ok = False``, the user may know better — e.g. open late today), a wait before
    opening is scheduled as usual, and ``over_budget`` says if the new order overruns the window."""
    return _assemble_plan(list(seq), req, req.provider)


# ---------------------------------------------------------------------------
# Hand-off to a real navigator (Google Maps / Apple Maps deep links)
# ---------------------------------------------------------------------------
# The dashboard map is for judging the plan; on foot the user needs turn-by-turn navigation.
# Google Maps URLs (https://developers.google.com/maps/documentation/urls/get-started) accept a
# walking route with origin + destination + up to GMAPS_MAX_WAYPOINTS intermediate stops, so a plan
# is emitted as one link when it fits and as consecutive "parts" otherwise (part N ends where
# part N+1 begins). `place_ids` (Google ChIJ… ids, keyed by our place_id/cid) make the pins carry
# the venue name instead of "dropped pin"; coordinates are always sent too, as the URL spec
# requires. Apple Maps takes only one destination per link, so it gets per-leg links.
GMAPS_MAX_WAYPOINTS = 9


def _fmt_pt(lat: float, lon: float) -> str:
    return f"{lat:.6f},{lon:.6f}"


def _gmaps_dir_url(points: list, place_ids: list) -> str:
    """points = [(lat, lon), ...] (>= 2), place_ids = parallel list (None where unknown)."""
    from urllib.parse import quote
    origin, dest, mids = points[0], points[-1], points[1:-1]
    url = ("https://www.google.com/maps/dir/?api=1&travelmode=walking"
           f"&origin={_fmt_pt(*origin)}&destination={_fmt_pt(*dest)}")
    if place_ids[-1]:
        url += f"&destination_place_id={place_ids[-1]}"
    if place_ids[0]:
        url += f"&origin_place_id={place_ids[0]}"
    if mids:
        url += "&waypoints=" + quote("|".join(_fmt_pt(*p) for p in mids), safe=",")
        mid_ids = place_ids[1:-1]
        if all(mid_ids):
            url += "&waypoint_place_ids=" + quote("|".join(mid_ids), safe=",")
    return url


def navigation_links(plan: "WalkPlan", start=None, shape: str = "one_way",
                     place_ids: Optional[dict] = None, start_label: str = "Старт") -> dict:
    """Deep links for walking the plan.

    Returns {"google": [url, ...] (whole route, split into parts if > GMAPS_MAX_WAYPOINTS),
             "legs": [{"from": name, "to": name, "google": url, "apple": url}, ...]}
    — both empty for a plan without stops. `start` (lat, lon) is prepended as the origin when
    given; for shape="loop" it is also the final destination. `place_ids` maps our place_id ->
    Google place id (optional).
    `start_label` names the start anchor in ``legs`` (the dashboard's "Старт" by default)."""
    if not plan.stops:                  # nothing to walk to: no start -> start link for an empty loop
        return {"google": [], "legs": []}
    pid = place_ids or {}
    pts = [((start_label, start[0], start[1], None))] if start else []
    pts += [(s.name, s.lat, s.lon, pid.get(s.place_id)) for s in plan.stops]
    if start and shape == "loop":
        pts.append((start_label, start[0], start[1], None))
    if len(pts) < 2:
        return {"google": [], "legs": []}

    # whole route, chunked so each part has <= GMAPS_MAX_WAYPOINTS intermediate stops
    parts = []
    i = 0
    while i < len(pts) - 1:
        chunk = pts[i:i + GMAPS_MAX_WAYPOINTS + 2]
        parts.append(_gmaps_dir_url([(p[1], p[2]) for p in chunk], [p[3] for p in chunk]))
        i += GMAPS_MAX_WAYPOINTS + 1          # next part starts at this part's destination
    legs = []
    for a, b in zip(pts, pts[1:]):
        legs.append({
            "from": a[0], "to": b[0],
            "google": _gmaps_dir_url([(a[1], a[2]), (b[1], b[2])], [a[3], b[3]]),
            "apple": f"https://maps.apple.com/?saddr={_fmt_pt(a[1], a[2])}&daddr={_fmt_pt(b[1], b[2])}&dirflg=w",
        })
    return {"google": parts, "legs": legs}
