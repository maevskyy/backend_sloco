"""Unit tests of the golden parity checker's rules (golden/tools/parity_check.py) on hand-made digests:
the bug-2 guard of S19 (only the notes, the must-visit candidates and the variants' plans / renders /
navigation may differ — and only as the fix explains), bug 1 and the environment rule. Offline; the
real-catalog run is in test_pipeline.py (golden subset) and in the tool itself."""

import copy
import importlib.util
from pathlib import Path

import pytest

from walk_planner.messages import Message

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
S19 = {"id": "S19_must_closed", "shape": "loop", "must_visit_place_ids": ["111", "222"]}
CLOSED = {"111": "La Mama"}


@pytest.fixture(scope="module")
def pc():
    spec = importlib.util.spec_from_file_location("wp_parity_check_rules", GOLDEN / "tools" / "parity_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stop(pid, pinned=False):
    return {"order": 0, "place_id": pid, "name": pid, "pinned": pinned, "extra": not pinned, "arrival_min": 1.0}


def _digest(must_ids, stops, notes=(), slot=(("s1", 0, 0.5),), search_km=2.5, dropped=()):
    return {
        "scenario": {"id": "S19_must_closed"},
        "inputs_resolved": {"must_ids": ["111", "222"], "center_exact": ["44.43", "26.1"]},
        "error": None,
        "notes": [dict(n) for n in notes],
        "search_km": search_km,
        "area": [44.43, 26.1],
        "request": {"n_must_visit": len(must_ids), "n_candidates": 9},
        "candidates": {"slot": [list(r) for r in slot], "extra": [["e1", -1, 0.4, 10.0, "Парк / природа"]],
                       "must": [[pid, -1, 0.0, 60.0, False, "food_drink", "pub", True] for pid in must_ids]},
        "variants": [{"dropped": list(dropped), "plan": {"stops": [dict(s) for s in stops], "total_time_min": 100.0},
                      "render": {"warnings": []}, "navigation": {"google": ["u"], "legs": []}}],
        "edits": {},
    }


def _note():
    return {"kind": "warning", "text": Message("must_visit_closed_forever", {"place_ids": ["111"],
                                                                            "names": ["La Mama"]}).text("ru")}


BASE = _digest(["111", "222"], [_stop("111", True), _stop("222", True), _stop("x")])
GOOD = _digest(["222"], [_stop("222", True), _stop("y"), _stop("x")], notes=[_note()])


def test_bug2_differences_that_the_fix_explains_are_allowed(pc):
    rep = pc.check_scenario(S19, BASE, GOOD, closed_forever=CLOSED)
    assert rep["status"] == "allowed" and rep["diffs"] == []
    areas = {pc._guard_area(a[1]) for a in rep["allowed"]}
    assert areas == {"notes", "candidates.must", "request.n_must_visit", "variants[*].plan"}
    # the fallback without the catalog (dropped must-visits from the digests, one warning note added)
    assert pc.check_scenario(S19, BASE, GOOD)["status"] == "allowed"


@pytest.mark.parametrize("mutate,where", [
    (lambda d: d["candidates"]["slot"].append(["s2", 0, 0.4]), "candidates.slot"),          # slot pool
    (lambda d: d["candidates"]["extra"][0].__setitem__(2, 0.9), "candidates.extra"),          # extra pool
    (lambda d: d.__setitem__("search_km", 2.0), ".search_km"),
    (lambda d: d["request"].__setitem__("n_candidates", 8), ".request.n_candidates"),
    (lambda d: d["inputs_resolved"].__setitem__("must_ids", ["222"]), ".inputs_resolved.must_ids"),
    (lambda d: d["variants"][0].__setitem__("dropped", ["Кофе"]), ".variants[0].dropped"),
    (lambda d: d["variants"].append(copy.deepcopy(d["variants"][0])), ".variants.<len>"),
    (lambda d: d["notes"].append({"kind": "info", "text": "something else"}), "<bug2>.notes.others"),
    (lambda d: d["notes"].clear(), "<bug2>.notes.closed_forever_note"),
    (lambda d: d["notes"].__setitem__(0, {"kind": "warning", "text": "Закрыто навсегда: Other"}),
     "<bug2>.notes.closed_forever_note"),
    (lambda d: d["candidates"]["must"].clear(), "<bug2>.candidates.must"),                    # kept one lost
    (lambda d: d["request"].__setitem__("n_must_visit", 2), "<bug2>.request.n_must_visit"),
    (lambda d: d["variants"][0]["plan"]["stops"].append(_stop("111", True)), "<bug2>.variants[0].plan.closed"),
    (lambda d: d["variants"][0]["plan"]["stops"][0].__setitem__("pinned", False),
     "<bug2>.variants[0].plan.must_visit_not_pinned"),
])
def test_bug2_guard_flags_everything_else(pc, mutate, where):
    bad = copy.deepcopy(GOOD)
    mutate(bad)
    rep = pc.check_scenario(S19, BASE, bad, closed_forever=CLOSED)
    assert rep["status"] == "diff", where
    assert any(d[0].startswith(where) for d in rep["diffs"]), (where, rep["diffs"])


def test_a_bug2_scenario_without_a_closed_place_explains_nothing(pc):
    rep = pc.check_scenario(S19, BASE, GOOD, closed_forever={})
    assert rep["status"] == "diff"
    assert any(d[0] == "<bug2>.closed_forever_must_visits" for d in rep["diffs"])


def test_other_scenarios_get_no_bug2_allowance(pc):
    other = {"id": "S18_must_evening_bar", "shape": "loop"}
    rep = pc.check_scenario(other, BASE, GOOD)
    assert rep["status"] == "diff" and not any(a[0] == "bug2" for a in rep["allowed"])


def test_bug1_and_env_rules(pc):
    seg = {"from_order": 0, "to_order": 1}
    base = {"inputs_resolved": {"center_exact": ["44.4301", "26.1"]}, "candidates": {"slot": [], "extra": []},
            "variants": [{"plan": {"segments": [dict(seg)]}}], "edits": {}}
    new = copy.deepcopy(base)
    new["variants"][0]["plan"]["segments"][0] = {"from_order": 1, "to_order": 2}
    new["inputs_resolved"]["center_exact"][0] = "44.43010000000001"
    rep = pc.check_scenario({"id": "S05_free", "shape": "free"}, base, new)
    assert rep["status"] == "allowed" and {a[0] for a in rep["allowed"]} == {"bug1", "env"}
    rep = pc.check_scenario({"id": "S04_one_way", "shape": "one_way"}, base, new)        # not "free": a diff
    assert rep["status"] == "diff"


def test_scenarios_without_a_baseline_are_skipped(pc):
    scenarios = [{"id": "S01_default"}, {"id": "P01_fav_s1", "baseline_v0": False}, {"id": "S02_chill"},
                 {"id": "P02_fav_s4", "baseline_v0": False}, {"id": "S03_scenic", "baseline_v0": True}]
    run, skipped = pc.select_scenarios(scenarios, set())
    assert [s["id"] for s in run] == ["S01_default", "S02_chill", "S03_scenic"]
    assert skipped == ["P01_fav_s1", "P02_fav_s4"]
    run, skipped = pc.select_scenarios(scenarios, {"P01_fav_s1", "S02_chill"})
    assert [s["id"] for s in run] == ["S02_chill"] and skipped == ["P01_fav_s1"]


def test_the_committed_scenarios_mark_exactly_the_v1_only_ones(pc):
    import json

    scenarios = json.loads((GOLDEN / "scenarios.json").read_text(encoding="utf-8"))
    run, skipped = pc.select_scenarios(scenarios, set())
    assert skipped == ["P01_fav_s1", "P02_fav_s4"]
    assert all((GOLDEN / "baseline_v0" / f"{s['id']}.json").is_file() for s in run)       # nothing else lacks one
