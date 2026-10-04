"""Unit tests of walk_planner.pipeline: request validation (error codes), the resolved context, the
request-echo round trip, build_plan (every message code, the must-visit status policy, cold start vs
the personalised path), the stateless editing operations (schedule / insert_place, re-hydration,
errors) and — when the real catalog is present — a golden subset through golden/tools/parity_check.py.
Offline: synthetic catalog, straight-line routing; the interest model is stubbed."""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

from walk_planner.catalog import CityCatalog
from walk_planner.core import Candidate, best_insertion, reach_radius_km
from walk_planner.pipeline import (
    LIMITS,
    EditStop,
    PlanInterest,
    PlannerInputError,
    build_plan,
    edit_stops,
    from_request_echo,
    insert_place,
    make_context,
    normalize_params,
    resolve_interest,
    schedule,
    sequence_candidates,
    sequence_request,
    to_request_echo,
)
from walk_planner.present import variant_messages

SAT = 5 * 1440
S = (44.4300, 26.1000)
GOLDEN = Path(__file__).resolve().parents[1] / "golden"
# The research repo root (services/walk_planner/tests -> parents[3]); None when this package is vendored fewer
# than 3 levels below "/" (e.g. /src/tests): there is then no real data, and those tests skip.
_UP = Path(__file__).resolve().parents
REPO = _UP[3] if len(_UP) > 3 else None
REAL_CSV = (REPO / "recommendation_system" / "ai_location_recommender" / "data" / "locations_bucharest_all.csv"
            if REPO is not None else None)


def _p(pid, dlat, dlon, theme, group, ptype, reviews=500, hours=None, status=None, rating=4.6):
    return {"place_id": pid, "name": pid.replace("_", " ").title(), "latitude": S[0] + dlat, "longitude": S[1] + dlon,
            "city": "Testville", "theme": theme, "theme_group": group, "primary_type": ptype,
            "ai_place_type_summary": ptype, "ai_card_summary": "", "google_user_rating_count": reviews,
            "bayesian_rating": rating, "opening_hours": None if hours is None else json.dumps(hours),
            "business_status": status, "google_place_id": "ChIJ" + pid}


OPEN_SAT = [[SAT + 480, SAT + 1320]]


def _rows():
    rows = []
    for i in range(1, 7):
        rows.append(_p(f"museum_{i}", 0.0015 * i, 0.0012 * (i % 3), "culture_sights", "sights", "museum",
                       400 * i, OPEN_SAT, rating=4.2 + 0.1 * i))
        rows.append(_p(f"cafe_{i}", 0.0010 * i, -0.0015 * (i % 2), "food_drink", "food_drink", "cafe", 150 * i, OPEN_SAT))
        rows.append(_p(f"park_{i}", -0.0015 * i, 0.0015 * (i % 3), "nature_outdoors", "sights", "park", 900 * i))
        rows.append(_p(f"bistro_{i}", -0.0010 * i, -0.0012 * (i % 3), "food_drink", "food_drink", "restaurant",
                       300 * i, OPEN_SAT))
    rows += [
        _p("gone", 0.0021, 0.0004, "food_drink", "food_drink", "romanian_restaurant", 6000, status="closed_forever"),
        _p("paused_pub", 0.0012, 0.0006, "food_drink", "food_drink", "pub", 1900, status="temporarily_closed"),
        _p("late_cafe", 0.0300, 0.0300, "food_drink", "food_drink", "coffee_shop", 50, [[SAT + 780, SAT + 840]]),
    ]
    return rows


@pytest.fixture(scope="module")
def cat():
    return CityCatalog.from_frame(pd.DataFrame(_rows()))


def req(**kw):
    raw = {"city": "Testville", "date": "2026-10-03", "start_time": "10:00", "end_time": "16:00", "shape": "loop",
           "start": "city_center", "slots": ["sight", "coffee", "park", "food"], "variants": 3}
    raw.update(kw)
    return raw


def code_of(fn):
    with pytest.raises(PlannerInputError) as ei:
        fn()
    return ei.value.code, ei.value.http_status, ei.value.params


# --------------------------------------------------------------------------- #
# normalize_params
# --------------------------------------------------------------------------- #
def test_defaults_follow_the_page(cat):
    p = normalize_params({"date": "2026-10-03", "start": "city_center"}, cat)
    assert (p.city, p.start_time.hour, p.end_time.hour, p.end_day_offset, p.window_min) == ("Testville", 10, 14, 0, 240)
    assert (p.shape, p.style, p.slot_codes, p.variants, p.radius_km) == ("loop", "max", ["sight", "coffee", "park", "food"], 3, 2.5)
    assert (p.fill_window, p.known_hours_only, p.top_k, p.personalization_strength) == (True, False, 8, 0.5)
    assert (p.must_visit_place_ids, p.favourite_place_ids, p.want_to_go_place_ids) == ([], [], [])
    assert p.start == "city_center" and p.start_place_id is None


def test_labels_codes_and_ids_are_normalised(cat):
    p = normalize_params(req(shape="Петля", style="Живописный", slots=["Кофе", {"activity": "food", "dwell_min": 120.0}],
                             must_visit_place_ids=["cafe_1", "cafe_1", "museum_2"], favorite_place_ids=["park_1"],
                             want_to_go_place_ids=[" 18444330390184695581 "]), cat)
    assert (p.shape, p.style) == ("loop", "scenic")
    assert [(s.activity, s.dwell_min) for s in p.slots] == [("coffee", None), ("food", 120)]
    assert p.must_visit_place_ids == ["cafe_1", "museum_2"] and p.favourite_place_ids == ["park_1"]
    assert p.want_to_go_place_ids == ["18444330390184695581"]


