"""Tests of the golden expected API outputs (walk_planner.cli golden machinery).

* the mini synthetic set (tests/fixtures/mini: bundle + scenarios + expected) runs green, and the
  runner flags every kind of difference (strings, numbers beyond 1e-6, missing files) — CI needs no
  real data;
* update / run round trip, the identity fields of a re-dated bundle with the same content;
* compare_json / golden_dumps / scenario_request units;
* the committed real expected set (golden/expected/<bundle_id>/): complete for golden/scenarios.json
  and consistent with baseline_v0 (stop order, times, edit chains; S19 = the bug-2 fix) — JSON only;
* a real-bundle subset run when the Bucharest bundle is present (skipped otherwise)."""

import json
import re
import shutil
from pathlib import Path

import pytest

from walk_planner import cli
from walk_planner.cli import GOLDEN_FORMAT, compare_json, golden_dumps, run_golden, scenario_request

ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "tests" / "fixtures" / "mini"
BUNDLES = MINI / "bundles"
BUNDLE = sorted(p for p in BUNDLES.iterdir() if p.is_dir() and not p.name.startswith("."))[0]
MINI_SCENARIOS = MINI / "scenarios.json"
MINI_EXPECTED = MINI / "expected"
GOLDEN = ROOT / "golden"
REAL_EXPECTED = GOLDEN / "expected"
# The research repo root (services/walk_planner/tests -> parents[3]); None when this package is vendored fewer
# than 3 levels below "/" (e.g. /src/tests): there is then no real data, and those tests skip.
_UP = Path(__file__).resolve().parents
REPO = _UP[3] if len(_UP) > 3 else None
REAL_BUNDLES = (REPO / "recommendation_system" / "ai_location_recommender" / "data" / "walk_bundles"
                if REPO is not None else None)
ROUTING_ENV = ("WALK_ROUTER_URL", "ORS_API_KEY", "WALK_ROUTE_CACHE_REDIS_URL", "WALK_BUNDLE_DIR")


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch):
    for name in ROUTING_ENV:
        monkeypatch.delenv(name, raising=False)


def golden(capsys, *argv, expected=MINI_EXPECTED, bundle=BUNDLES, scenarios=MINI_SCENARIOS):
    code = cli.main(["golden", *argv, "--bundle", str(bundle), "--scenarios", str(scenarios),
                     "--expected-root", str(expected)])
    return code, capsys.readouterr().out


def mini_expected_copy(tmp_path) -> Path:
    dst = tmp_path / "expected"
    shutil.copytree(MINI_EXPECTED, dst)
    return dst


