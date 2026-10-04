"""Unit tests of walk_planner.present: the dashboard formatters (exact outputs), the structured hours,
TimePoints, stop derivations (kind / slot / activity / hours status), segments / navigation / bbox /
summary views, variant messages, the plan / edit responses and the Russian render helpers.
Offline, on a small synthetic catalog."""

import json
from datetime import datetime, time as dtime

import pandas as pd
import pytest

from walk_planner.catalog import CityCatalog
from walk_planner.core import Candidate, Stop, WalkPlan
from walk_planner.pipeline import (
    VariantState,
    build_plan,
    make_context,
    normalize_params,
    schedule,
)
from walk_planner.present import (
    _polyline6,
    bbox_view,
    clock_label,
    edit_response,
    end_label,
    hours_label_ru,
    hours_on_day,
    hours_status,
    kind_label_ru,
    metrics_ru,
    navigation_ru,
    navigation_view,
    places_word_ru,
    plan_response,
    render_variant_ru,
    segment_views,
    stop_activity,
    stop_card_lines_ru,
    stop_card_markdown_ru,
    stop_kind,
    stop_slot_index,
    summary_view,
    time_point,
    variant_messages,
    variants_caption_ru,
    window_caption_ru,
)

SAT = 5 * 1440
S = (44.4300, 26.1000)


def _p(pid, dlat, dlon, theme, group, ptype, reviews=500, hours=None, status=None):
    return {"place_id": pid, "name": pid.replace("_", " ").title(), "latitude": S[0] + dlat, "longitude": S[1] + dlon,
            "city": "Testville", "theme": theme, "theme_group": group, "primary_type": ptype,
            "ai_place_type_summary": ptype, "ai_card_summary": "", "google_user_rating_count": reviews,
            "bayesian_rating": 4.6, "opening_hours": None if hours is None else json.dumps(hours),
            "business_status": status, "google_place_id": "ChIJ" + pid}


@pytest.fixture(scope="module")
def cat():
    open_sat = [[SAT + 480, SAT + 1320]]
    rows = [_p(f"museum_{i}", 0.002 * i, 0.001, "culture_sights", "sights", "museum", 1000 * i, open_sat)
            for i in range(1, 5)]
    rows += [_p(f"cafe_{i}", 0.001 * i, -0.002, "food_drink", "food_drink", "cafe", 300 * i, open_sat)
             for i in range(1, 4)]
    rows += [_p(f"park_{i}", -0.002 * i, 0.002, "nature_outdoors", "sights", "park", 2000 * i) for i in range(1, 4)]
    rows += [_p(f"bistro_{i}", -0.001 * i, -0.001, "food_drink", "food_drink", "restaurant", 800 * i, open_sat)
             for i in range(1, 4)]
    rows += [_p("paused_pub", 0.0015, 0.0, "food_drink", "food_drink", "pub", 900, status="temporarily_closed"),
             _p("late_bar", 0.003, -0.003, "food_drink", "food_drink", "bar", 400, [[SAT + 1200, SAT + 1500]])]
    return CityCatalog.from_frame(pd.DataFrame(rows))


def _request(**kw):
    raw = {"city": "Testville", "date": "2026-10-03", "start_time": "10:00", "end_time": "14:00", "shape": "loop",
           "start": "city_center", "slots": ["sight", "coffee", "park", "food"], "variants": 2}
    raw.update(kw)
    return raw


@pytest.fixture(scope="module")
def built(cat):
    return build_plan(normalize_params(_request(), cat), cat)


# --------------------------------------------------------------------------- #
# Formatters
# --------------------------------------------------------------------------- #
def test_end_and_clock_labels():
    assert end_label(840) == "14:00" and end_label(1440) == "00:00 (+1 день)" and end_label(2040) == "10:00 (+1 день)"
    t = dtime(10, 0)
    assert clock_label(t, 225.4) == "13:45" and clock_label(dtime(22, 0), 150) == "00:30 (+1)"
    assert clock_label(t, 97.5) == "11:38" and clock_label(t, 58.5) == "10:58"     # round half to even
    assert clock_label(dtime(23, 0), 2 * 1440) == "23:00 (+2)"


