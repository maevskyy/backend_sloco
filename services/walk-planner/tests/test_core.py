"""Unit tests for the Walk Planner core (walk_planner/core.py; formerly
recommendation_system/ai_location_recommender/walk_planner.py).

All tests are offline (the straight-line ``RoutingProvider`` estimate, or fake sessions) with
synthetic coordinates whose optimal route is known by construction. conftest.py puts the
project root on sys.path. The routing chain itself is tested in test_routing.py.
"""

import math
from dataclasses import replace

import pytest

from walk_planner import (
    Candidate,
    RoutingProvider,
    WalkRequest,
    _two_opt,
    dwell_for,
    haversine_km,
    plan_walk,
)

# A small patch of Bucharest; ~0.001 deg lat ~= 111 m.
BASE_LAT, BASE_LON = 44.4300, 26.1000


def _c(pid, dlat, dlon, slot=0, theme="", interest=0.0, dwell=10.0, subtype=""):
    return Candidate(place_id=pid, name=pid, lat=BASE_LAT + dlat, lon=BASE_LON + dlon,
                     slot=slot, theme=theme, subtype=subtype, interest=interest, dwell_min=dwell)


# --------------------------------------------------------------------------- #
# Provider / helpers
# --------------------------------------------------------------------------- #
def test_haversine_and_detour_consistency():
    p = RoutingProvider(walk_kmh=4.5, detour=1.35)
    a, b = (BASE_LAT, BASE_LON), (BASE_LAT + 0.01, BASE_LON)
    expected = haversine_km(*a, *b) * 1.35 / 4.5 * 60.0
    assert p.walk_minutes(a, b) == pytest.approx(expected, rel=1e-9)
    # detour inflates over the straight-line time
    assert RoutingProvider(detour=2.0).walk_minutes(a, b) > RoutingProvider(detour=1.0).walk_minutes(a, b)


def test_dwell_lookup_prefers_subtype_then_theme_then_default():
    assert dwell_for("nature_outdoors", "viewpoint") == 15.0   # subtype wins
    assert dwell_for("religious_sights") == 20.0               # theme
    assert dwell_for("totally_unknown") == pytest.approx(40.0)  # default


# --------------------------------------------------------------------------- #
# Fixed-order slots (exact DP)
# --------------------------------------------------------------------------- #
def test_slots_one_stop_per_slot_in_order():
    cands = [
        _c("s0a", 0.001, 0.001, slot=0, interest=0.5),
        _c("s1a", 0.002, 0.002, slot=1, interest=0.5),
        _c("s2a", 0.003, 0.003, slot=2, interest=0.5),
    ]
    plan = plan_walk(WalkRequest(candidates=cands, mode="slots", n_slots=3,
                                 start=(BASE_LAT, BASE_LON), shape="one_way",
                                 time_budget_min=600))
    assert [s.slot for s in plan.stops] == [0, 1, 2]
    assert [s.order for s in plan.stops] == [0, 1, 2]


def test_slots_dp_prefers_nearby_candidate_when_interest_is_equal():
    # slot 0 has a near option and a far option with the SAME interest -> pick near.
    cands = [
        _c("near", 0.001, 0.001, slot=0, interest=0.6),
        _c("far", 0.050, 0.050, slot=0, interest=0.6),
        _c("sight", 0.002, 0.002, slot=1, interest=0.6),
    ]
    plan = plan_walk(WalkRequest(candidates=cands, mode="slots", n_slots=2,
                                 start=(BASE_LAT, BASE_LON), style="max", time_budget_min=600))
    assert plan.stops[0].place_id == "near"


def test_slots_dp_prefers_higher_interest_when_travel_is_equal():
    # two slot-0 options essentially equidistant to start and to the sight, but
    # very different interest -> the interest term must break the tie toward "rich".
    cands = [
        _c("dull", 0.0010, 0.0010, slot=0, interest=0.10),
        _c("rich", 0.0011, 0.0011, slot=0, interest=0.95),
        _c("sight", 0.0020, 0.0020, slot=1, interest=0.5),
    ]
    plan = plan_walk(WalkRequest(candidates=cands, mode="slots", n_slots=2,
                                 start=(BASE_LAT, BASE_LON), style="chill", time_budget_min=600))
    assert plan.stops[0].place_id == "rich"


def test_loop_returns_to_start_and_counts_return_leg():
    cands = [
        _c("a", 0.002, 0.000, slot=0, interest=0.5, dwell=10),
        _c("b", 0.002, 0.004, slot=1, interest=0.5, dwell=10),
    ]
    start = (BASE_LAT, BASE_LON)
    loop = plan_walk(WalkRequest(candidates=cands, mode="slots", n_slots=2, start=start,
                                 shape="loop", time_budget_min=600))
    one_way = plan_walk(WalkRequest(candidates=cands, mode="slots", n_slots=2, start=start,
                                    shape="one_way", time_budget_min=600))
    # loop has an extra return segment (start, s0, s1, start) => 3 legs vs 2
    assert len(loop.segments) == len(one_way.segments) + 1
    assert loop.segments[-1].to_order == -1                 # last leg returns to the start anchor
    assert loop.total_walk_min > one_way.total_walk_min     # return leg adds walking


