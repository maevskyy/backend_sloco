"""Unit tests of walk_planner.slots and walk_planner.candidates: the slot registry and constants, and
every candidate-selection rule of the dashboard (theme vs keyword membership, theme_group gate,
radius, precision filters and their relaxation, business status, known hours, the opening-hours
prefilter, top-K by interest + K near the start, merge order, candidate fields), the on-the-way
pools and the must-visit / add place policy. Synthetic places around a fixed start; offline."""

import json

import pandas as pd
import pytest

from walk_planner.candidates import (
    distances_from,
    extra_activities,
    extra_candidates,
    place_candidate,
    place_dwell,
    row_candidate,
    slot_candidates,
)
from walk_planner.catalog import CityCatalog
from walk_planner.core import DEFAULT_DETOUR, DEFAULT_WALK_KMH, dwell_for, estimate_dwell_min
from walk_planner.slots import (
    ACTIVITY_BY_LABEL_RU,
    ACTIVITY_CODES,
    ACTIVITY_TYPES,
    CLOSED_STATUS,
    DEFAULT_SLOTS,
    DWELL_CHOICES,
    EXTRA_K,
    MIN_FILTERED,
    NEAR_MIN_REVIEWS,
    NEAR_WEIGHT,
    SHAPE_BY_LABEL_RU,
    SHAPES,
    STYLE_BY_LABEL_RU,
    STYLES,
    activity,
    activity_label,
    dwell_choice_label_ru,
)

S = (44.4300, 26.1000)                  # the start / search-area centre
SAT = 5 * 1440                          # Saturday 00:00 in week minutes
WINDOW = (SAT + 600, SAT + 840)         # Saturday 10:00-14:00
OPEN_SAT = json.dumps([[SAT + 540, SAT + 1080]])      # 09:00-18:00
MONDAY_ONLY = json.dumps([[600, 1080]])
SHORT_SAT = json.dumps([[SAT + 810, SAT + 840]])      # 13:30-14:00 only


def place(pid, dlat, dlon, theme, group, ptype, ai="", reviews=100, hours=None, status=None, name=None):
    return {"place_id": pid, "name": name or pid, "latitude": S[0] + dlat, "longitude": S[1] + dlon,
            "theme": theme, "theme_group": group, "primary_type": ptype, "ai_place_type_summary": ai,
            "ai_card_summary": "", "google_user_rating_count": reviews, "bayesian_rating": 4.5,
            "opening_hours": hours, "business_status": status}


def catalog(rows, **drop) -> CityCatalog:
    df = pd.DataFrame(rows)
    return CityCatalog.from_frame(df.drop(columns=[c for c in drop.get("drop", ()) if c in df.columns]))


def ids(cands):
    return [c.place_id for c in cands]


# --------------------------------------------------------------------------- #
# slots registry
# --------------------------------------------------------------------------- #
def test_activity_registry_codes_labels_and_dwell_keys():
    assert ACTIVITY_CODES == ("sight", "coffee", "food", "bar", "park", "market", "entertainment", "shopping")
    assert [a.label_ru for a in ACTIVITY_TYPES.values()] == [
        "Достопримечательность", "Кофе", "Еда / ресторан", "Бар / напитки", "Парк / природа", "Рынок / площадь",
        "Развлечение", "Шопинг"]
    assert {c: a.dwell_key for c, a in ACTIVITY_TYPES.items()} == {
        "sight": "culture_sights", "coffee": "coffee", "food": "restaurant", "bar": "bar", "park": "nature_outdoors",
        "market": "markets_walks", "entertainment": "performing_arts", "shopping": "shopping_souvenirs"}
    assert {c: a.base_dwell_min for c, a in ACTIVITY_TYPES.items()} == {
        "sight": 45.0, "coffee": 30.0, "food": 75.0, "bar": 60.0, "park": 40.0, "market": 40.0,
        "entertainment": 90.0, "shopping": 30.0}
    assert {c for c, a in ACTIVITY_TYPES.items() if a.ai_deny} == {"sight", "park", "market", "entertainment"}
    assert ACTIVITY_TYPES["sight"].themes == ("culture_sights", "religious_sights")
    assert ACTIVITY_TYPES["coffee"].themes == () and ACTIVITY_TYPES["food"].group == "food_drink"
    assert "park" in ACTIVITY_TYPES["sight"].type_deny and "town_square" in ACTIVITY_TYPES["market"].type_allow
    assert DEFAULT_SLOTS == ("sight", "coffee", "park", "food")
    assert activity("park") is activity("Парк / природа") is ACTIVITY_TYPES["park"]
    assert ACTIVITY_BY_LABEL_RU["Кофе"] == "coffee" and activity_label("bar", "en") == "Bar / drinks"
    with pytest.raises(KeyError):
        activity("museum")