def test_hours_label_ru_verified_outputs():
    fri_sat = [[4 * 1440 + 1080, SAT + 120], [SAT + 1080, 6 * 1440 + 120]]            # 18:00-02:00 Fri and Sat
    assert hours_label_ru(fri_sat, SAT + 60) == "сб до 02:00, 18:00–02:00"
    assert hours_label_ru(fri_sat, SAT + 600) == "сб 18:00–02:00"                      # last night's opening over
    week24 = [[d * 1440, d * 1440 + 1440] for d in range(7)]
    assert hours_label_ru(week24, SAT + 600) == "сб круглосуточно"
    assert hours_label_ru([[SAT + 540, SAT + 780], [SAT + 840, SAT + 1200]], SAT + 600) == "сб 09:00–13:00, 14:00–20:00"
    assert hours_label_ru([[SAT + 540, SAT + 780]], 600) == "пн закрыто"
    sunday_bar = [[6 * 1440 + 1080, 7 * 1440 + 120]]                                   # Sun 18:00 -> Mon 02:00
    assert hours_label_ru(sunday_bar, 60) == "пн до 02:00"                             # week wrap
    assert hours_label_ru(None, 0) == "часы работы неизвестны"


def test_hours_on_day_structure():
    fri_sat = [[4 * 1440 + 1080, SAT + 120], [SAT + 1080, 6 * 1440 + 120]]
    assert hours_on_day(fri_sat, SAT + 60) == {
        "weekday": 5, "open_24h": False, "closed_all_day": False, "carryover_until": "02:00",
        "intervals": [{"open": "18:00", "close": "02:00", "close_day_offset": 1}]}
    week24 = [[d * 1440, d * 1440 + 1440] for d in range(7)]
    d = hours_on_day(week24, SAT + 600)
    assert d["open_24h"] is True and d["closed_all_day"] is False and d["intervals"] == []
    closed = hours_on_day([[SAT + 540, SAT + 780]], 600)
    assert closed == {"weekday": 0, "open_24h": False, "closed_all_day": True, "intervals": [], "carryover_until": None}
    midnight = hours_on_day([[SAT + 600, 6 * 1440]], SAT + 700)
    assert midnight["intervals"] == [{"open": "10:00", "close": "00:00", "close_day_offset": 1}]
    assert hours_on_day(None, 0) is None


def test_time_point_window_caption_and_plural():
    ws = datetime(2026, 10, 3, 22, 0)
    assert time_point(ws, 0.5) == {"offset_min": 0.5, "local": "2026-10-03T22:00"}           # half to even
    assert time_point(ws, 150.2) == {"offset_min": 150.2, "local": "2026-10-04T00:30"}
    assert window_caption_ru(240, 5, False) == "Окно: 4 ч 00 мин · суббота"
    assert window_caption_ru(375, 4, True) == "Окно: 6 ч 15 мин · пятница · заканчиваю на следующий день"
    assert [places_word_ru(n) for n in (1, 2, 4, 5, 11, 12, 21, 22, 111, 112)] == [
        "место", "места", "места", "мест", "мест", "мест", "место", "места", "мест", "мест"]


def test_polyline6_is_the_routing_codec():
    # one codec (routing.polyline6_encode: round half away from zero, like Google's reference encoder)
    from walk_planner.routing import polyline6_decode, polyline6_encode
    coords = [[26.097708, 44.440287], [26.084638, 44.453113], [26.084638, 44.453113], [-0.0000005, 0.0000005]]
    assert _polyline6(coords) == polyline6_encode(coords)
    assert polyline6_decode(_polyline6(coords[:3])) == coords[:3]


# --------------------------------------------------------------------------- #
# Stop derivations
# --------------------------------------------------------------------------- #
def _stop(order=0, slot=0, extra=False, pinned=False, hours_ok=None, wait=0.0, arrival=10.0, dwell=30.0, pid="p"):
    return Stop(order=order, place_id=pid, name=pid, lat=S[0], lon=S[1], slot=slot, theme="t", interest=0.5,
                dwell_min=dwell, arrival_min=arrival, depart_min=arrival + wait + dwell, wait_min=wait,
                hours_ok=hours_ok, extra=extra, pinned=pinned)


def test_kind_slot_and_activity_precedence():
    codes = ["sight", "coffee"]
    slot = _stop(slot=1)
    extra = _stop(slot=0, extra=True, pid="e")
    pinned_slot = _stop(slot=0, pinned=True)          # a must-visit the solver also picked for slot 0
    pinned = _stop(slot=-1, pinned=True)
    assert [stop_kind(s) for s in (slot, extra, pinned_slot, pinned)] == ["slot", "on_the_way", "pinned", "pinned"]
    assert [stop_slot_index(s, 2) for s in (slot, extra, pinned_slot, pinned)] == [1, None, 0, None]
    assert stop_activity(slot, codes, {}) == "coffee" and stop_activity(pinned_slot, codes, {}) == "sight"
    assert stop_activity(extra, codes, {"e": "park"}) == "park" and stop_activity(extra, codes, {}) == "sight"
    assert stop_activity(pinned, codes, {}) is None