@pytest.mark.parametrize("kw,expected", [
    (dict(start_time="22:00", end_time="02:00", end_day_offset=None), (1, 240)),   # offset inferred
    (dict(start_time="00:00", end_time="00:00", end_day_offset=1), (1, 1440)),     # full day
    (dict(start_time="10:00", end_time="10:15", end_day_offset=0), (0, 15)),
    (dict(start_time="22:00", end_time=None, end_day_offset=None), (1, 240)),      # default 4 h
])
def test_window_resolution(cat, kw, expected):
    p = normalize_params(req(**kw), cat)
    assert (p.end_day_offset, p.window_min) == expected


@pytest.mark.parametrize("kw,code,field", [
    (dict(start_time="10:00", end_time="10:10"), "invalid_window", None),
    (dict(start_time="10:00", end_time="09:00", end_day_offset=0), "invalid_window", None),
    (dict(start_time="10:00", end_time="11:00", end_day_offset=1), "invalid_window", None),
    (dict(date="03.10.2026"), "validation_error", "date"),
    (dict(date=None), "validation_error", "date"),
    (dict(start_time="25:00"), "validation_error", "start_time"),
    (dict(end_day_offset=2), "validation_error", "end_day_offset"),
    (dict(city="Paris"), "unknown_city", None),
    (dict(shape="circle"), "validation_error", "shape"),
    (dict(style="relaxed"), "validation_error", "style"),
    (dict(start=None), "start_required", None),
    (dict(start="home"), "validation_error", "start"),
    (dict(start={"lat": 95, "lon": 26.1}), "validation_error", "start.lat"),
    (dict(start={}), "validation_error", "start"),
    (dict(start={"place_id": "nope"}), "unknown_place", None),
    (dict(slots=["museum"]), "unknown_activity", None),
    (dict(slots=["sight", "Достопримечательность"]), "duplicate_activity", None),
    (dict(slots=["sight", "coffee", "food", "bar", "park", "market", "entertainment", "shopping", "sight"]),
     "validation_error", "slots"),
    (dict(slots=[{"activity": "food", "dwell_min": 4}]), "validation_error", "slots[0].dwell_min"),
    (dict(slots=[{"activity": "food", "dwell_min": 47.5}]), "validation_error", "slots[0].dwell_min"),
    (dict(slots=[5]), "validation_error", "slots[0]"),
    (dict(slots=[]), "no_slots_or_must_visits", None),
    (dict(must_visit_place_ids=[str(i) for i in range(11)]), "too_many_must_visits", None),
    (dict(must_visit_place_ids=[1.5]), "validation_error", "must_visit_place_ids[0]"),
    (dict(must_visit_place_ids="cafe_1"), "validation_error", "must_visit_place_ids"),
    (dict(favourite_place_ids=[str(i) for i in range(300)], want_to_go_place_ids=[f"w{i}" for i in range(201)]),
     "validation_error", "favourite_place_ids"),
    (dict(variants=0), "validation_error", "variants"),
    (dict(variants=6), "validation_error", "variants"),
    (dict(variants=True), "validation_error", "variants"),
    (dict(radius_km=0.2), "validation_error", "radius_km"),
    (dict(radius_km=51), "validation_error", "radius_km"),
    (dict(personalization_strength=1.5), "validation_error", "personalization_strength"),
    (dict(fill_window="yes"), "validation_error", "fill_window"),
    (dict(top_k=0), "validation_error", "top_k"),
    # dates: strictly YYYY-MM-DD on every Python (3.11+ fromisoformat alone also takes the compact /
    # week forms), ASCII digits, a real calendar day
    (dict(date="20261003"), "validation_error", "date"),
    (dict(date="2026-W40-6"), "validation_error", "date"),
    (dict(date="2026-10-3"), "validation_error", "date"),
    (dict(date="2026-02-30"), "validation_error", "date"),
    (dict(date="2026-10-03T10:00"), "validation_error", "date"),
    (dict(date="２０２６-１０-０３"), "validation_error", "date"),
    (dict(date=20261003), "validation_error", "date"),
    # times: strictly HH:MM
    (dict(start_time="9:00"), "validation_error", "start_time"),
    (dict(start_time="10:00:00"), "validation_error", "start_time"),
    (dict(start_time="10:0"), "validation_error", "start_time"),
    (dict(start_time="١٠:٠٠"), "validation_error", "start_time"),
    (dict(end_time="24:00"), "validation_error", "end_time"),
    (dict(end_time="12:60"), "validation_error", "end_time"),
    (dict(end_time=1200), "validation_error", "end_time"),
    # non-finite / out-of-float-range numbers are validation errors, never a crash
    (dict(variants=float("inf")), "validation_error", "variants"),
    (dict(variants=float("nan")), "validation_error", "variants"),
    (dict(variants=10 ** 400), "validation_error", "variants"),
    (dict(top_k=float("-inf")), "validation_error", "top_k"),
    (dict(end_time="12:00", end_day_offset=float("inf")), "validation_error", "end_day_offset"),
    (dict(slots=[{"activity": "food", "dwell_min": float("inf")}]), "validation_error", "slots[0].dwell_min"),
    (dict(slots=[{"activity": "food", "dwell_min": float("nan")}]), "validation_error", "slots[0].dwell_min"),
    (dict(radius_km=float("inf")), "validation_error", "radius_km"),
    (dict(radius_km=10 ** 400), "validation_error", "radius_km"),
    (dict(personalization_strength=float("nan")), "validation_error", "personalization_strength"),
    (dict(start={"lat": float("nan"), "lon": 26.1}), "validation_error", "start.lat"),
    (dict(start={"lat": 44.4, "lon": -10 ** 400}), "validation_error", "start.lon"),
    # enums of the wrong JSON type
    (dict(shape=["loop"]), "validation_error", "shape"),
    (dict(shape={"code": "loop"}), "validation_error", "shape"),
    (dict(shape=1), "validation_error", "shape"),
    (dict(style=["max"]), "validation_error", "style"),
    (dict(style={"max": True}), "validation_error", "style"),
    (dict(style=True), "validation_error", "style"),
    (dict(slots=[{"activity": ["food"]}]), "validation_error", "slots[0].activity"),
    (dict(slots=[{"activity": {"code": "food"}}]), "validation_error", "slots[0].activity"),
    (dict(slots=[{"dwell_min": 30}]), "validation_error", "slots[0].activity"),
    (dict(slots=["sight", {"activity": None}]), "validation_error", "slots[1].activity"),
    (dict(city=["Testville"]), "validation_error", "city"),
    (dict(city=7), "validation_error", "city"),
    # place ids are strings: a JSON number has already lost a CID's precision
    (dict(must_visit_place_ids=[12345]), "validation_error", "must_visit_place_ids[0]"),
    (dict(must_visit_place_ids=[True]), "validation_error", "must_visit_place_ids[0]"),
    (dict(favourite_place_ids=["park_1", 18444330390184695581]), "validation_error", "favourite_place_ids[1]"),
    (dict(want_to_go_place_ids=[1.8444330390184696e19]), "validation_error", "want_to_go_place_ids[0]"),
    (dict(must_visit_place_ids=[""]), "validation_error", "must_visit_place_ids[0]"),
    (dict(start={"place_id": 12345}), "validation_error", "start.place_id"),
    (dict(start={"lat": 44.4, "lon": 26.1, "place_id": 12345}), "validation_error", "start.place_id"),
])
def test_validation_errors(cat, kw, code, field):
    got, status, params = code_of(lambda: normalize_params(req(**kw), cat))
    assert got == code and status == {"unknown_place": 404}.get(code, 422)
    if field is not None:
        assert params["field"] == field
    err = PlannerInputError(got, params)
    for lang in ("ru", "en"):                    # the HTTP error body renders and is strict JSON
        json.dumps(err.to_dict(lang), allow_nan=False)