def test_constants_match_the_dashboard():
    assert (MIN_FILTERED, NEAR_WEIGHT, EXTRA_K, NEAR_MIN_REVIEWS) == (3, 8.0, 30, 20)
    assert DWELL_CHOICES == [None, 10, 15, 20, 30, 45, 60, 75, 90, 120, 150, 180, 240]
    assert CLOSED_STATUS == {"closed_forever", "temporarily_closed"}
    assert list(STYLES) == ["max", "chill", "scenic"] and list(SHAPES) == ["one_way", "loop", "free"]
    assert STYLE_BY_LABEL_RU == {"Максимум мест": "max", "Размеренный": "chill", "Живописный": "scenic"}
    assert SHAPE_BY_LABEL_RU == {"В одну сторону": "one_way", "Петля": "loop", "По району": "free"}
    assert [dwell_choice_label_ru(m) for m in (None, 45, 60, 90, 240)] == ["авто", "45 мин", "1 ч", "1 ч 30 мин", "4 ч"]


# --------------------------------------------------------------------------- #
# Membership: theme vs keywords, theme_group gate
# --------------------------------------------------------------------------- #
def test_theme_path_uses_catalog_themes_only():
    cat = catalog([
        place("museum", 0.001, 0, "culture_sights", "sights", "museum"),
        place("church", 0.002, 0, "religious_sights", "sights", "church"),
        place("monument", 0.003, 0, "culture_sights", "sights", "monument"),
        place("park", 0.001, 0.001, "nature_outdoors", "sights", "park"),
        place("museum_cafe", 0.001, 0.002, "food_drink", "food_drink", "cafe", name="Museum Cafe"),
    ])
    out = slot_candidates(cat.rows, {}, 0, "sight", S, 5.0, top_k=10)
    assert set(ids(out)) == {"museum", "church", "monument"}            # no keyword guessing
    assert all(c.theme == "culture_sights" and c.slot == 0 for c in out)


def test_keyword_path_for_food_slots_with_substring_quirk_and_group_gate():
    cat = catalog([
        place("espresso", 0.001, 0, "food_drink", "food_drink", "coffee_shop"),
        place("bbq", 0.002, 0, "food_drink", "food_drink", "barbecue_restaurant"),
        place("pub", 0.003, 0, "food_drink", "food_drink", "pub"),
        place("bar_museum", 0.001, 0.001, "culture_sights", "sights", "museum", name="Bar Museum"),
    ])
    bar = slot_candidates(cat.rows, {}, 3, "bar", S, 5.0, top_k=10)
    assert set(ids(bar)) == {"bbq", "pub"}          # "bar" in "barbecue" (no word boundary); gated to food_drink
    coffee = slot_candidates(cat.rows, {}, 1, "coffee", S, 5.0, top_k=10)
    assert ids(coffee) == ["espresso"] and coffee[0].theme == "coffee"


def test_keyword_fallback_without_theme_column_uses_the_legacy_group():
    rows = [place("castle", 0.001, 0, None, "things_to_do", "castle", ai="medieval castle"),
            place("palace_bar", 0.002, 0, None, "food_drink", "bar", name="Palace Bar")]
    cat = catalog(rows, drop=["theme"])
    out = slot_candidates(cat.rows, {}, 0, "sight", S, 5.0, top_k=10)
    assert ids(out) == ["castle"]                   # "sights" missing -> things_to_do gate