def test_hours_status_all_outcomes():
    hours = [[SAT + 600, SAT + 660]]                   # Saturday 10:00-11:00
    t0 = SAT + 600
    assert hours_status(_stop(hours_ok=True), hours, None) == "not_checked"
    assert hours_status(_stop(hours_ok=None), None, t0) == "unknown"
    assert hours_status(_stop(hours_ok=True), hours, t0) == "open"
    assert hours_status(_stop(hours_ok=True, wait=0.9), hours, t0) == "open"
    assert hours_status(_stop(hours_ok=True, wait=12), hours, t0) == "open_after_wait"
    assert hours_status(_stop(hours_ok=False, arrival=50, dwell=30), hours, t0) == "closes_during_visit"
    assert hours_status(_stop(hours_ok=False, arrival=60, dwell=30), hours, t0) == "closes_during_visit"  # closing minute
    assert hours_status(_stop(hours_ok=False, arrival=70), hours, t0) == "closed_at_arrival"


# --------------------------------------------------------------------------- #
# Views of real (synthetic-catalog) plans
# --------------------------------------------------------------------------- #
def test_segments_from_to_kinds_and_times(cat, built):
    ctx, st = built.context, built.variants[0]
    segs = segment_views(ctx, st.plan)
    n = len(st.plan.stops)
    assert len(segs) == n + 1                                          # start -> stops -> back to the start
    assert segs[0]["from"] == {"kind": "start", "stop_index": None} and segs[0]["to"] == {"kind": "stop", "stop_index": 0}
    assert segs[-1]["to"] == {"kind": "start", "stop_index": None} and segs[-1]["from"]["stop_index"] == n - 1
    for i, g in enumerate(segs[1:-1], start=1):
        assert (g["from"]["stop_index"], g["to"]["stop_index"]) == (i - 1, i)
    stops = st.plan.stops
    assert segs[0]["depart"]["offset_min"] == 0.0
    assert segs[1]["arrive"] == time_point(ctx.window_start, stops[1].arrival_min)
    assert segs[-1]["arrive"]["offset_min"] == pytest.approx(st.plan.total_time_min)
    assert all(g["quality"] == "estimate" and g["provider"] == "haversine" for g in segs)
    assert segs[0]["geometry"]["type"] == "LineString" and len(segs[0]["geometry"]["coordinates"]) == 2
    assert segs[0]["distance_m"] == round(st.plan.segments[0].distance_km * 1000)
    poly = segment_views(ctx, st.plan, geometry="polyline6")[0]
    assert "geometry" not in poly and isinstance(poly["geometry_polyline6"], str)
    from walk_planner.routing import polyline6_decode
    decoded = polyline6_decode(poly["geometry_polyline6"])
    geo = segs[0]["geometry"]["coordinates"]
    assert len(decoded) == len(geo) and all(abs(a - b) <= 1e-6 for p, q in zip(decoded, geo) for a, b in zip(p, q))


def test_free_shape_segments_count_stops_not_points(cat):
    ctx = make_context(normalize_params(_request(shape="free", start=None), cat), cat)
    seq = [Candidate("museum_1", "a", S[0] + 0.002, S[1] + 0.001, slot=0, dwell_min=20),
           Candidate("cafe_1", "b", S[0] + 0.001, S[1] - 0.002, slot=1, dwell_min=20),
           Candidate("park_1", "c", S[0] - 0.002, S[1] + 0.002, slot=2, dwell_min=20)]
    from walk_planner.core import plan_sequence
    from walk_planner.pipeline import sequence_request
    plan = plan_sequence(seq, sequence_request(ctx, seq))
    segs = segment_views(ctx, plan)
    assert [(g["from"]["stop_index"], g["to"]["stop_index"]) for g in segs] == [(0, 1), (1, 2)]
    assert segs[0]["depart"]["offset_min"] == plan.stops[0].depart_min
    nav = navigation_view(ctx, plan, cat)
    assert [leg["segment_index"] for leg in nav["legs"]] == [0, 1]