def edit_json(path: Path, fn) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(golden_dumps(data) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# the mini set
# --------------------------------------------------------------------------- #
def test_mini_golden_run_passes(capsys):
    code, out = golden(capsys, "run")
    assert code == 0, out
    assert re.search(r"8 scenarios: 8 pass", out)
    assert re.search(r"M08_invalid_duplicate_slot\s+PASS\s+422 duplicate_activity", out)


def test_mini_expected_set_is_complete_and_indexed():
    scenarios = json.loads(MINI_SCENARIOS.read_text(encoding="utf-8"))
    exp = MINI_EXPECTED / BUNDLE.name
    index = json.loads((exp / "index.json").read_text(encoding="utf-8"))
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    assert index["bundle_id"] == BUNDLE.name and index["content_sha256"] == manifest["content_sha256"]
    assert index["catalog_sha256"] == manifest["catalog"]["catalog_sha256"]
    assert index["interest_fingerprint"] == manifest["interest"]["fingerprint"]
    assert index["versions"]["algorithm"] == "1.0.0" and index["options"] == {"lang": "ru", "geometry": "geojson"}
    assert sorted(index["scenarios"]) == sorted(s["id"] for s in scenarios)
    for sid, entry in index["scenarios"].items():
        text = (exp / entry["file"]).read_text(encoding="utf-8")
        assert cli._sha256_text(text) == entry["sha256"], sid
        assert json.loads(text)["format"] == GOLDEN_FORMAT


@pytest.mark.parametrize("mutate,path_part", [
    (lambda d: d["plan"]["response"]["variants"][0]["stops"][0].__setitem__("name", "Elsewhere"),
     ".plan.response.variants[0].stops[0].name"),
    (lambda d: d["plan"]["response"]["variants"][0]["summary"].__setitem__(
        "walk_min", d["plan"]["response"]["variants"][0]["summary"]["walk_min"] + 1e-3),
     ".plan.response.variants[0].summary.walk_min"),
    (lambda d: d["plan"]["response"]["variants"][0]["stops"].reverse(), ".plan.response.variants[0].stops[0]"),
    (lambda d: d["edits"]["A"][0]["call"]["response"]["variant"]["sequence"].pop(),
     ".edits.A[0].call.response.variant.sequence.<len>"),
    (lambda d: d["plan"]["response"].pop("personalization"), ".plan.response.personalization"),
    (lambda d: d["plan"]["response"]["request"].__setitem__("catalog_version", "minitown-20000101-00000000"),
     ".plan.response.request.catalog_version"),
])
def test_the_runner_flags_differences(capsys, tmp_path, mutate, path_part):
    exp = mini_expected_copy(tmp_path)
    edit_json(exp / BUNDLE.name / "M01_default.json", mutate)
    code, out = golden(capsys, "run", "--only", "M01_default,M03_free_chill", expected=exp)
    assert code == 1
    assert re.search(r"M01_default\s+DIFF", out) and re.search(r"M03_free_chill\s+PASS", out)
    assert f"M01_default{path_part}" in out, out


def test_plan_id_is_exact_only_for_a_bit_identical_request(capsys, tmp_path):
    """plan_id hashes the exact request echo: last-bit noise of an echoed float (numpy builds differ in
    the city centre's mean) changes it, so it is compared only when the echo is bit-identical — but it
    must always be the sha1 of the response's own request + versions."""
    exp = mini_expected_copy(tmp_path)
    f = exp / BUNDLE.name / "M01_default.json"

    def noisy_centre(d):
        r = d["plan"]["response"]
        r["request"]["start"]["lat"] += 1e-13
        r["plan_id"] = "0" * 40
    edit_json(f, noisy_centre)
    code, out = golden(capsys, "run", "--only", "M01_default", expected=exp)
    assert code == 0, out

    def other_id(d):
        d["plan"]["response"]["plan_id"] = "0" * 40
    shutil.copy(MINI_EXPECTED / BUNDLE.name / "M01_default.json", f)
    edit_json(f, other_id)
    code, out = golden(capsys, "run", "--only", "M01_default", expected=exp)
    assert code == 1 and "M01_default.plan.response.plan_id" in out
    from walk_planner.cli import golden_diffs

    good = json.loads((MINI_EXPECTED / BUNDLE.name / "M01_default.json").read_text(encoding="utf-8"))
    bad = json.loads(json.dumps(good))
    bad["plan"]["response"]["plan_id"] = "f" * 40               # not the sha1 of its request: always flagged
    assert golden_diffs(good, bad, identity=False) == [(".plan.response.plan_id",
                                                        "sha1 of the response's request + versions", "f" * 40)]


def test_a_clock_may_flip_only_at_a_half_minute():
    from walk_planner.cli import golden_diffs

    def doc(offset, local, local2="2026-10-03T10:03"):
        stops = [{"departure": {"offset_min": offset, "local": local}},
                 {"arrival": {"offset_min": 3.0, "local": local2}}]
        return {"plan": {"response": {"variants": [{"stops": stops}]}}}
    half = doc(217.49999999999974, "2026-10-03T13:37")
    assert golden_diffs(half, doc(217.50000000000003, "2026-10-03T13:38")) == []        # either rounding is right
    assert golden_diffs(half, doc(217.50000000000003, "2026-10-03T13:39")) != []        # two minutes: a real change
    assert golden_diffs(doc(217.2, "2026-10-03T13:37"), doc(217.2, "2026-10-03T13:38")) != []   # not at a half
    assert golden_diffs(half, doc(217.49999999999974, "2026-10-03T13:37", "2026-10-03T10:04")) != []


def test_numbers_within_tolerance_and_missing_files(capsys, tmp_path):
    exp = mini_expected_copy(tmp_path)

    def nudge(d):
        s = d["plan"]["response"]["variants"][0]["summary"]
        s["walk_min"] += 5e-7
    edit_json(exp / BUNDLE.name / "M01_default.json", nudge)
    code, out = golden(capsys, "run", "--only", "M01_default", expected=exp)
    assert code == 0, out
    (exp / BUNDLE.name / "M02_one_way_scenic.json").unlink()
    code, out = golden(capsys, "run", expected=exp)
    assert code == 1 and re.search(r"M02_one_way_scenic\s+MISSING", out)
    code, out = golden(capsys, "run", expected=tmp_path / "nothing")
    assert code == 1 and "8 missing" in out


def test_update_then_run_round_trip(capsys, tmp_path):
    exp = tmp_path / "exp"
    code, out = golden(capsys, "update", "--only", "M03_free_chill,M08_invalid_duplicate_slot", expected=exp)
    assert code == 0 and "2 new" in out
    index = json.loads((exp / BUNDLE.name / "index.json").read_text(encoding="utf-8"))
    assert sorted(index["scenarios"]) == ["M03_free_chill", "M08_invalid_duplicate_slot"]
    assert index["routing"].startswith("estimate") and index["scenarios_file"] == "scenarios.json"
    for name in ("M03_free_chill.json", "M08_invalid_duplicate_slot.json"):
        assert (exp / BUNDLE.name / name).read_bytes() == (MINI_EXPECTED / BUNDLE.name / name).read_bytes()
    code, out = golden(capsys, "update", "--only", "M03_free_chill", expected=exp)
    assert code == 0 and re.search(r"M03_free_chill\s+SAME", out)
    assert sorted(json.loads((exp / BUNDLE.name / "index.json").read_text())["scenarios"]) == \
        ["M03_free_chill", "M08_invalid_duplicate_slot"]                       # partial update keeps the rest
    code, out = golden(capsys, "run", "--only", "M03_free_chill,M08_invalid_duplicate_slot", expected=exp)
    assert code == 0
    code, out = golden(capsys, "run", "--only", "M99", expected=exp)
    assert code == 2


def test_same_content_under_another_bundle_id(capsys, tmp_path):
    """A rebuilt bundle (new date in the id, same content) is compared with the existing expected set,
    without the identity fields that embed the id."""
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    new_id = "minitown-20990101-" + manifest["content_sha256"][:8]
    dst = tmp_path / new_id
    shutil.copytree(BUNDLE, dst)
    manifest["bundle_id"] = new_id
    (dst / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    code, out = golden(capsys, "run", bundle=dst)
    assert code == 0, out
    assert f"note: expected set {BUNDLE.name} has the same content as {new_id}" in out
    # a different content would not match any expected set
    manifest["content_sha256"] = "0" * 64
    (dst / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    cli._LOADED.clear()
    code, out = golden(capsys, "run", bundle=dst)
    assert code == 1 and "expected (none)" in out


def test_edit_chain_semantics_on_the_mini_set():
    """Chain E (remove stop 0, then drag it back to position 0) re-creates the planned variant, and
    chain D sets every stop's minutes like the dashboard's minutes editor."""
    m01 = json.loads((MINI_EXPECTED / BUNDLE.name / "M01_default.json").read_text(encoding="utf-8"))
    planned = m01["plan"]["response"]["variants"][0]
    e = m01["edits"]["E"]
    assert [s["applied"] for s in e] == [True, True] and e[1]["resolved"]["place_id"] == planned["stops"][0]["place_id"]
    restored = e[1]["call"]["response"]["variant"]
    assert compare_json([(s["place_id"], s["arrival"], s["departure"]) for s in planned["stops"]],
                        [(s["place_id"], s["arrival"], s["departure"]) for s in restored["stops"]]) == []
    d = m01["edits"]["D"][0]["call"]
    assert d["call"] == "POST /v1/walks/schedule" and d["body"]["sequence"][0]["dwell_min"] == 120.0
    assert all(x["dwell_fixed"] for x in d["body"]["sequence"])
    assert all(float(x["dwell_min"]).is_integer() for x in d["body"]["sequence"])
    m04 = json.loads((MINI_EXPECTED / BUNDLE.name / "M04_must_status.json").read_text(encoding="utf-8"))
    codes = {ch: [(s["call"]["status"], s["call"]["response"].get("error", {}).get("code")) for s in steps]
             for ch, steps in m04["edits"].items()}
    assert codes["A"] == [(422, "place_closed_forever")] and codes["B"] == [(409, "place_already_in_route")]
    assert codes["D"] == [(200, None), (404, "unknown_place")]
    assert [m["code"] for m in m04["plan"]["response"]["messages"]] == \
        ["unknown_place_ids", "must_visit_closed_forever"]


# --------------------------------------------------------------------------- #
# units
# --------------------------------------------------------------------------- #
def test_compare_json_rules():
    assert compare_json({"a": [1, 2.0, "x", None, True]}, {"a": [1.0000001, 2, "x", None, True]}) == []
    assert compare_json({"a": 1.0}, {"a": 1.00001}) == [(".a", 1.0, 1.00001)]
    assert compare_json([1, 2], [2, 1]) == [("[0]", 1, 2), ("[1]", 2, 1)]
    assert compare_json({"a": True}, {"a": 1}) == [(".a", True, 1)]                 # booleans are not numbers
    assert compare_json({"a": 1, "b": 2}, {"b": 2, "c": 3}) == [(".a", 1, "<missing>"), (".c", "<missing>", 3)]
    assert compare_json([1], [1, 2]) == [(".<len>", 1, 2)]
    a = {"plan_id": "x", "versions": {"catalog": "a", "api": "v1"}, "request": {"catalog_version": "a", "city": "C"}}
    b = {"plan_id": "y", "versions": {"catalog": "b", "api": "v1"}, "request": {"catalog_version": "b", "city": "D"}}
    identity = lambda p: bool(cli._IDENTITY_PATH.search(p))  # noqa: E731
    assert compare_json(a, b, ignore=identity) == [(".request.city", "C", "D")]
    assert len(compare_json(a, b)) == 4
    assert compare_json(1.0, 1.5, tol=0.6) == []


def test_golden_dumps_is_compact_json():
    obj = {"a": {"b": [[26.1, 44.4], [26.2, 44.5]], "c": "текст"}, "list": [{"x": 1}, {"y": [1, 2]}],
           "long": {"k" * 30: "v" * 100, "z": 1}, "empty": {}, "e2": []}
    text = golden_dumps(obj)
    assert json.loads(text) == obj
    assert '"b": [[26.1, 44.4], [26.2, 44.5]]' in text and "текст" in text
    lines = text.splitlines()
    assert lines[0] == "{" and lines[-1] == "}"
    assert any(line.strip().startswith('"long": {') and line.strip() == '"long": {' for line in lines)
    with pytest.raises(ValueError):
        golden_dumps({"x": float("nan")})


def test_scenario_request_is_the_api_body():
    sc = {"id": "X", "city": "C", "date": "2026-10-03", "slots": [{"activity": "sight", "dwell_min": None}],
          "favourite_place_ids": ["1"], "edits": [{"chain": "A", "ops": []}], "note": "n"}
    assert scenario_request(sc) == {"city": "C", "date": "2026-10-03",
                                    "slots": [{"activity": "sight", "dwell_min": None}], "favourite_place_ids": ["1"]}


# --------------------------------------------------------------------------- #
# the committed real expected set (no bundle needed: JSON only)
# --------------------------------------------------------------------------- #
def _real_sets():
    if not REAL_EXPECTED.is_dir():
        return []
    return sorted(d for d in REAL_EXPECTED.iterdir() if (d / "index.json").is_file())


@pytest.mark.skipif(not _real_sets(), reason="no golden/expected set")
def test_real_expected_set_covers_every_scenario():
    scenarios = json.loads((GOLDEN / "scenarios.json").read_text(encoding="utf-8"))
    ids = [s["id"] for s in scenarios]
    assert {"P01_fav_s1", "P02_fav_s4"} <= set(ids)
    for d in _real_sets():
        index = json.loads((d / "index.json").read_text(encoding="utf-8"))
        assert index["bundle_id"] == d.name and sorted(index["scenarios"]) == sorted(ids)
        for sid in ids:
            g = json.loads((d / f"{sid}.json").read_text(encoding="utf-8"))
            sc = next(s for s in scenarios if s["id"] == sid)
            assert g["scenario"] == sc and g["plan"]["body"] == scenario_request(sc)
            assert g["plan"]["status"] == 200 and g["plan"]["response"]["status"] == "ok"
            assert g["plan"]["response"]["versions"]["catalog"] == d.name
            assert sorted(g["edits"]) == sorted(c["chain"] for c in sc.get("edits") or [])
        for pid in ("P01_fav_s1", "P02_fav_s4"):
            pers = json.loads((d / f"{pid}.json").read_text(encoding="utf-8"))["plan"]["response"]["personalization"]
            assert pers["mode"] == "favourites" and pers["favourites_ignored"] == []
        p02 = json.loads((d / "P02_fav_s4.json").read_text(encoding="utf-8"))["plan"]["response"]["personalization"]
        assert p02["profiles"] == 2                     # the engine's 2 taste profiles (food / museums)


_NUMBER = re.compile(r"(-?\d+\.\d+)")


def _same_links(api: list, base: list) -> bool:
    """Navigation URLs equal, except that a coordinate printed with 6 decimals may differ by one unit (1e-6):
    the baseline came from the CSV through pandas' default float parser, which reads some 17-digit coordinates
    one ulp off (``26.098287499999998`` -> ``26.0982875``); the bundle parses them exactly (round_trip), and a
    coordinate on a half-way point then rounds the other way (golden/README.md, "Bundle rebuild 2026-10-02")."""
    if len(api) != len(base):
        return False
    for a, b in zip(api, base):
        pa, pb = _NUMBER.split(a), _NUMBER.split(b)
        if len(pa) != len(pb):
            return False
        for i, (x, y) in enumerate(zip(pa, pb)):
            if x == y:
                continue
            if i % 2 == 0 or abs(abs(float(x) - float(y)) - 1e-6) > 1e-9:
                return False
    return True


def _same_route(view, plan) -> bool:
    """API stops == baseline digest stops: places, order and kinds exactly; arrival / departure minutes within
    2e-6 (the baseline rounds to 6 decimals)."""
    api = [(s["place_id"], s["kind"], s["arrival"]["offset_min"], s["departure"]["offset_min"]) for s in view["stops"]]
    base = [(s["place_id"], "pinned" if s["pinned"] else ("on_the_way" if s["extra"] else "slot"), s["arrival_min"],
             s["depart_min"]) for s in plan["stops"]]
    return [a[:2] for a in api] == [b[:2] for b in base] and compare_json(
        [list(b[2:]) for b in base], [list(a[2:]) for a in api], tol=2e-6) == []


@pytest.mark.skipif(not _real_sets(), reason="no golden/expected set")
def test_real_expected_set_agrees_with_baseline_v0():
    """Every variant and every edit chain of the v1 API outputs routes the same places at the same times
    as the pre-refactor dashboard (baseline_v0) — except S19, where the bug-2 fix drops the
    closed_forever must-visit and keeps the temporarily_closed one pinned (and flagged)."""
    base_dir = GOLDEN / "baseline_v0"
    for d in _real_sets():
        for f in sorted(d.glob("*.json")):
            if f.name == "index.json" or not (base_dir / f.name).is_file():
                continue
            g = json.loads(f.read_text(encoding="utf-8"))
            base = json.loads((base_dir / f.name).read_text(encoding="utf-8"))
            sid = g["scenario"]["id"]
            variants = g["plan"]["response"]["variants"]
            assert len(variants) == len(base["variants"]), sid
            if sid == "S19_must_closed":
                for v in variants:
                    kinds = {s["place_id"]: (s["kind"], s["business_status"]) for s in v["stops"]}
                    assert "14018219728270213257" not in kinds                         # La Mama: closed_forever
                    assert kinds["10366085341954844986"] == ("pinned", "temporarily_closed")   # Ryan's Pub
                    assert any(m["code"] == "place_temporarily_closed" for m in v["messages"])
                assert any(m["code"] == "must_visit_closed_forever" for m in g["plan"]["response"]["messages"])
                continue
            for i, (v, bv) in enumerate(zip(variants, base["variants"])):
                assert _same_route(v, bv["plan"]), (sid, i)
                if bv["navigation"]:
                    assert _same_links(v["navigation"]["google_parts"], bv["navigation"]["google"]), (sid, i)
            for name, steps in g["edits"].items():
                bch = base["edits"][name]
                assert [s["applied"] for s in steps] == [o["applied"] for o in bch["ops"]], (sid, name)
                done = [s for s in steps if s["call"] is not None and s["call"]["status"] == 200]
                final = done[-1]["call"]["response"]["variant"] if done else variants[0]
                assert _same_route(final, bch["plan"]), (sid, name)


# --------------------------------------------------------------------------- #
# the real bundle (research data; skipped when absent)
# --------------------------------------------------------------------------- #
def _real_bundle():
    from walk_planner.bundle import BundleError, resolve_bundle_dirs

    if REAL_BUNDLES is None or not REAL_BUNDLES.is_dir():
        return None
    try:
        return [d for d in resolve_bundle_dirs(REAL_BUNDLES)
                if json.loads((d / "manifest.json").read_text())["city"] == "Bucharest"][0]
    except (BundleError, IndexError):
        return None


@pytest.mark.skipif(_real_bundle() is None or not _real_sets(), reason="no real Bucharest bundle / expected set")
def test_real_golden_subset_runs_green(capsys):
    from walk_planner.bundle import load_bundle

    bundle = load_bundle(_real_bundle())
    if cli._expected_dir_for(bundle, REAL_EXPECTED)[0] is None:
        pytest.skip("the expected set was built from another bundle content")
    scenarios = json.loads((GOLDEN / "scenarios.json").read_text(encoding="utf-8"))
    code = run_golden([bundle], scenarios, REAL_EXPECTED, only={"S01_default", "S19_must_closed", "P01_fav_s1"},
                      out=lambda line: None)
    assert code == 0


def test_link_comparison_allows_only_half_way_coordinate_flips():
    base = "https://www.google.com/maps/dir/?api=1&travelmode=walking&origin=44.440287,26.098288&destination=x"
    assert _same_links([base], [base])
    assert _same_links([base.replace("26.098288", "26.098287")], [base])
    assert not _same_links([base.replace("26.098288", "26.098286")], [base])            # two units: a real move
    assert not _same_links([base.replace("walking", "driving")], [base])
    assert not _same_links([base], [base, base])


# --------------------------------------------------------------------------- #
# golden/tools/compare_expected_sets.py (the proof tool of a bundle rebuild)
# --------------------------------------------------------------------------- #
def _compare_tool():
    import importlib.util

    spec = importlib.util.spec_from_file_location("wp_compare_sets", GOLDEN / "tools" / "compare_expected_sets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compare_expected_sets_classifies_every_difference(tmp_path):
    tool = _compare_tool()
    old = MINI_EXPECTED / BUNDLE.name
    new = tmp_path / "new"
    shutil.copytree(old, new)
    lines = []
    assert tool.compare_sets(old, new, out=lines.append) == 0 and "SUBSTANTIVE 0" in lines[-2]

    def tweak(d):
        r = d["plan"]["response"]
        r["plan_id"] = "0" * 40                                                  # identity
        r["variants"][0]["summary"]["walk_min"] += 3e-9                           # float noise
        url = r["variants"][0]["navigation"]["google_parts"][0]
        num = re.search(r"origin=(-?\d+\.\d{6})", url).group(1)
        flipped = f"{float(num) + 1e-6:.6f}"
        r["variants"][0]["navigation"]["google_parts"][0] = url.replace(num, flipped, 1)    # half-way flip

    edit_json(new / "M01_default.json", tweak)
    lines = []
    assert tool.compare_sets(old, new, out=lines.append) == 0
    row = next(ln for ln in lines if ln.startswith("M01_default"))
    assert row.split()[2:7] == ["1", "1", "0", "1", "0"]                       # identity float clock coord tie
    edit_json(new / "M03_free_chill.json",
              lambda d: d["plan"]["response"]["variants"][0]["stops"][0].__setitem__("name", "Elsewhere"))
    lines = []
    assert tool.compare_sets(old, new, out=lines.append) == 1
    assert any("SUBSTANTIVE: .plan.response.variants[0].stops[0].name" in ln for ln in lines)
    assert lines[-1] == "RESULT: SUBSTANTIVE differences"
    assert not tool._coordinate_flip("https://x?o=1.000000", "https://x?o=1.000002")
    assert not tool._coordinate_flip("https://x?o=1.000000&m=walk", "https://x?o=1.000001&m=car")