@pytest.mark.parametrize("body", [
    '{"variants": Infinity}', '{"variants": NaN}', '{"top_k": -Infinity}', '{"radius_km": Infinity}',
    '{"radius_km": 1e400}', '{"personalization_strength": NaN}', '{"end_time": "12:00", "end_day_offset": Infinity}',
    '{"slots": [{"activity": "food", "dwell_min": Infinity}]}', '{"start": {"lat": NaN, "lon": 26.1}}',
    '{"variants": 1' + "0" * 400 + '}',
])
def test_json_non_finite_numbers_are_validation_errors(cat, body):
    # Python's json (FastAPI's parser) accepts NaN / Infinity / 1e400 (-> inf) and huge integers
    raw = {**req(), **json.loads(body)}
    got, status, params = code_of(lambda: normalize_params(raw, cat))
    assert (got, status) == ("validation_error", 422)
    json.dumps(PlannerInputError(got, params).to_dict("en"), allow_nan=False)


def test_start_forms(cat):
    p = normalize_params(req(start={"place_id": "museum_2"}), cat)
    assert p.start == cat.coord_of("museum_2") and p.start_place_id == "museum_2"
    p = normalize_params(req(start=[44.45, 26.11]), cat)
    assert p.start == (44.45, 26.11) and p.start_place_id is None
    p = normalize_params(req(start={"lat": 44.45, "lon": 26.11, "place_id": "museum_2"}), cat)
    assert p.start == (44.45, 26.11) and p.start_place_id == "museum_2"      # coordinates win (echo)
    p = normalize_params(req(shape="free", start={"lat": 1, "lon": 2}), cat)
    assert p.start is None and p.start_place_id is None                       # "free" forces no start


def test_must_visits_alone_are_enough(cat):
    assert normalize_params(req(slots=[], must_visit_place_ids=["cafe_1"]), cat).slots == []


def test_payload_must_be_an_object(cat):
    assert code_of(lambda: normalize_params(["x"], cat))[0] == "validation_error"


# --------------------------------------------------------------------------- #
# Context + request echo
# --------------------------------------------------------------------------- #
def test_make_context(cat):
    p = normalize_params(req(start_time="22:00", end_time="02:00", slots=["sight", {"activity": "food", "dwell_min": 120}]), cat)
    ctx = make_context(p, cat)
    assert (ctx.weekday, ctx.t0, ctx.budget_min, ctx.end_abs, ctx.next_day) == (5, SAT + 1320, 240, 1560, True)
    assert ctx.start_resolved == cat.center == ctx.area and ctx.center == cat.center
    assert ctx.dwell_total == 45.0 + 120 and ctx.reach_km == reach_radius_km(240, 165.0, shape="loop")
    assert ctx.search_km == min(2.5, ctx.reach_km)
    assert ctx.window_start.isoformat() == "2026-10-03T22:00:00" and ctx.window_end.isoformat() == "2026-10-04T02:00:00"
    assert (ctx.start_label, ctx.end_label, ctx.clock(150)) == ("22:00", "02:00 (+1 день)", "00:30 (+1)")
    assert ctx.shape == "loop" and ctx.slot_codes == ["sight", "food"]          # params readable on the context
    with pytest.raises(AttributeError):
        ctx.nonexistent
    free = make_context(normalize_params(req(shape="free", start=None), cat), cat)
    assert free.start_resolved is None and free.area == cat.center