def test_navigation_view_and_empty_plan(cat, built):
    ctx, st = built.context, built.variants[0]
    nav = navigation_view(ctx, st.plan, cat)
    assert len(nav["legs"]) == len(st.plan.segments) and [leg["segment_index"] for leg in nav["legs"]] == \
        list(range(len(st.plan.segments)))
    assert nav["google_parts"] and "destination_place_id=ChIJ" in nav["legs"][0]["google"]
    assert navigation_ru(ctx, st.plan, cat)["legs"][0]["from"] == "Старт"
    assert navigation_ru(ctx, st.plan, cat, start_label="Start")["legs"][0]["from"] == "Start"
    empty = WalkPlan(stops=[], segments=[], total_time_min=0, total_walk_min=0, total_dwell_min=0,
                     total_distance_km=0, over_budget=False)
    assert navigation_view(ctx, empty, cat) == {"google_parts": [], "legs": []}
    assert segment_views(ctx, empty) == [] and bbox_view(ctx, empty) is None


def test_bbox_covers_stops_start_and_geometry(built):
    ctx, plan = built.context, built.variants[0].plan
    lon0, lat0, lon1, lat1 = bbox_view(ctx, plan)
    for s in plan.stops:
        assert lon0 <= s.lon <= lon1 and lat0 <= s.lat <= lat1
    assert lon0 <= ctx.start_resolved[1] <= lon1 and lat0 <= ctx.start_resolved[0] <= lat1


def test_summary_counts_pinned_slot_as_filled(cat):
    ctx = make_context(normalize_params(_request(), cat), cat)
    stops = [_stop(0, slot=0, pinned=True, pid="museum_1"), _stop(1, slot=1, pid="cafe_1"),
             _stop(2, slot=0, extra=True, pid="museum_2"), _stop(3, slot=-1, pinned=True, pid="park_1")]
    plan = WalkPlan(stops=stops, segments=[], total_time_min=250.0, total_walk_min=20.0, total_dwell_min=230.0,
                    total_distance_km=1.5, over_budget=True, dropped_slots=[2, 3])
    st = VariantState(0, plan, [], dropped_slot_indices=[2, 3])
    s = summary_view(ctx, st)
    assert (s["stops_total"], s["slots_requested"], s["slots_filled"], s["on_the_way"], s["pinned"]) == (4, 4, 2, 1, 2)
    assert s["slack_min"] == -10.0 and s["over_budget"] and s["finish_kind"] == "return" and s["routing"] == "none"
    assert s["finish_at"] == {"offset_min": 250.0, "local": "2026-10-03T14:10"} and s["dropped_slot_indices"] == [2, 3]
    assert metrics_ru(ctx, plan)["stops"] == "1 из 4 + 1 + 📍2"            # the page counts kinds


def test_variant_messages_rules(cat, built):
    ctx, st = built.context, built.variants[0]
    codes = [m.code for m in variant_messages(ctx, st, cat)]
    n_extra = sum(s.extra for s in st.plan.stops)
    assert ("extras_added" in codes) == bool(n_extra) and codes[-1] == "routing_estimate"
    edited = VariantState(0, st.plan, st.sequence, dropped_slot_indices=[1], edited=True)
    assert not {"extras_added", "slots_dropped"} & {m.code for m in variant_messages(ctx, edited, cat)}
    dropped = VariantState(0, st.plan, st.sequence, dropped_slot_indices=[1], edited=False)
    m = [x for x in variant_messages(ctx, dropped, cat) if x.code == "slots_dropped"][0]
    assert m.params == {"activities": ["coffee"], "slot_indices": [1], "window_end_label": "14:00"}
    empty = WalkPlan(stops=[], segments=[], total_time_min=0, total_walk_min=0, total_dwell_min=0,
                     total_distance_km=0, over_budget=False)
    assert [x.code for x in variant_messages(ctx, VariantState(0, empty, []), cat)] == ["route_empty"]