def test_over_budget_is_flagged_with_a_note():
    cands = [_c("a", 0.01, 0.01, slot=0, interest=0.5, dwell=90),
             _c("b", 0.02, 0.02, slot=1, interest=0.5, dwell=90)]
    plan = plan_walk(WalkRequest(candidates=cands, mode="slots", n_slots=2,
                                 start=(BASE_LAT, BASE_LON), time_budget_min=5))
    assert plan.over_budget is True
    assert plan.note                                        # non-empty explanation
    assert plan.total_time_min == pytest.approx(plan.total_walk_min + plan.total_dwell_min)


def test_arrival_times_are_monotonic_and_include_dwell():
    cands = [_c("a", 0.002, 0.000, slot=0, interest=0.5, dwell=30),
             _c("b", 0.002, 0.006, slot=1, interest=0.5, dwell=20)]
    plan = plan_walk(WalkRequest(candidates=cands, mode="slots", n_slots=2,
                                 start=(BASE_LAT, BASE_LON), time_budget_min=600))
    assert plan.stops[0].depart_min == pytest.approx(plan.stops[0].arrival_min + 30)
    # arrival at stop 1 = depart of stop 0 + the walking leg between them
    assert plan.stops[1].arrival_min > plan.stops[0].depart_min


# --------------------------------------------------------------------------- #
# Free order (2-opt) + auto selection
# --------------------------------------------------------------------------- #
def test_two_opt_uncrosses_a_closed_tour():
    prov = RoutingProvider()
    # unit square corners; the order [0,2,1,3] crosses, [0,1,2,3] is the perimeter.
    coords = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]

    def tour_len(o, closed=True):
        t = sum(prov.walk_minutes(coords[o[i]], coords[o[i + 1]]) for i in range(len(o) - 1))
        return t + (prov.walk_minutes(coords[o[-1]], coords[o[0]]) if closed else 0.0)

    crossed = [0, 2, 1, 3]
    fixed = _two_opt(crossed, coords, prov, closed=True, fix_first=True)
    assert tour_len(fixed) < tour_len(crossed)


def test_auto_mode_respects_time_budget():
    cands = [_c(f"p{i}", 0.001 * i, 0.001 * i, theme="culture_sights", interest=0.9 - 0.01 * i, dwell=20)
             for i in range(12)]
    start = (BASE_LAT, BASE_LON)
    tight = plan_walk(WalkRequest(candidates=cands, mode="auto", start=start,
                                  shape="one_way", style="max", time_budget_min=90))
    assert tight.total_time_min <= 90 + 1e-6
    assert 0 < len(tight.stops) < len(cands)
    # a much larger budget should fit at least as many stops
    roomy = plan_walk(WalkRequest(candidates=cands, mode="auto", start=start,
                                  shape="one_way", style="max", time_budget_min=600))
    assert len(roomy.stops) >= len(tight.stops)


def test_auto_scenic_boosts_green_places():
    # equal base interest, but scenic style should surface the park/waterfront ones.
    cands = [
        _c("mall1", 0.001, 0.001, theme="shopping_souvenirs", interest=0.6, dwell=20),
        _c("park1", 0.001, 0.002, theme="nature_outdoors", subtype="park", interest=0.6, dwell=20),
        _c("mall2", 0.002, 0.001, theme="shopping_souvenirs", interest=0.6, dwell=20),
    ]
    plan = plan_walk(WalkRequest(candidates=cands, mode="auto", start=(BASE_LAT, BASE_LON),
                                 style="scenic", time_budget_min=45, max_stops=1))
    assert plan.stops and plan.stops[0].place_id == "park1"


# --------------------------------------------------------------------------- #
# Distinctness — a place must never be used twice in one route
# --------------------------------------------------------------------------- #
def test_slots_never_repeats_the_same_place():
    # "shared" qualifies for BOTH slots (as happens when a venue matches several keywords)
    # and is high-value + central, so a naive DP would place it twice (0-cost self-hop).
    cands = [
        _c("shared", 0.0010, 0.0010, slot=0, interest=0.95),
        _c("alt0", 0.0300, 0.0300, slot=0, interest=0.30),
        _c("shared", 0.0010, 0.0010, slot=1, interest=0.95),
        _c("alt1", 0.0020, 0.0020, slot=1, interest=0.60),
    ]
    plan = plan_walk(WalkRequest(candidates=cands, mode="slots", n_slots=2,
                                 start=(BASE_LAT, BASE_LON), time_budget_min=600))
    ids = [s.place_id for s in plan.stops]
    assert len(ids) == len(set(ids))       # no duplicate stop
    assert len(plan.stops) == 2            # both slots still filled (with distinct places)
    assert "shared" in ids                 # the high-value place is used exactly once


def test_slots_skips_a_slot_when_its_only_candidate_is_already_used():
    # slot 1's ONLY option is the same place chosen for slot 0 -> skip it, don't duplicate.
    cands = [
        _c("shared", 0.001, 0.001, slot=0, interest=0.9),
        _c("shared", 0.001, 0.001, slot=1, interest=0.9),
    ]
    plan = plan_walk(WalkRequest(candidates=cands, mode="slots", n_slots=2,
                                 start=(BASE_LAT, BASE_LON), time_budget_min=600))
    assert [s.place_id for s in plan.stops] == ["shared"]   # one stop, no repeat