def test_request_echo_round_trip(cat):
    p = normalize_params(req(start={"place_id": "museum_3"}, must_visit_place_ids=["cafe_2"], radius_km=1.7,
                             slots=["sight", {"activity": "coffee", "dwell_min": 15}], favourite_place_ids=["park_2"]),
                         cat)
    ctx = make_context(p, cat)
    echo = json.loads(json.dumps(to_request_echo(ctx)))
    assert echo["start"] == {"lat": cat.coord_of("museum_3")[0], "lon": cat.coord_of("museum_3")[1], "place_id": "museum_3"}
    assert (echo["timezone"], echo["weekday"], echo["window_min"], echo["window_start"], echo["window_end"]) == \
        ("UTC", 5, 360, "2026-10-03T10:00", "2026-10-03T16:00")
    assert echo["search_radius_km"] == ctx.search_km and echo["catalog_version"] == cat.version
    back = from_request_echo(echo, cat)
    assert back.params == p
    for f in ("t0", "budget_min", "end_abs", "next_day", "start_resolved", "area", "search_km", "window_start"):
        assert getattr(back, f) == getattr(ctx, f), f
    assert back.plan_catalog_version == cat.version
    assert to_request_echo(back) == to_request_echo(ctx)
    assert to_request_echo(make_context(normalize_params(req(shape="free", start=None), cat), cat))["start"] is None
    assert code_of(lambda: from_request_echo("x", cat))[0] == "validation_error"


# --------------------------------------------------------------------------- #
# build_plan
# --------------------------------------------------------------------------- #
def test_build_ok_variants_and_states(cat):
    r = build_plan(normalize_params(req(), cat), cat)
    assert r.ok and r.status == "ok" and 1 <= len(r.variants) <= 3
    assert r.request is not None and r.request.must_visit == [] and r.interest.mode == "popularity"
    assert isinstance(r.interest, PlanInterest) and r.interest.error is None
    assert r.interest.map is cat.cold_interest()                         # the cold start, unchanged
    for i, st in enumerate(r.variants):
        assert st.index == i and not st.edited and st.dropped_slot_indices == list(st.plan.dropped_slots)
        assert [c.place_id for c in st.sequence] == [s.place_id for s in st.plan.stops]
        for c, s in zip(st.sequence, st.plan.stops):
            assert (c.dwell_min, c.slot, c.theme, c.extra, c.pinned, c.interest) == \
                   (s.dwell_min, s.slot, s.theme, s.extra, s.pinned, s.interest)
            assert c.open_hours == cat.hours_of(s.place_id)
        assert set(st.extra_activity) == {s.place_id for s in st.plan.stops if s.extra}
    codes = [m.code for m in r.messages]
    if "radius_shrunk" in codes:
        assert codes[0] == "radius_shrunk"                             # the page's note order
    assert all(c.extra for c in r.extra_candidates) and len(r.extra_candidate_activities) == len(r.extra_candidates)
    assert set(r.extra_activity) == {c.place_id for c in r.extra_candidates}


def test_radius_messages(cat):
    r = build_plan(normalize_params(req(end_time="14:00", radius_km=5), cat), cat)
    m = [x for x in r.messages if x.code == "radius_shrunk"][0]
    assert m.params["reason"] == "reach" and m.params["anchor"] == "start" and m.params["loop"] is True
    assert m.params["dwell_total_min"] == 190.0 and m.params["window_min"] == 240
    assert m.params["window_start_label"] == "10:00" and m.params["window_end_label"] == "14:00"
    r = build_plan(normalize_params(req(start_time="12:00", end_time="12:15", slots=["sight", "coffee"],
                                        shape="free", start=None), cat), cat)
    m = [x for x in r.messages if x.code == "radius_shrunk"][0]
    assert m.params["reason"] == "overpacked" and m.params["anchor"] == "center" and m.params["loop"] is False
    assert m.text("ru").endswith("поэтому ищу в центре района, а слоты, которые не влезут, отпадут.")
    big = build_plan(normalize_params(req(radius_km=0.3), cat), cat)
    assert "radius_shrunk" not in [x.code for x in big.messages]


def test_no_candidates_and_slots_without_candidates(cat):
    r = build_plan(normalize_params(req(slots=["shopping"]), cat), cat)
    assert r.status == "no_candidates" and r.variants == [] and r.request is None
    assert [m.code for m in r.messages if m.code != "radius_shrunk"] == ["slots_no_candidates", "no_candidates"]
    assert r.messages[-1].severity == "error"
    r = build_plan(normalize_params(req(slots=["sight", "shopping"]), cat), cat)
    m = [x for x in r.messages if x.code == "slots_no_candidates"][0]
    assert r.ok and m.params == {"activities": ["shopping"], "slot_indices": [1]}


def test_no_route_when_nothing_can_be_visited_on_time(cat):
    # the only coffee place in reach is open 13:00-14:00 (passes the whole-window prefilter) but the
    # route reaches it at ~10:00 and may wait at most 30 min -> no stop at all -> no plan
    r = build_plan(normalize_params(req(slots=["coffee"], radius_km=0.3, end_time="14:00",
                                        start={"lat": S[0] + 0.03, "lon": S[1] + 0.03}), cat), cat)
    assert [c.place_id for c in r.slot_candidates] == ["late_cafe"]
    assert r.status == "no_route" and r.variants == [] and r.messages[-1].code == "no_route"
    assert r.messages[-1].severity == "error" and r.request is not None