def test_stop_conflict_and_temporarily_closed_messages(cat):
    ctx = make_context(normalize_params(_request(start_time="10:00", end_time="13:00"), cat), cat)
    st = schedule(ctx, [{"place_id": "late_bar", "kind": "pinned", "dwell_min": 60},
                        {"place_id": "paused_pub", "kind": "pinned", "dwell_min": 60}], cat)
    assert [s.place_id for s in st.plan.stops] == ["late_bar", "paused_pub"]
    msgs = variant_messages(ctx, st, cat)
    conflict = [m for m in msgs if m.code == "stop_hours_conflict"][0]
    assert conflict.stop_index == 0 and conflict.params["reason"] == "closed_at_arrival"
    assert conflict.params["hours_label"] == "сб 20:00–01:00" and conflict.params["closed_all_day"] is False
    assert conflict.text("ru").startswith("⚠️ «Late Bar» — в 10:")
    tc = [m for m in msgs if m.code == "place_temporarily_closed"][0]
    assert tc.stop_index == 1 and tc.params == {"place_id": "paused_pub", "name": "Paused Pub"}
    lines = render_variant_ru(ctx, [st], 0, cat)
    assert any(w.startswith("⛔ «Paused Pub» — временно закрыто") for w in lines["warnings"])
    assert lines["stop_lines"][1][-1] == "⛔ Временно закрыто по данным Google"
    assert lines["stop_lines"][0][-1] == "⚠️ по графику в это время может быть закрыто — маршрут построен как есть"


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
def test_plan_response_shape(cat, built):
    resp = plan_response(built, cat, lang="en", photo_base_url="https://cdn/", debug=True)
    json.dumps(resp)                                                     # JSON-serialisable as is
    assert set(resp) == {"plan_id", "versions", "status", "request", "personalization", "messages", "variants",
                         "places", "debug"}
    assert resp["status"] == "ok" and resp["versions"]["api"] == "v1" and resp["versions"]["catalog"] == cat.version
    assert resp["personalization"] == {"mode": "popularity", "strength": 0.5, "favourites_used": [],
                                       "favourites_ignored": [], "profiles": 0}
    v = resp["variants"][0]
    assert set(v) == {"index", "edited", "summary", "messages", "stops", "segments", "navigation", "bbox", "sequence"}
    stop = v["stops"][0]
    assert set(stop) == {"index", "number", "place_id", "name", "lat", "lon", "kind", "slot_index", "activity",
                         "arrival", "visit_start", "departure", "dwell_min", "dwell_fixed", "wait_min", "hours",
                         "business_status", "interest"}
    assert set(v["sequence"][0]) == {"place_id", "kind", "slot_index", "activity", "dwell_min", "dwell_fixed"}
    assert {s["place_id"] for vv in resp["variants"] for s in vv["stops"]} == set(resp["places"])
    assert resp["request"]["window_start"] == "2026-10-03T10:00" and resp["request"]["timezone"] == "UTC"
    assert plan_response(built, cat)["plan_id"] == resp["plan_id"]       # language does not change the id
    assert all(m["text"] for vv in resp["variants"] for m in vv["messages"])


def test_stop_interest_is_reported_unpenalised(cat, built):
    # the core keeps interest x0.35 on places an earlier variant used; the API reports the original
    for v, view in zip(built.variants, plan_response(built, cat)["variants"]):
        got = {s["place_id"]: s["interest"] for s in view["stops"]}
        for s in v.plan.stops:
            expected = 0.0 if (s.pinned and s.slot < 0) else built.interest.map.get(s.place_id, 0.0)
            assert got[s.place_id] == expected


def test_edit_response(cat, built):
    ctx = built.context
    st = schedule(ctx, plan_response(built, cat)["variants"][0]["sequence"][::-1], cat)
    resp = edit_response(ctx, st, cat, inserted_index=2)
    assert set(resp) == {"versions", "request", "variant", "places", "messages", "inserted_index"}
    assert resp["variant"]["edited"] is True and resp["inserted_index"] == 2
    assert not {"extras_added", "slots_dropped"} & {m["code"] for m in resp["variant"]["messages"]}


# --------------------------------------------------------------------------- #
# Russian render helpers
# --------------------------------------------------------------------------- #
def test_metrics_and_quirks(cat):
    ctx = make_context(normalize_params(_request(start_time="22:00", end_time="04:00", end_day_offset=1), cat), cat)
    stops = [_stop(0, slot=0, pid="museum_1"), _stop(1, slot=0, extra=True, pid="museum_2")]
    plan = WalkPlan(stops=stops, segments=[], total_time_min=360.3, total_walk_min=88.5, total_dwell_min=270.0,
                    total_distance_km=6.55, over_budget=True)
    m = metrics_ru(ctx, plan)
    assert m["stops"] == "1 из 4 + 1" and m["finish_label"] == "Возвращение" and m["finish"] == "04:00 (+1)"
    assert m["delta"] == "-0 мин, не успеваю"                             # slack in (-0.5, 0): the page's "-0"
    assert m["walk"] == "6.5 км · 1:28" and m["walk_help"] == "88 мин ходьбы, 6.5 км"
    assert m["window"] == "22:00–04:00⁺¹" and m["window_help"] == "22:00–04:00 (+1 день)"
    ok = WalkPlan(stops=stops, segments=[], total_time_min=300.0, total_walk_min=20, total_dwell_min=280,
                  total_distance_km=1.0, over_budget=False)
    assert metrics_ru(ctx, ok)["delta"] == "+60 мин запаса"