def test_auto_never_repeats_the_same_place():
    cands = [_c("dup", 0.001, 0.001, theme="cafe", interest=0.9, dwell=15) for _ in range(3)]
    cands += [_c("other", 0.002, 0.002, theme="cafe", interest=0.5, dwell=15)]
    plan = plan_walk(WalkRequest(candidates=cands, mode="auto", start=(BASE_LAT, BASE_LON),
                                 time_budget_min=600))
    ids = [s.place_id for s in plan.stops]
    assert len(ids) == len(set(ids))       # "dup" appears at most once


# --- navigation deep links -------------------------------------------------------------------

def _plan_with_n_stops(n):
    from walk_planner import Stop, WalkPlan
    stops = [Stop(order=i, place_id=f"p{i}", name=f"P{i}", lat=44.43 + i * 0.001, lon=26.10, slot=i,
                  theme="cafe", interest=0.5, dwell_min=30, arrival_min=0, depart_min=0) for i in range(n)]
    return WalkPlan(stops=stops, segments=[], total_time_min=0, total_walk_min=0, total_dwell_min=0,
                    total_distance_km=0, over_budget=False)


def test_navigation_single_link_and_loop_returns_to_start():
    from walk_planner import navigation_links
    nav = navigation_links(_plan_with_n_stops(4), start=(44.40, 26.09), shape="loop", place_ids={"p1": "ChIJx"})
    assert len(nav["google"]) == 1
    url = nav["google"][0]
    assert "travelmode=walking" in url and "origin=44.400000,26.090000" in url
    assert "destination=44.400000,26.090000" in url          # loop -> back to start
    assert url.count("%7C") == 3                                 # 4 stops as waypoints, 3 separators
    assert "waypoint_place_ids" not in url                      # only 1 of 4 ids known -> coords only
    assert len(nav["legs"]) == 5 and nav["legs"][0]["from"] == "Старт" and nav["legs"][-1]["to"] == "Старт"


def test_navigation_of_an_empty_plan_has_no_links():
    # v0 returned a phantom start -> start leg (and a Google link) for an empty loop plan
    from walk_planner import navigation_links
    empty = _plan_with_n_stops(0)
    for shape, start in (("loop", (44.40, 26.09)), ("one_way", (44.40, 26.09)), ("free", None), ("loop", None)):
        assert navigation_links(empty, start=start, shape=shape) == {"google": [], "legs": []}
    one = navigation_links(_plan_with_n_stops(1), start=(44.40, 26.09), shape="loop")
    assert len(one["google"]) == 1 and [(leg["from"], leg["to"]) for leg in one["legs"]] == [("Старт", "P0"),
                                                                                              ("P0", "Старт")]


def test_navigation_splits_long_routes_into_contiguous_parts():
    from walk_planner import navigation_links, GMAPS_MAX_WAYPOINTS
    nav = navigation_links(_plan_with_n_stops(14), start=(44.40, 26.09), shape="one_way")
    assert len(nav["google"]) == 2
    # part 1: origin + 9 waypoints + destination(=stop 9); part 2 starts at that destination
    assert nav["google"][0].count("%7C") == GMAPS_MAX_WAYPOINTS - 1
    d1 = nav["google"][0].split("destination=")[1].split("&")[0]
    o2 = nav["google"][1].split("origin=")[1].split("&")[0]
    assert d1 == o2


# --- time window (slots mode fits the budget) -------------------------------------------------

def test_slots_prefers_a_nearer_place_when_the_far_one_breaks_the_window():
    # "far" is so much more interesting that it wins without a time limit, but walking there
    # and back does not fit 20 min -> the planner must take "near" and stay inside the window.
    cands = [_c("far", 0.004, 0.000, slot=0, interest=1.0, dwell=10),
             _c("near", 0.001, 0.000, slot=0, interest=0.1, dwell=10)]
    kw = dict(candidates=cands, mode="slots", n_slots=1, start=(BASE_LAT, BASE_LON),
              shape="loop", style="chill", time_budget_min=20)
    free = plan_walk(WalkRequest(fit_budget=False, **kw))
    assert free.stops[0].place_id == "far" and free.over_budget     # sanity: far wins untimed
    fit = plan_walk(WalkRequest(**kw))
    assert fit.stops[0].place_id == "near"
    assert not fit.over_budget and fit.total_time_min <= 20 + 1e-6


def test_slots_drops_a_slot_that_does_not_fit_and_reports_it():
    cands = [_c("a", 0.001, 0.000, slot=0, interest=0.5, dwell=60),
             _c("b", 0.001, 0.001, slot=1, interest=0.5, dwell=60)]
    plan = plan_walk(WalkRequest(candidates=cands, mode="slots", n_slots=2,
                                 start=(BASE_LAT, BASE_LON), shape="loop", time_budget_min=90))
    assert len(plan.stops) == 1 and len(plan.dropped_slots) == 1
    assert plan.dropped_slots[0] != plan.stops[0].slot
    assert not plan.over_budget


def test_slots_nothing_fits_returns_full_plan_flagged():
    cands = [_c("a", 0.001, 0.000, slot=0, interest=0.5, dwell=60),
             _c("b", 0.001, 0.001, slot=1, interest=0.5, dwell=60)]
    plan = plan_walk(WalkRequest(candidates=cands, mode="slots", n_slots=2,
                                 start=(BASE_LAT, BASE_LON), time_budget_min=30))
    assert len(plan.stops) == 2 and plan.dropped_slots == [] and plan.over_budget