def test_fewer_variants_message(cat):
    r = build_plan(normalize_params(req(slots=[], must_visit_place_ids=["museum_1"], fill_window=False,
                                        variants=3), cat), cat)
    assert r.ok and len(r.variants) == 1
    m = [x for x in r.messages if x.code == "fewer_variants"][0]
    assert m.params == {"built": 1, "requested": 3}


def test_must_visit_status_policy(cat):
    r = build_plan(normalize_params(req(must_visit_place_ids=["gone", "paused_pub", "ghost"]), cat), cat)
    codes = {m.code: m for m in r.messages}
    assert codes["unknown_place_ids"].params == {"place_ids": ["ghost"], "field": "must_visit_place_ids"}
    assert codes["must_visit_closed_forever"].params == {"place_ids": ["gone"], "names": ["Gone"]}
    assert codes["must_visit_closed_forever"].text("ru") == ("Закрыто навсегда по данным Google — не добавлено в "
                                                            "маршрут: Gone.")
    assert [c.place_id for c in r.must_candidates] == ["paused_pub"] and r.must_candidates[0].pinned
    for st in r.variants:
        ids = [s.place_id for s in st.plan.stops]
        assert "gone" not in ids and "paused_pub" in ids
        pub = st.plan.stops[ids.index("paused_pub")]
        assert pub.pinned
        flagged = [m for m in variant_messages(r.context, st, cat) if m.code == "place_temporarily_closed"]
        assert [m.stop_index for m in flagged] == [pub.order]
    only_closed = build_plan(normalize_params(req(slots=[], must_visit_place_ids=["gone"]), cat), cat)
    assert only_closed.status == "no_candidates"


# --------------------------------------------------------------------------- #
# Personalization
# --------------------------------------------------------------------------- #
def _fake_interest_module(calls, boost="park_3"):
    mod = types.ModuleType("walk_planner.interest")

    def interest_map(place_ids, themes, cold, taste, favourite_ids=(), want_to_go_ids=(), strength=0.5):
        calls.append(dict(place_ids=list(place_ids), themes=list(themes), cold=cold, taste=taste,
                          favourite_ids=list(favourite_ids), want_to_go_ids=list(want_to_go_ids), strength=strength))
        if taste == "broken":
            raise ValueError("bad artifacts")
        m = dict(cold)
        m[boost] = 0.99
        return types.SimpleNamespace(map=m, mode="favourites", used=list(favourite_ids), ignored=["x"], profiles=1,
                                     strength=strength, taste_pct={boost: 1.0}, similar_to={boost: "park_1"})
    mod.interest_map = interest_map
    return mod


def test_cold_start_never_touches_the_taste_model(cat, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "walk_planner.interest", _fake_interest_module(calls))
    res, msgs = resolve_interest(normalize_params(req(), cat), cat, taste="artifacts")
    assert calls == [] and msgs == [] and res.map is cat.cold_interest() and res.mode == "popularity"


def test_personalised_interest_through_the_interest_module(cat, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "walk_planner.interest", _fake_interest_module(calls))
    p = normalize_params(req(favourite_place_ids=["park_1"], want_to_go_place_ids=["cafe_2"],
                             personalization_strength=0.7), cat)
    r = build_plan(p, cat, taste="artifacts")
    call = calls[0]
    assert call["place_ids"] == cat.place_ids and call["themes"] == list(cat.rows["theme"])
    assert call["cold"] == cat.cold_interest() and call["taste"] == "artifacts"
    assert (call["favourite_ids"], call["want_to_go_ids"], call["strength"]) == (["park_1"], ["cafe_2"], 0.7)
    it = r.interest
    assert (it.mode, it.used, it.ignored, it.profiles, it.strength) == ("favourites", ["park_1"], ["x"], 1, 0.7)
    assert it.map["park_3"] == 0.99 and it.similar_to == {"park_3": "park_1"}
    boosted = [c for c in r.slot_candidates if c.place_id == "park_3"]
    assert boosted and boosted[0].interest == 0.99


def test_personalization_unavailable_falls_back_to_cold_start(cat, monkeypatch):
    p = normalize_params(req(favourite_place_ids=["park_1"]), cat)
    res, msgs = resolve_interest(p, cat, taste=None)
    assert res.map is cat.cold_interest() and res.mode == "popularity" and res.ignored == ["park_1"]
    assert [(m.code, m.params) for m in msgs] == [("personalization_unavailable", {"reason": "taste_unavailable"})]
    calls = []
    monkeypatch.setitem(sys.modules, "walk_planner.interest", _fake_interest_module(calls))
    res, msgs = resolve_interest(p, cat, taste="broken")
    assert res.error == "interest_error:ValueError" and msgs[0].params["reason"] == "interest_error:ValueError"
    cold_plan = build_plan(normalize_params(req(), cat), cat)
    fallback = build_plan(p, cat, taste=None)
    assert [[s.place_id for s in v.plan.stops] for v in fallback.variants] == \
           [[s.place_id for s in v.plan.stops] for v in cold_plan.variants]
    assert fallback.messages[-1].code == "personalization_unavailable" or \
        "personalization_unavailable" in [m.code for m in fallback.messages]


# --------------------------------------------------------------------------- #
# Editing
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def built(cat):
    return build_plan(normalize_params(req(must_visit_place_ids=["museum_5"]), cat), cat)


def _times(plan):
    return [(s.place_id, s.arrival_min, s.wait_min, s.depart_min, s.dwell_min, s.hours_ok, s.slot, s.theme,
             s.extra, s.pinned) for s in plan.stops]