# --------------------------------------------------------------------------- #
# Radius, precision filters + relaxation, status, hours
# --------------------------------------------------------------------------- #
def test_radius_limits_the_search_area():
    cat = catalog([place("near", 0.001, 0, "culture_sights", "sights", "museum"),
                   place("far", 0.05, 0, "culture_sights", "sights", "museum")])          # ~5.6 km
    assert ids(slot_candidates(cat.rows, {}, 0, "sight", S, 1.0, top_k=10)) == ["near"]
    assert set(ids(slot_candidates(cat.rows, {}, 0, "sight", S, 0, top_k=10))) == {"near", "far"}   # falsy radius
    d = distances_from(cat.rows, S)
    assert ids(slot_candidates(cat.rows, {}, 0, "sight", S, 1.0, top_k=10, dist=d)) == ["near"]


def test_precision_filters_apply_when_at_least_three_places_survive():
    base = [place(f"m{i}", 0.001 * i, 0, "markets_walks", "sights", "market") for i in range(1, 4)]
    extra = [place("tree_store", 0.004, 0, "markets_walks", "sights", "christmas_tree_store"),
             place("tour_co", 0.005, 0, "markets_walks", "sights", "market", ai="walking tour company")]
    out = slot_candidates(catalog(base + extra).rows, {}, 0, "market", S, 5.0, top_k=10)
    assert set(ids(out)) == {"m1", "m2", "m3"}      # type_allow + AI non-venue filter
    # only two strict survivors -> all three filters are dropped together (theme only)
    out = slot_candidates(catalog(base[:2] + extra).rows, {}, 0, "market", S, 5.0, top_k=10)
    assert set(ids(out)) == {"m1", "m2", "tree_store", "tour_co"}


def test_strict_count_is_taken_before_status_and_hours_filters():
    rows = [place("open", 0.001, 0, "markets_walks", "sights", "market"),
            place("gone", 0.002, 0, "markets_walks", "sights", "market", status="closed_forever"),
            place("paused", 0.003, 0, "markets_walks", "sights", "market", status="temporarily_closed"),
            place("square_shop", 0.004, 0, "markets_walks", "sights", "gift_shop")]
    out = slot_candidates(catalog(rows).rows, {}, 0, "market", S, 5.0, top_k=10)
    assert ids(out) == ["open"]                    # strict (3 incl. closed) wins, then closed are dropped


def test_closed_places_are_never_slot_candidates():
    rows = [place(f"s{i}", 0.001 * i, 0, "culture_sights", "sights", "museum", reviews=1000) for i in range(1, 4)]
    rows += [place("gone", 0.0005, 0, "culture_sights", "sights", "museum", status="closed_forever", reviews=9e4),
             place("paused", 0.0006, 0, "culture_sights", "sights", "museum", status="CLOSED_TEMPORARILY")]
    interest = {"gone": 1.0, "paused": 1.0}
    out = slot_candidates(catalog(rows).rows, interest, 0, "sight", S, 50.0, top_k=10)
    assert not {"gone", "paused"} & set(ids(out))


def test_known_hours_only_and_the_opening_hours_prefilter():
    rows = [place("open", 0.001, 0, "culture_sights", "sights", "museum", hours=OPEN_SAT),
            place("monday", 0.002, 0, "culture_sights", "sights", "museum", hours=MONDAY_ONLY),
            place("unknown", 0.003, 0, "culture_sights", "sights", "museum"),
            place("short", 0.004, 0, "culture_sights", "sights", "museum", hours=SHORT_SAT)]
    cat = catalog(rows)
    out = slot_candidates(cat.rows, {}, 0, "sight", S, 5.0, top_k=10, window=WINDOW)
    assert set(ids(out)) == {"open", "unknown"}    # 30 open minutes < the sight's 45-min base dwell
    assert set(ids(slot_candidates(cat.rows, {}, 0, "sight", S, 5.0, top_k=10, window=WINDOW,
                                   dwell_override=20))) == {"open", "unknown", "short"}
    assert set(ids(slot_candidates(cat.rows, {}, 0, "sight", S, 5.0, top_k=10, window=WINDOW,
                                   known_hours_only=True))) == {"open"}
    assert len(slot_candidates(cat.rows, {}, 0, "sight", S, 5.0, top_k=10)) == 4   # no window -> no prefilter