def test_reach_radius_shrinks_with_visits_and_halves_for_a_loop():
    from walk_planner import reach_radius_km
    one_way = reach_radius_km(240, 120, shape="one_way")
    assert reach_radius_km(240, 120, shape="loop") == pytest.approx(one_way / 2)
    assert reach_radius_km(240, 180, shape="one_way") < one_way          # more visiting, less walking
    # 120 min of walking at 4.5 km/h, deflated by the 1.35 street detour
    assert one_way == pytest.approx(120 / 60 * 4.5 / 1.35)
    assert reach_radius_km(60, 500, shape="one_way") > 0                 # over-packed -> still a small area


# --- opening hours ----------------------------------------------------------------------------

def _tt(**days):
    """DataForSEO-style timetable: _tt(monday=("10:00", "18:00"), ...)."""
    def hm(s):
        h, m = s.split(":")
        return {"hour": int(h), "minute": int(m)}
    return {d: [{"open": hm(o), "close": hm(c)}] for d, (o, c) in days.items()}


MON = 0            # week minutes of Monday 00:00
FRI = 4 * 1440
SUN = 6 * 1440


def test_timetable_parsing_overnight_24h_and_closed_days():
    from walk_planner import week_intervals_from_timetable as wi
    assert wi(None) is None and wi({}) is None                             # unknown
    assert wi(_tt(monday=("10:00", "18:00"))) == [[600, 1080]]            # other days: closed
    assert wi(_tt(friday=("18:00", "02:00"))) == [[FRI + 1080, FRI + 1560]]  # past midnight
    assert wi(_tt(monday=("10:00", "00:00"))) == [[600, 1440]]            # 00:00 = end of day
    assert wi(_tt(monday=("00:00", "00:00"))) == [[0, 1440]]              # 24 h


def test_visit_wait_open_wait_closed_and_week_wrap():
    from walk_planner import visit_wait, week_intervals_from_timetable as wi
    museum = wi(_tt(monday=("10:00", "18:00")))
    assert visit_wait(museum, MON + 11 * 60, 60) == 0                     # open, whole visit fits
    assert visit_wait(museum, MON + 9 * 60 + 40, 60, max_wait=30) == 20   # wait 20 min at the door
    assert visit_wait(museum, MON + 9 * 60, 60, max_wait=30) is None      # would wait an hour
    assert visit_wait(museum, MON + 17 * 60 + 30, 60) is None             # closes mid-visit
    assert visit_wait(None, MON, 60) == 0                                  # unknown hours: open
    bar = wi(_tt(sunday=("20:00", "03:00")))                               # Sunday night into Monday
    assert visit_wait(bar, MON + 60, 60) == 0                              # Mon 01:00 still open
    assert visit_wait(bar, 7 * 1440 + 60, 60) == 0                         # same, one week later


def test_slots_skip_a_place_closed_at_arrival_for_an_open_one():
    start = (BASE_LAT, BASE_LON)
    from walk_planner import week_intervals_from_timetable as wi
    evening = wi(_tt(monday=("18:00", "23:00")))
    cands = [_c("famous_but_closed", 0.001, 0.000, slot=0, interest=1.0, dwell=30),
             _c("open_now", 0.002, 0.000, slot=0, interest=0.2, dwell=30)]
    cands[0].open_hours = evening
    kw = dict(candidates=cands, mode="slots", n_slots=1, start=start, time_budget_min=240)
    assert plan_walk(WalkRequest(**kw)).stops[0].place_id == "famous_but_closed"      # no clock
    plan = plan_walk(WalkRequest(start_week_min=MON + 10 * 60, **kw))                 # Monday 10:00
    assert plan.stops[0].place_id == "open_now"
    evening_plan = plan_walk(WalkRequest(start_week_min=MON + 19 * 60, **kw))        # Monday 19:00
    assert evening_plan.stops[0].place_id == "famous_but_closed" and evening_plan.stops[0].hours_ok


def test_waiting_for_opening_is_scheduled_and_counted():
    from walk_planner import week_intervals_from_timetable as wi
    c = _c("museum", 0.001, 0.000, slot=0, interest=0.8, dwell=60)
    c.open_hours = wi(_tt(monday=("10:00", "18:00")))
    plan = plan_walk(WalkRequest(candidates=[c], mode="slots", n_slots=1, start=(BASE_LAT, BASE_LON),
                                 shape="one_way", time_budget_min=240, start_week_min=MON + 9 * 60 + 50))
    s = plan.stops[0]
    assert s.wait_min > 0 and s.hours_ok
    assert s.arrival_min + s.wait_min == pytest.approx(10)                  # visit starts at 10:00
    assert s.depart_min == pytest.approx(70)
    assert plan.total_time_min == pytest.approx(plan.total_walk_min + plan.total_dwell_min + plan.total_wait_min)


# --- using a roomy window: cheaper walking + optional stops -----------------------------------

def test_roomy_window_reaches_a_better_place_further_out():
    # "far" is better but ~22 min further; in a tight window hug the start, in a long one go there
    cands = [_c("near", 0.001, 0.000, slot=0, interest=0.3, dwell=30),
             _c("far", 0.012, 0.000, slot=0, interest=0.9, dwell=30)]
    kw = dict(candidates=cands, mode="slots", n_slots=1, start=(BASE_LAT, BASE_LON), shape="one_way")
    assert plan_walk(WalkRequest(time_budget_min=60, **kw)).stops[0].place_id == "near"
    assert plan_walk(WalkRequest(time_budget_min=600, **kw)).stops[0].place_id == "far"