def test_kind_labels_cards_and_caption(cat, built):
    ctx, states = built.context, built.variants
    codes = ctx.slot_codes
    assert kind_label_ru(_stop(slot=1), codes, {}) == "Кофе"
    assert kind_label_ru(_stop(slot=0, extra=True, pid="x"), codes, {"x": "park"}) == \
        "Парк / природа · по пути, можно пройти мимо"
    assert kind_label_ru(_stop(slot=-1, pinned=True), codes, {}) == "📍 ваше место"
    assert kind_label_ru(_stop(slot=-1), codes, {}) == "t"                      # no slot -> its theme
    s = _stop(order=2, slot=1, wait=12.0, arrival=30.0, hours_ok=True, pid="cafe_1")
    lines = stop_card_lines_ru(ctx, s, "Кофе", cat.hours_of("cafe_1"))
    assert lines == ["3. cafe_1 — Кофе", "10:42–11:12 · осмотр 30 мин · ⏳ приходим в 10:30, ждём открытия 12 мин",
                     "🕒 сб 08:00–22:00"]
    assert stop_card_markdown_ru(ctx, s, "Кофе", cat.hours_of("cafe_1")) == (
        "**3. cafe_1** — _Кофе_  \n10:42–11:12 · осмотр 30 мин · ⏳ приходим в 10:30, ждём открытия 12 мин  \n"
        "🕒 сб 08:00–22:00")
    two = [states[0], VariantState(1, states[0].plan, states[0].sequence)]
    cap = variants_caption_ru(ctx, two, 1)
    assert cap.startswith("Вариант 1: ") and "  ·  **Вариант 2**: " in cap and "✏️" not in cap
    n = len(states[0].plan.stops)
    assert cap.split("  ·  ")[0] == (f"Вариант 1: {n} {places_word_ru(n)} · {states[0].plan.total_distance_km:.1f} км"
                                    f" · до {ctx.clock(states[0].plan.total_time_min)}")
    edited = [VariantState(0, states[0].plan, states[0].sequence, edited=True), two[1]]
    assert variants_caption_ru(ctx, edited, 0).split("  ·  ")[0].endswith(" · ✏️ изменён")
    assert variants_caption_ru(ctx, two[:1], 0) is None


def test_config_view(cat):
    from walk_planner.present import config_view
    cfg = config_view(cat)
    json.dumps(cfg)
    assert [a["code"] for a in cfg["activities"]] == ["sight", "coffee", "food", "bar", "park", "market",
                                                      "entertainment", "shopping"]
    assert cfg["activities"][0] == {"code": "sight", "label_ru": "Достопримечательность", "label_en": "Sight",
                                    "group": "sights", "base_dwell_min": 45.0}
    assert [s["code"] for s in cfg["styles"]] == ["max", "chill", "scenic"]
    assert [s["code"] for s in cfg["shapes"]] == ["one_way", "loop", "free"]
    assert cfg["dwell_choices"][0] == 10 and cfg["limits"]["variants"] == [1, 5]
    assert cfg["defaults"]["slots"] == ["sight", "coffee", "park", "food"] and cfg["city"] == "Testville"
    assert cfg["center"] == {"lat": cat.center[0], "lon": cat.center[1]} and cfg["versions"]["catalog"] == cat.version


def test_render_variant_ru_has_the_golden_digest_keys(cat, built):
    r = render_variant_ru(built.context, built.variants, 0, cat)
    assert list(r) == ["manual", "variants_caption", "metric_stops", "metric_finish_label", "metric_finish",
                       "metric_delta", "metric_walk", "metric_window", "captions", "warnings", "infos", "stop_lines"]
    assert len(r["stop_lines"]) == len(built.variants[0].plan.stops) and r["manual"] is False