# --------------------------------------------------------------------------- #
# Ranking: top-K by interest + K near the start, merge order
# --------------------------------------------------------------------------- #
def test_top_by_interest_plus_near_the_start_merged_in_order():
    rows = [place("famous_far", 0.010, 0, "culture_sights", "sights", "museum", reviews=50000),
            place("good_far", 0.012, 0, "culture_sights", "sights", "museum", reviews=8000),
            place("ok_near", 0.0005, 0, "culture_sights", "sights", "museum", reviews=300),
            place("tiny_next_door", 0.0001, 0, "culture_sights", "sights", "museum", reviews=3),
            place("mid_near", 0.001, 0, "culture_sights", "sights", "museum", reviews=500)]
    interest = {"famous_far": 0.9, "good_far": 0.8, "ok_near": 0.3, "tiny_next_door": 0.05, "mid_near": 0.35}
    cat = catalog(rows)
    out = slot_candidates(cat.rows, interest, 2, "sight", S, 5.0, top_k=2)
    # top-2 by interest first, then the near list (cost = walk min - 8 * interest) minus duplicates;
    # "tiny_next_door" has < 20 reviews -> not in the near pool
    d = distances_from(cat.rows, S)
    cost = {p: d[i] * DEFAULT_DETOUR / DEFAULT_WALK_KMH * 60 - NEAR_WEIGHT * interest[p]
            for i, p in zip(cat.rows.index, cat.rows["place_id"]) if p != "tiny_next_door"}
    near = sorted(cost, key=cost.get)[:2]
    assert ids(out)[:2] == ["famous_far", "good_far"]
    assert ids(out)[2:] == [p for p in near if p not in ("famous_far", "good_far")]
    assert "tiny_next_door" not in ids(out)
    assert [c.interest for c in out][:2] == [0.9, 0.8] and all(c.slot == 2 for c in out)


def test_missing_interest_counts_as_zero_and_nan_reviews_are_not_near():
    rows = [place("a", 0.001, 0, "culture_sights", "sights", "museum", reviews=None),
            place("b", 0.002, 0, "culture_sights", "sights", "museum", reviews=25)]
    out = slot_candidates(catalog(rows).rows, {"b": 0.4}, 0, "sight", S, 5.0, top_k=1)
    assert ids(out) == ["b"]                        # top-1 by interest; "a" (no reviews) never near
    out = slot_candidates(catalog(rows).rows, {}, 0, "sight", S, 5.0, top_k=2)
    assert {c.interest for c in out} == {0.0}


def test_candidate_fields_and_dwell():
    rows = [place("statue", 0.001, 0, "culture_sights", "sights", "monument", ai="bronze statue", reviews=50,
                  hours=OPEN_SAT),
            place("rest", 0.001, 0.001, "food_drink", "food_drink", "restaurant", ai="bistro", reviews=900)]
    cat = catalog(rows)
    sight = slot_candidates(cat.rows, {"statue": 0.5}, 0, "sight", S, 5.0, window=WINDOW)[0]
    assert (sight.place_id, sight.name, sight.slot, sight.theme, sight.subtype, sight.interest) == \
           ("statue", "statue", 0, "culture_sights", "monument", 0.5)
    assert sight.open_hours == json.loads(OPEN_SAT) and not sight.dwell_fixed and not sight.extra
    assert sight.dwell_min == estimate_dwell_min("culture_sights", "monument", 50, "bronze statue", "") == 10.0
    food = slot_candidates(cat.rows, {}, 3, "food", S, 5.0, dwell_override=120)[0]
    assert (food.dwell_min, food.dwell_fixed, food.theme) == (120.0, True, "restaurant")
    assert slot_candidates(cat.rows, {}, 0, "shopping", S, 5.0) == []