def test_extras_fill_a_long_window_but_not_a_short_one():
    core = [_c("s0", 0.002, 0.000, slot=0, interest=0.5, dwell=30),
            _c("s1", 0.004, 0.000, slot=1, interest=0.5, dwell=30)]
    extras = [_c(f"x{i}", 0.001 * i, 0.001 * (i % 3), slot=0, interest=0.8, dwell=20) for i in range(1, 9)]
    for e in extras:
        e.extra = True
    kw = dict(candidates=core + extras, mode="slots", n_slots=2, start=(BASE_LAT, BASE_LON), shape="loop")
    short = plan_walk(WalkRequest(time_budget_min=90, **kw))
    assert [s.place_id for s in short.stops] == ["s0", "s1"]               # no room -> slots only
    long = plan_walk(WalkRequest(time_budget_min=300, style="max", **kw))
    ids = [s.place_id for s in long.stops]
    assert sum(s.extra for s in long.stops) >= 3                          # window got used
    assert ids.index("s0") < ids.index("s1")                              # requested order kept
    assert len(ids) == len(set(ids))
    assert long.total_time_min <= 0.95 * 300 + 1e-6
    assert plan_walk(WalkRequest(time_budget_min=300, fill_window=False, **kw)).stops.__len__() == 2


def test_chill_style_stays_longer():
    c = [_c("a", 0.001, 0.0, slot=0, interest=0.5, dwell=40)]
    kw = dict(candidates=c, mode="slots", n_slots=1, start=(BASE_LAT, BASE_LON), time_budget_min=300)
    assert plan_walk(WalkRequest(style="chill", **kw)).stops[0].dwell_min == pytest.approx(52)
    assert plan_walk(WalkRequest(style="max", **kw)).stops[0].dwell_min == pytest.approx(40)


def test_ors_route_legs_splits_one_response_into_legs():
    # ORSProvider moved to routing.py (strict ORS router + the chain); the old constructor keeps
    # working, now on the heigit host. The HTTP session is injected instead of patching `requests`.
    from walk_planner import ORSProvider
    calls = []

    class _Resp:
        status_code = 200
        headers: dict = {}

        def json(self):
            geom = [[26.10, 44.43], [26.101, 44.431], [26.102, 44.432], [26.103, 44.433], [26.104, 44.434]]
            return {"features": [{"geometry": {"coordinates": geom},
                                  "properties": {"way_points": [0, 2, 4],
                                                 "segments": [{"duration": 120, "distance": 300},
                                                              {"duration": 60, "distance": 150}]}}]}

    class _Session:
        def post(self, url, **kw):
            calls.append((url, kw))
            return _Resp()

    legs = ORSProvider(api_key="test", session=_Session()).route_legs(
        [(44.43, 26.10), (44.432, 26.102), (44.434, 26.104)])
    assert len(calls) == 1                                        # one request for the whole path
    assert calls[0][0] == "https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson"
    assert [len(l["geometry"]) for l in legs] == [3, 3]           # split at the waypoint index
    assert legs[0]["duration_min"] == pytest.approx(2) and legs[1]["distance_km"] == pytest.approx(0.15)
    assert {(l["quality"], l["provider"]) for l in legs} == {("streets", "ors")}


# --- opening hours depend on WHEN the route reaches a place -----------------------------------
SAT = 5 * 1440


def _hours(**days):
    from walk_planner import week_intervals_from_timetable
    return week_intervals_from_timetable(_tt(**days))


def _all_visits_inside_hours(plan, t0):
    for s in plan.stops:
        assert s.hours_ok is not False, s.place_id
    return True


def _cafe_walk():
    """Saturday 16:00-22:00: a 2 h sight, then coffee. The better cafe closes at 18:00."""
    sight = _c("sight", 0.002, 0.000, slot=0, interest=0.6, dwell=120)
    early = _c("cafe_till_18", 0.001, 0.001, slot=1, interest=0.95, dwell=30)
    early.open_hours = _hours(saturday=("08:00", "18:00"))
    late = _c("cafe_late", 0.003, 0.001, slot=1, interest=0.3, dwell=30)
    late.open_hours = _hours(saturday=("08:00", "23:00"))
    return plan_walk(WalkRequest(candidates=[sight, early, late], mode="slots", n_slots=2,
                                 start=(BASE_LAT, BASE_LON), shape="one_way", time_budget_min=360,
                                 start_week_min=SAT + 16 * 60))


def test_fixed_order_swaps_a_cafe_that_would_be_closed_by_the_time_we_get_there():
    plan = _cafe_walk()
    assert [s.place_id for s in plan.stops] == ["sight", "cafe_late"]   # coffee is reached ~18:05
    assert plan.stops[1].arrival_min > 120                               # after the 2 h sight
    assert _all_visits_inside_hours(plan, SAT + 16 * 60)