def test_schedule_of_a_built_variant_reproduces_it(cat, built):
    ctx = from_request_echo(json.loads(json.dumps(to_request_echo(built.context))), cat)
    v0 = built.variants[0]
    stops = [json.loads(json.dumps(e.to_dict())) for e in edit_stops(ctx, v0)]
    st = schedule(ctx, stops, cat)
    assert _times(st.plan) == _times(v0.plan)          # variant 0: same interest too (no penalty)
    assert [s.interest for s in st.plan.stops] == [s.interest for s in v0.plan.stops]
    assert st.edited and st.dropped_slot_indices == [] and st.extra_activity == v0.extra_activity
    assert st.plan.total_time_min == v0.plan.total_time_min


def test_schedule_keeps_the_exact_order_and_new_minutes(cat, built):
    ctx = built.context
    seq = edit_stops(ctx, built.variants[0])
    rev = list(reversed(seq))
    st = schedule(ctx, rev, cat)
    assert [s.place_id for s in st.plan.stops] == [e.place_id for e in rev]
    longer = [EditStop(**{**e.to_dict(), "dwell_min": 120.0, "dwell_fixed": True}) if i == 0 else e
              for i, e in enumerate(seq)]
    st2 = schedule(ctx, longer, cat)
    assert st2.plan.stops[0].dwell_min == 120.0 and st2.sequence[0].dwell_fixed
    assert edit_stops(ctx, st2)[0].dwell_fixed is True
    shorter = schedule(ctx, seq[1:], cat)
    assert len(shorter.plan.stops) == len(seq) - 1
    empty = schedule(ctx, [], cat)
    assert empty.plan.stops == [] and [m.code for m in variant_messages(ctx, empty, cat)] == ["route_empty"]


def test_rehydration_rules(cat, built):
    ctx = built.context
    stops = [{"place_id": "museum_1", "kind": "slot", "slot_index": 0, "dwell_min": 45},
             {"place_id": "park_2", "kind": "on_the_way", "activity": "park", "dwell_min": 10},
             {"place_id": "museum_3", "kind": "on_the_way", "activity": "market", "dwell_min": 10},
             {"place_id": "cafe_1", "kind": "pinned", "slot_index": 1, "dwell_min": 30},
             {"place_id": "bistro_2", "kind": "pinned", "dwell_min": 60}]
    seq, items = sequence_candidates(ctx, stops, cat)
    cold = cat.cold_interest()
    got = [(c.slot, c.theme, c.extra, c.pinned, c.interest) for c in seq]
    assert got == [(0, "culture_sights", False, False, cold["museum_1"]),
                   (2, "nature_outdoors", True, False, cold["park_2"]),       # park is slot 2 of the request
                   (-1, "markets_walks", True, False, cold["museum_3"]),      # unrequested pool type -> -1
                   (1, "coffee", False, True, cold["cafe_1"]),                # a must-visit that filled a slot
                   (-1, "food_drink", False, True, 0.0)]                      # the user's own place
    assert seq[0].name == "Museum 1" and seq[0].open_hours == cat.hours_of("museum_1")
    assert [e.kind for e in items] == ["slot", "on_the_way", "on_the_way", "pinned", "pinned"]