# --------------------------------------------------------------------------- #
# On-the-way pools
# --------------------------------------------------------------------------- #
def test_extra_activity_rule():
    assert extra_activities(["sight", "coffee", "park", "food"], "max") == ["sight", "park"]
    assert extra_activities(["coffee", "bar"], "chill") == ["sight"]                  # all food -> sights
    assert extra_activities(["sight", "coffee"], "scenic") == ["park", "market"]
    assert extra_activities(["park", "park"], "max") == ["park"]


def test_extra_candidates_flags_slots_and_first_label():
    rows = [place("museum", 0.001, 0, "culture_sights", "sights", "museum", reviews=500),
            place("garden", 0.002, 0, "nature_outdoors", "sights", "garden", reviews=500),
            place("square", 0.003, 0, "markets_walks", "sights", "town_square", reviews=500)]
    cat = catalog(rows)
    ex = extra_candidates(cat.rows, {}, ["coffee", "park"], "max", True, S, 5.0, None)
    assert ids(ex.candidates) == ["garden"] and ex.activities == ["park"]
    assert ex.candidates[0].extra and ex.candidates[0].slot == 1 and ex.activity_of == {"garden": "park"}
    sc = extra_candidates(cat.rows, {}, ["sight"], "scenic", True, S, 5.0, None)
    assert ids(sc.candidates) == ["garden", "square"] and [c.slot for c in sc.candidates] == [-1, -1]
    assert sc.activity_of == {"garden": "park", "square": "market"}
    assert extra_candidates(cat.rows, {}, ["sight"], "max", False, S, 5.0, None).candidates == []
    # a place in two pools (keyword catalog) keeps the first pool's label
    kw = catalog([place("market_park", 0.001, 0, None, "things_to_do", "park", name="Market Park")], drop=["theme"])
    both = extra_candidates(kw.rows, {}, [], "scenic", True, S, 5.0, None)
    assert ids(both.candidates) == ["market_park", "market_park"] and both.activities == ["park", "market"]
    assert both.activity_of == {"market_park": "park"}


# --------------------------------------------------------------------------- #
# Must-visit / add: row candidate + business-status policy
# --------------------------------------------------------------------------- #
def policy_catalog():
    return catalog([place("open", 0.001, 0, "food_drink", "food_drink", "coffee_shop", reviews=300, hours=OPEN_SAT),
                    place("gone", 0.002, 0, "food_drink", "food_drink", "romanian_restaurant", status="closed_forever"),
                    place("paused", 0.003, 0, "food_drink", "food_drink", "pub", status="temporarily_closed")])


def test_place_candidate_status_policy():
    cat = policy_catalog()
    ok = place_candidate(cat, "open")
    assert ok.accepted and not ok.flagged and ok.reason is None and ok.business_status == ""
    c = ok.candidate
    assert (c.slot, c.pinned, c.extra, c.interest, c.theme, c.subtype) == (-1, True, False, 0.0, "food_drink", "coffee_shop")
    assert c.open_hours == json.loads(OPEN_SAT)
    assert c.dwell_min == 60.0          # own theme food_drink (coffee_shop is not a DWELL key), not the 30 of a slot
    gone = place_candidate(cat, "gone")
    assert not gone.accepted and gone.reason == "place_closed_forever" and gone.name == "gone"
    paused = place_candidate(cat, "paused")                               # must-visits allow it, flagged
    assert paused.accepted and paused.flagged and paused.business_status == "temporarily_closed"
    refused = place_candidate(cat, "paused", allow_temporarily_closed=False)   # add without confirmation
    assert not refused.accepted and refused.reason == "place_temporarily_closed"
    unknown = place_candidate(cat, "nope")
    assert not unknown.accepted and unknown.reason == "unknown_place"


def test_row_candidate_uses_the_places_own_theme_or_group():
    rows = pd.DataFrame([place("x", 0, 0, None, "sights", "museum", reviews=5000)])
    r = rows.iloc[0]
    c = row_candidate(r, None)
    assert c.theme == "sights" and c.dwell_min == place_dwell(r, "sights") == estimate_dwell_min("sights", "museum", 5000)
    assert dwell_for("sights") == 40.0 and c.pinned and c.open_hours is None
    assert row_candidate(r, [[0, 10]], pinned=False).pinned is False