def test_same_cafe_is_used_when_its_slot_comes_before_closing_but_not_after():
    early = _c("cafe_till_18", 0.001, 0.001, slot=1, interest=0.5, dwell=30)
    early.open_hours = _hours(saturday=("08:00", "18:00"))
    sight = _c("sight", 0.002, 0.000, slot=0, interest=0.95, dwell=120)
    kw = dict(mode="slots", n_slots=2, start=(BASE_LAT, BASE_LON), shape="one_way",
              time_budget_min=360, start_week_min=SAT + 16 * 60)
    # coffee slot AFTER the 2 h sight: reached ~18:05 -> the only cafe is closed -> slot dropped
    late = plan_walk(WalkRequest(candidates=[sight, early], **kw))
    assert [s.place_id for s in late.stops] == ["sight"] and late.dropped_slots == [1]
    # coffee slot FIRST (the user put it there): same cafe, reached ~16:03 -> in the route
    early0, sight1 = replace(early, slot=0), replace(sight, slot=1)
    first = plan_walk(WalkRequest(candidates=[early0, sight1], **kw))
    assert [s.place_id for s in first.stops] == ["cafe_till_18", "sight"] and first.stops[0].hours_ok


def test_manual_order_keeps_a_stop_that_may_be_closed_and_flags_it():
    from walk_planner import plan_sequence
    cafe = _c("cafe_till_18", 0.001, 0.001, slot=0, interest=0.5, dwell=30)
    cafe.open_hours = _hours(saturday=("08:00", "18:00"))
    sight = _c("sight", 0.002, 0.000, slot=1, interest=0.95, dwell=120)
    req = WalkRequest(candidates=[], start=(BASE_LAT, BASE_LON), shape="one_way", time_budget_min=360,
                      start_week_min=SAT + 16 * 60)
    ok = plan_sequence([cafe, sight], req)                               # cafe first: fine
    assert [s.place_id for s in ok.stops] == ["cafe_till_18", "sight"] and ok.stops[0].hours_ok
    moved = plan_sequence([sight, cafe], req)                            # user moved it after the sight
    assert [s.place_id for s in moved.stops] == ["sight", "cafe_till_18"]  # still built, nothing dropped
    assert moved.stops[1].hours_ok is False                              # ... but flagged
    assert moved.stops[1].arrival_min > moved.stops[0].depart_min        # times follow the new order


def test_on_the_way_stop_is_placed_while_it_is_still_open():
    # an optional museum that closes at 17:00 is only possible BEFORE the 2.5 h sight
    sight = _c("sight", 0.002, 0.000, slot=0, interest=0.6, dwell=150)
    museum = _c("museum_till_17", 0.001, 0.0005, slot=0, interest=0.9, dwell=45, subtype="museum")
    museum.open_hours = _hours(saturday=("10:00", "17:00"))
    museum.extra = True
    plan = plan_walk(WalkRequest(candidates=[sight, museum], mode="slots", n_slots=1,
                                 start=(BASE_LAT, BASE_LON), shape="loop", time_budget_min=360,
                                 start_week_min=SAT + 16 * 60))
    ids = [s.place_id for s in plan.stops]
    assert ids == ["museum_till_17", "sight"]
    m = plan.stops[0]
    assert m.extra and m.dwell_min == pytest.approx(10)                  # a look on the way, not 45 min
    assert m.hours_ok and 16 * 60 + m.depart_min <= 17 * 60


def test_on_the_way_stops_may_repeat_a_kind():
    core = [_c("park", 0.004, 0.000, slot=0, interest=0.6, dwell=40)]
    churches = [_c(f"church{i}", 0.001 * i, 0.0003, slot=0, interest=0.8, dwell=45, subtype="orthodox_church")
                for i in (1, 2, 3)]
    for c in churches:
        c.extra = True
    plan = plan_walk(WalkRequest(candidates=core + churches, mode="slots", n_slots=1,
                                 start=(BASE_LAT, BASE_LON), shape="loop", time_budget_min=180))
    assert sum(s.extra for s in plan.stops) == 3                          # all three churches on the way
    assert all(s.dwell_min == pytest.approx(10) for s in plan.stops if s.extra)


# --- the user's own places & alternative routes -----------------------------------------------

def test_must_visit_place_is_always_in_the_route_where_it_fits_best():
    a = _c("a", 0.002, 0.000, slot=0, interest=0.6, dwell=30)
    b = _c("b", 0.004, 0.000, slot=1, interest=0.6, dwell=30)
    mine = _c("mine", 0.003, 0.0002, interest=0.0, dwell=20)            # between a and b, dull
    plan = plan_walk(WalkRequest(candidates=[a, b], mode="slots", n_slots=2, start=(BASE_LAT, BASE_LON),
                                 shape="one_way", time_budget_min=300, fill_window=False, must_visit=[mine]))
    assert [s.place_id for s in plan.stops] == ["a", "mine", "b"]        # cheapest spot: on the way
    assert plan.stops[1].pinned and not plan.stops[0].pinned


def test_must_visit_goes_where_it_is_open_and_is_kept_even_if_never_open():
    from walk_planner import best_insertion
    sight = _c("sight", 0.002, 0.000, slot=0, interest=0.6, dwell=120)
    shop = _c("shop_till_17", 0.0021, 0.0001, dwell=30)
    shop.open_hours = _hours(saturday=("10:00", "17:00"))
    req = WalkRequest(candidates=[], start=(BASE_LAT, BASE_LON), shape="one_way", time_budget_min=360,
                      start_week_min=SAT + 16 * 60)
    assert [c.place_id for c in best_insertion([sight], shop, req)] == ["shop_till_17", "sight"]  # before 17:00
    closed = _c("closed_today", 0.001, 0.0, dwell=30)
    closed.open_hours = _hours(monday=("10:00", "17:00"))                # never open on Saturday
    assert "closed_today" in [c.place_id for c in best_insertion([sight], closed, req)]    # kept anyway
    plan = plan_walk(WalkRequest(candidates=[sight], mode="slots", n_slots=1, start=(BASE_LAT, BASE_LON),
                                 shape="one_way", time_budget_min=360, start_week_min=SAT + 16 * 60,
                                 fill_window=False, must_visit=[closed]))
    s = next(s for s in plan.stops if s.place_id == "closed_today")
    assert s.pinned and s.hours_ok is False                              # flagged, not dropped