@pytest.mark.parametrize("stops,code,status", [
    ([{"place_id": "cafe_1", "kind": "pinned", "dwell_min": 30}] * 2, "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "detour", "dwell_min": 30}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "slot", "dwell_min": 30}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "slot", "slot_index": 9, "dwell_min": 30}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "on_the_way", "dwell_min": 10}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "pinned", "dwell_min": 4}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "pinned"}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "pinned", "dwell_min": "long"}], "validation_error", 422),
    ([{"kind": "pinned", "dwell_min": 30}], "validation_error", 422),
    (["cafe_1"], "validation_error", 422),
    ([{"place_id": "gone", "kind": "pinned", "dwell_min": 30}], "place_closed_forever", 422),
    ([{"place_id": "ghost", "kind": "pinned", "dwell_min": 30}], "unknown_place", 404),
    ([{"place_id": f"p{i}", "kind": "pinned", "dwell_min": 30} for i in range(151)], "validation_error", 422),
    # JSON types: never a TypeError / OverflowError (a 500)
    ([{"place_id": 12345, "kind": "pinned", "dwell_min": 30}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": ["pinned"], "dwell_min": 30}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "on_the_way", "activity": ["coffee"], "dwell_min": 10}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "on_the_way", "activity": {"code": "coffee"}, "dwell_min": 10}],
     "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "pinned", "dwell_min": float("nan")}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "pinned", "dwell_min": float("inf")}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "pinned", "dwell_min": 10 ** 400}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "pinned", "dwell_min": 30, "dwell_fixed": "yes"}], "validation_error", 422),
    ([{"place_id": "cafe_1", "kind": "slot", "slot_index": 1.0, "dwell_min": 30}], "validation_error", 422),
])
def test_sequence_validation(cat, built, stops, code, status):
    got, http, params = code_of(lambda: schedule(built.context, stops, cat))
    assert (got, http) == (code, status)
    json.dumps(PlannerInputError(got, params).to_dict("en"), allow_nan=False)


def test_sequence_json_values_are_typed(cat, built):
    ctx = built.context
    stops = json.loads('[{"place_id": "cafe_1", "kind": "pinned", "dwell_min": Infinity}]')
    assert code_of(lambda: schedule(ctx, stops, cat))[:2] == ("validation_error", 422)
    ok = schedule(ctx, [{"place_id": "cafe_1", "kind": None, "dwell_min": 30, "dwell_fixed": None},
                        {"place_id": "cafe_2", "kind": "pinned", "dwell_min": 30, "dwell_fixed": 1}], cat)
    assert [(c.place_id, c.pinned, c.dwell_fixed) for c in ok.sequence] == [("cafe_1", True, False),
                                                                          ("cafe_2", True, True)]


def test_missing_place_after_a_catalog_change_is_a_conflict(cat, built):
    echo = to_request_echo(built.context)
    echo["catalog_version"] = "older-bundle"
    ctx = from_request_echo(echo, cat)
    got, http, params = code_of(lambda: schedule(ctx, [{"place_id": "ghost", "kind": "pinned", "dwell_min": 30}], cat))
    assert (got, http) == ("catalog_changed", 409) and params["plan_catalog_version"] == "older-bundle"


def test_insert_place_best_position_and_errors(cat, built):
    ctx = built.context
    seq = edit_stops(ctx, built.variants[0])
    cands, _ = sequence_candidates(ctx, seq, cat)
    st, idx = insert_place(ctx, seq, "bistro_6", cat)
    expected = best_insertion(cands, Candidate("bistro_6", "Bistro 6", *cat.coord_of("bistro_6")),
                              sequence_request(ctx, cands))
    assert [s.place_id for s in st.plan.stops] == [c.place_id for c in expected]
    assert st.plan.stops[idx].place_id == "bistro_6" and st.plan.stops[idx].pinned and st.plan.stops[idx].slot == -1
    assert st.interest["bistro_6"] == 0.0 and st.edited
    assert edit_stops(ctx, st)[idx].to_dict() == {"place_id": "bistro_6", "kind": "pinned", "slot_index": None,
                                                  "activity": None, "dwell_min": st.plan.stops[idx].dwell_min,
                                                  "dwell_fixed": False}
    st2, idx2 = insert_place(ctx, seq, "bistro_6", cat, dwell_min=25)
    assert st2.plan.stops[idx2].dwell_min == 25.0 and st2.sequence[idx2].dwell_fixed
    assert code_of(lambda: insert_place(ctx, seq, "ghost", cat))[:2] == ("unknown_place", 404)
    assert code_of(lambda: insert_place(ctx, seq, seq[0].place_id, cat))[:2] == ("place_already_in_route", 409)
    assert code_of(lambda: insert_place(ctx, seq, "gone", cat))[:2] == ("place_closed_forever", 422)
    no_pub = [e for e in seq if e.place_id != "paused_pub"]
    got = code_of(lambda: insert_place(ctx, no_pub, "paused_pub", cat))
    assert got[:2] == ("place_temporarily_closed", 422) and got[2]["name"] == "Paused Pub"
    st3, idx3 = insert_place(ctx, no_pub, "paused_pub", cat, allow_temporarily_closed=True)
    assert st3.plan.stops[idx3].place_id == "paused_pub"
    assert [m.stop_index for m in variant_messages(ctx, st3, cat) if m.code == "place_temporarily_closed"] == [idx3]
    assert code_of(lambda: insert_place(ctx, seq, "bistro_6", cat, dwell_min=1000))[0] == "validation_error"
    for bad in (float("inf"), float("nan"), 10 ** 400, "30", True):
        assert code_of(lambda: insert_place(ctx, seq, "bistro_6", cat, dwell_min=bad))[:2] == ("validation_error", 422)
    for bad_id in (12345, 1.5e19, None, "  ", ["bistro_6"]):        # place ids are strings, never numbers
        got = code_of(lambda: insert_place(ctx, seq, bad_id, cat))
        assert got[:2] == ("validation_error", 422) and got[2]["field"] == "place_id"
    st4, idx4 = insert_place(ctx, seq, " bistro_6 ", cat)
    assert st4.plan.stops[idx4].place_id == "bistro_6"


def test_limits_are_published():
    assert LIMITS["window_min"] == (15, 1440) and LIMITS["variants"] == (1, 5) and LIMITS["radius_km"] == (0.3, 50.0)
    assert LIMITS["max_slots"] == 8 and LIMITS["dwell_min"] == (5, 480) and LIMITS["max_must_visits"] == 10
    assert LIMITS["max_personal_ids"] == 500 and LIMITS["max_edit_stops"] == 150


def test_no_limit_can_reject_a_plan_the_planner_built():
    # the longest plan: every slot + the whole on-the-way pool of core._fill_extras + every must-visit
    import inspect

    from walk_planner import core
    max_pool = inspect.signature(core._fill_extras).parameters["max_pool"].default
    longest = LIMITS["max_slots"] + max_pool + LIMITS["max_must_visits"]
    assert longest == 98 and LIMITS["max_edit_stops"] >= longest + 50          # + room for inserted places
    # visit lengths of built stops: per-place estimates (x dwell_scale of the slowest style), on-the-way stops
    # capped at EXTRA_DWELL_MIN, user-set slot lengths validated against the same limit
    lo, hi = LIMITS["dwell_min"]
    slowest = max(p["dwell_scale"] for p in core.STYLE_PRESETS.values())
    estimates = [core.estimate_dwell_min(theme, sub, n, kind) for theme in [*core.DWELL_MINUTES, None]
                 for sub in (None, "museum", "statue") for n in (None, 1, 10 ** 6) for kind in ("", "small courtyard")]
    assert lo <= min(estimates) and max(estimates) * slowest <= hi
    assert lo <= core.EXTRA_DWELL_MIN <= hi


def _long_catalog() -> CityCatalog:
    """56 sights + parks within ~200 m, all popular and always open (a 12 h window fills with stops), and
    one restaurant to insert."""
    rows = []
    for i in range(28):
        for kind, theme, ptype, d in (("sight", "culture_sights", "monument", 1), ("park", "nature_outdoors", "park", -1)):
            rows.append({"place_id": f"{kind}_{i}", "name": f"{kind} {i}", "latitude": S[0] + d * 0.00007 * i,
                         "longitude": S[1] + 0.00011 * ((i * 7) % 17) - 0.0009, "city": "Testville", "theme": theme,
                         "theme_group": "sights", "primary_type": ptype, "ai_place_type_summary": ptype,
                         "ai_card_summary": "", "google_user_rating_count": 2000 + 37 * i, "bayesian_rating": 4.6,
                         "opening_hours": None, "business_status": None, "google_place_id": f"ChIJ{kind}{i}"})
    rows.append(_p("bistro_x", 0.0004, 0.0003, "food_drink", "food_drink", "restaurant"))   # not a sight / park
    return CityCatalog.from_frame(pd.DataFrame(rows))


def test_a_long_built_plan_round_trips_through_the_editing_api():
    # v0: max_edit_stops = 50 refused /schedule and /insert of any built variant with more stops
    # (the golden S08_full_day builds 55)
    lc = _long_catalog()
    r = build_plan(normalize_params({"city": "Testville", "date": "2026-10-03", "start_time": "08:00",
                                     "end_time": "20:00", "shape": "loop", "start": "city_center",
                                     "slots": ["sight", "park"], "variants": 1}, lc), lc)
    v0 = r.variants[0]
    assert len(v0.plan.stops) > 50
    ctx = from_request_echo(json.loads(json.dumps(to_request_echo(r.context))), lc)
    seq = [json.loads(json.dumps(e.to_dict())) for e in edit_stops(ctx, v0)]
    st = schedule(ctx, seq, lc)
    assert _times(st.plan) == _times(v0.plan)
    st2, idx = insert_place(ctx, seq, "bistro_x", lc)
    assert len(st2.plan.stops) == len(seq) + 1 and st2.plan.stops[idx].place_id == "bistro_x"


# --------------------------------------------------------------------------- #
# Golden subset (the full check: golden/tools/parity_check.py)
# --------------------------------------------------------------------------- #
def _parity_module():
    spec = importlib.util.spec_from_file_location("wp_parity_check", GOLDEN / "tools" / "parity_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def real_catalog():
    if REAL_CSV is None or not REAL_CSV.exists():
        pytest.skip("real Bucharest catalog not available")
    return CityCatalog.from_frame(pd.read_csv(REAL_CSV), city="Bucharest")


def _scenarios():
    return {s["id"]: s for s in json.loads((GOLDEN / "scenarios.json").read_text(encoding="utf-8"))}


def test_golden_subset_matches_the_baseline(real_catalog):
    pc = _parity_module()
    catalog = real_catalog
    scenarios = _scenarios()
    for sid in ("S01_default", "S05_free", "S13_dwell_overrides", "S17_must_only", "S19_must_closed"):
        digest, result = pc.capture(scenarios[sid], catalog)
        base = json.loads((GOLDEN / "baseline_v0" / f"{sid}.json").read_text(encoding="utf-8"))
        closed = pc.closed_forever_of(scenarios[sid], catalog)
        rep = pc.check_scenario(scenarios[sid], base, pc.norm(digest), closed_forever=closed)
        assert rep["diffs"] == [], (sid, rep["diffs"][:5])
        kinds = {a[0] for a in rep["allowed"]}           # "env": centre repr differs by < 1e-9 (numpy build)
        allowed = {"S05_free": {"bug1", "env"}, "S19_must_closed": {"bug2", "env"}}.get(sid, {"env"})
        assert kinds <= allowed, (sid, kinds)
        if sid == "S19_must_closed":                 # bug 2: La Mama dropped, Ryan's Pub kept + flagged
            assert "bug2" in kinds and closed == {"14018219728270213257": "La Mama"}
            assert [r[0] for r in digest["candidates"]["must"]] == ["10366085341954844986"]
            assert any(n["text"].startswith("Закрыто навсегда по данным Google") for n in digest["notes"])
            for v in result.variants:
                ids = [s.place_id for s in v.plan.stops]
                assert "14018219728270213257" not in ids and "10366085341954844986" in ids
            # the guard: a bug-2 scenario may not differ anywhere else (here: the slot candidates)
            tampered = pc.norm(digest)
            tampered["candidates"]["slot"] = tampered["candidates"]["slot"][1:]
            assert pc.check_scenario(scenarios[sid], base, tampered, closed_forever=closed)["status"] == "diff"


def test_golden_full_day_plan_round_trips_through_the_editing_api(real_catalog):
    # S08_full_day's first variant has 55 stops: v0's max_edit_stops = 50 refused to edit it at all
    pc = _parity_module()
    params = normalize_params({**pc.scenario_request(_scenarios()["S08_full_day"]), "variants": 1}, real_catalog)
    r = build_plan(params, real_catalog)
    v0 = r.variants[0]
    assert len(v0.plan.stops) > 50
    ctx = from_request_echo(json.loads(json.dumps(to_request_echo(r.context))), real_catalog)
    seq = [json.loads(json.dumps(e.to_dict())) for e in edit_stops(ctx, v0)]
    st = schedule(ctx, seq, real_catalog)
    assert _times(st.plan) == _times(v0.plan) and st.plan.total_time_min == v0.plan.total_time_min