def test_plan_variants_are_different_routes_and_keep_must_visits():
    from walk_planner import plan_variants
    cands = [_c(f"s{k}", 0.001 * (k + 1), 0.0005 * k, slot=0, interest=0.9 - 0.05 * k, dwell=30) for k in range(4)]
    cands += [_c(f"c{k}", 0.001 * (k + 1), -0.0005 * k, slot=1, interest=0.8 - 0.05 * k, dwell=20) for k in range(4)]
    mine = _c("mine", 0.002, 0.001, dwell=15)
    plans = plan_variants(WalkRequest(candidates=cands, mode="slots", n_slots=2, start=(BASE_LAT, BASE_LON),
                                      shape="loop", time_budget_min=240, fill_window=False, must_visit=[mine]), n=3)
    assert len(plans) == 3
    keys = [tuple(s.place_id for s in p.stops) for p in plans]
    assert len(set(keys)) == 3                                           # three different routes
    assert all("mine" in k for k in keys)                                # the user's place in every one
    assert plans[0].stops[0].interest >= plans[1].stops[0].interest or keys[0] != keys[1]


# --- visit length per place --------------------------------------------------------------------

def test_dwell_estimate_depends_on_the_place_not_just_the_type():
    from walk_planner import estimate_dwell_min as est
    # the two places from the user's screenshots
    assert est("nature_outdoors", "park", 2, "public park",
               "A public park offering an outdoor setting for a brief stroll, pause, or quiet break.") == 10
    assert est("culture_sights", "historical_landmark", 6, "historic church bell tower") == 10
    # big places get longer
    assert est("nature_outdoors", "park", 35092, "large city park") >= 55
    assert est("culture_sights", "natural_history_museum", 20945, "national museum") == 90
    assert est("culture_sights", "monument", 18886, "historic triumphal arch") <= 25
    # food keeps its base: sitting time does not grow with fame
    assert est("restaurant", "restaurant", 30000) == est("restaurant", "restaurant", 30) == 75
    assert 10 <= est("culture_sights", None, None) <= 180                 # no data -> type base


def test_a_visit_length_the_user_set_is_not_rescaled_by_the_style():
    c = _c("rest", 0.001, 0.0, slot=0, interest=0.5, dwell=120)
    c.dwell_fixed = True
    auto = _c("park", 0.002, 0.0, slot=1, interest=0.5, dwell=40)
    plan = plan_walk(WalkRequest(candidates=[c, auto], mode="slots", n_slots=2, start=(BASE_LAT, BASE_LON),
                                 style="chill", time_budget_min=400, fill_window=False))
    assert plan.stops[0].dwell_min == pytest.approx(120)                 # user's 2 h stays 2 h
    assert plan.stops[1].dwell_min == pytest.approx(52)                  # estimate: chill ×1.3


# --- v1: segment indices, empty plans, routing quality -----------------------------------------

def _three_stops():
    return [_c("a", 0.001, 0.0, slot=0, interest=0.5, dwell=20),
            _c("b", 0.002, 0.001, slot=1, interest=0.5, dwell=20),
            _c("c", 0.003, 0.0, slot=2, interest=0.5, dwell=20)]


def _assert_arrivals_follow_segments(plan):
    # invariant: a stop's arrival = departure of the segment's from-stop (0 at the start anchor) + its walk
    by_order = {s.order: s for s in plan.stops}
    for g in plan.segments:
        if g.to_order >= 0:
            prev_depart = by_order[g.from_order].depart_min if g.from_order >= 0 else 0.0
            assert by_order[g.to_order].arrival_min == pytest.approx(prev_depart + g.walk_min)


@pytest.mark.parametrize("shape,start,expected", [
    ("one_way", (BASE_LAT, BASE_LON), [(-1, 0), (0, 1), (1, 2)]),
    ("loop", (BASE_LAT, BASE_LON), [(-1, 0), (0, 1), (1, 2), (2, -1)]),
    ("free", None, [(0, 1), (1, 2)]),
    ("one_way", None, [(0, 1), (1, 2)]),
])
def test_segment_indices_match_stop_order(shape, start, expected):
    # BUG 1 (v0 labelled legs as if a start anchor always existed: free / no start gave (-1,0),(0,1))
    plan = plan_walk(WalkRequest(candidates=_three_stops(), mode="slots", n_slots=3, start=start, shape=shape,
                                 time_budget_min=600, fill_window=False))
    assert [s.place_id for s in plan.stops] == ["a", "b", "c"]
    assert [(g.from_order, g.to_order) for g in plan.segments] == expected
    _assert_arrivals_follow_segments(plan)


@pytest.mark.parametrize("shape,start,expected", [
    ("free", (BASE_LAT, BASE_LON), [(0, 1), (1, 2)]),          # "free" never walks from the start
    ("loop", None, [(0, 1), (1, 2)]),                          # no start: no anchor, no return leg
    ("loop", (BASE_LAT, BASE_LON), [(-1, 0), (0, 1), (1, 2), (2, -1)]),
])
def test_manual_order_segment_indices(shape, start, expected):
    from walk_planner import plan_sequence
    plan = plan_sequence(_three_stops(), WalkRequest(candidates=[], start=start, shape=shape, time_budget_min=600))
    assert [(g.from_order, g.to_order) for g in plan.segments] == expected
    _assert_arrivals_follow_segments(plan)


class _SpyProvider(RoutingProvider):
    """The estimate, counting route_legs calls."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def route_legs(self, coords):
        self.calls += 1
        return super().route_legs(coords)


def test_empty_plan_has_no_points_no_router_call_and_no_segments():
    # v0 routed start -> start for an empty loop (a phantom (-1, -1) segment and a router call)
    from walk_planner import plan_sequence
    spy = _SpyProvider()
    for shape in ("loop", "one_way", "free"):
        plan = plan_sequence([], WalkRequest(candidates=[], start=(BASE_LAT, BASE_LON), shape=shape,
                                             time_budget_min=60, provider=spy))
        assert plan.stops == [] and plan.segments == [] and plan.routing_quality == "none"
        assert plan.total_time_min == 0 and plan.total_distance_km == 0 and not plan.over_budget
    empty = plan_walk(WalkRequest(candidates=[], mode="slots", start=(BASE_LAT, BASE_LON), shape="loop",
                                  time_budget_min=60, provider=spy))
    assert empty.stops == [] and empty.segments == []
    assert spy.calls == 0
    one = plan_sequence(_three_stops()[:1], WalkRequest(candidates=[], start=(BASE_LAT, BASE_LON), shape="loop",
                                                         time_budget_min=60, provider=spy))
    assert spy.calls == 1 and [(g.from_order, g.to_order) for g in one.segments] == [(-1, 0), (0, -1)]


def test_estimate_legs_are_labelled_and_the_plan_says_estimate():
    leg = RoutingProvider().route([(BASE_LAT, BASE_LON), (BASE_LAT + 0.01, BASE_LON)])
    assert (leg["quality"], leg["provider"]) == ("estimate", "haversine")
    plan = plan_walk(WalkRequest(candidates=_three_stops(), mode="slots", n_slots=3, start=(BASE_LAT, BASE_LON),
                                 shape="loop", time_budget_min=600, fill_window=False))
    assert plan.segments and {(g.quality, g.provider) for g in plan.segments} == {("estimate", "haversine")}
    assert plan.routing_quality == "estimate"


class _LabelledProvider(RoutingProvider):
    """Estimate legs relabelled with the given qualities (a stand-in for a street router); None = an
    old-style provider whose legs carry no quality/provider keys at all."""

    def __init__(self, qualities):
        super().__init__()
        self.qualities = qualities

    def route_legs(self, coords):
        legs = super().route_legs(coords)
        for leg, q in zip(legs, self.qualities):
            if q is None:
                del leg["quality"], leg["provider"]
            else:
                leg["quality"], leg["provider"] = q, ("osrm" if q == "streets" else "haversine")
        return legs


@pytest.mark.parametrize("qualities,expected", [
    (["streets"] * 4, "streets"),
    (["estimate"] * 4, "estimate"),
    (["streets", "estimate", "streets", "streets"], "mixed"),
    ([None] * 4, "estimate"),
])
def test_plan_routing_quality_aggregates_the_segments(qualities, expected):
    from walk_planner import plan_sequence
    from walk_planner.core import _routing_quality
    plan = plan_sequence(_three_stops(), WalkRequest(candidates=[], start=(BASE_LAT, BASE_LON), shape="loop",
                                                     time_budget_min=600, provider=_LabelledProvider(qualities)))
    assert len(plan.segments) == 4 and plan.routing_quality == expected
    assert [g.quality for g in plan.segments] == [q or "estimate" for q in qualities]
    assert [g.provider for g in plan.segments] == [("osrm" if q == "streets" else "haversine") for q in qualities]
    assert _routing_quality([]) == "none"


def test_navigation_start_label_is_a_parameter():
    from walk_planner import navigation_links
    plan = _plan_with_n_stops(2)
    nav = navigation_links(plan, start=(44.40, 26.09), shape="loop", start_label="Start")
    assert nav["legs"][0]["from"] == "Start" and nav["legs"][-1]["to"] == "Start"
    default = navigation_links(plan, start=(44.40, 26.09), shape="loop")
    assert default["legs"][0]["from"] == "Старт" and default["legs"][-1]["to"] == "Старт"
    assert nav["google"] == default["google"]                                 # only the label changes
    assert [l["google"] for l in nav["legs"]] == [l["google"] for l in default["legs"]]


def test_core_never_imports_the_routing_module_itself():
    # routing.py imports core; core resolves the old ORSProvider name lazily (no import cycle, no HTTP stack)
    import ast
    import inspect
    from walk_planner import core
    tree = ast.parse(inspect.getsource(core))
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert not any("routing" in a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert "routing" not in (node.module or "") and not any("routing" in a.name for a in node.names)
    assert "ORSProvider" not in vars(core)
    from walk_planner.routing import ORSProvider
    assert core.ORSProvider is ORSProvider
