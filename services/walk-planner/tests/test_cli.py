"""Tests of the developer CLI (walk_planner.cli / python -m walk_planner) on the committed mini bundle:
plan / schedule / insert (JSON, --pretty, errors, stdin), search / place / config, interest, route,
bundle build|validate|info, golden list, serve (exec mocked), bundle resolution errors. Offline:
routing is the straight-line estimate (the routing environment is cleared)."""

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from walk_planner import cli
from walk_planner.bundle import read_manifest

ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "tests" / "fixtures" / "mini"
BUNDLES = MINI / "bundles"
BUNDLE = sorted(p for p in BUNDLES.iterdir() if p.is_dir() and not p.name.startswith("."))[0]
SCENARIOS = {s["id"]: s for s in json.loads((MINI / "scenarios.json").read_text(encoding="utf-8"))}
EXPECTED = MINI / "expected" / BUNDLE.name
SRC = MINI / "source"
ROUTING_ENV = ("WALK_ROUTER_URL", "ORS_API_KEY", "ORS_BASE_URL", "WALK_ROUTE_CACHE_REDIS_URL", "WALK_BUNDLE_DIR",
               "PHOTO_BASE_URL")


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch):
    for name in ROUTING_ENV:
        monkeypatch.delenv(name, raising=False)


def run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def run_json(capsys, *argv):
    code, out, err = run(capsys, *argv)
    return code, json.loads(out), err


def ids():
    m = read_manifest(BUNDLE)
    assert m["city"] == "Minitown"
    import pyarrow.parquet as pq

    t = pq.read_table(BUNDLE / "walk_catalog.parquet", columns=["place_id", "name"]).to_pydict()
    return dict(zip(t["name"], t["place_id"]))


NAMES = ids()


@pytest.fixture(scope="module")
def plan_file(tmp_path_factory):
    """A saved plan response (scenario M01 through the CLI)."""
    import contextlib

    path = tmp_path_factory.mktemp("plan") / "plan.json"
    body = cli.scenario_request(SCENARIOS["M01_default"])
    req = path.parent / "req.json"
    req.write_text(json.dumps(body), encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.main(["plan", "--bundle", str(BUNDLE), "--request", str(req), "--routing", "estimate",
                         "--compact"]) == 0
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def test_plan_from_flags(capsys):
    code, resp, _ = run_json(capsys, "plan", "--bundle", BUNDLE, "--date", "2026-10-03", "--start", "10:00", "--end",
                             "14:00", "--slots", "sight,coffee:20,park,food", "--routing", "estimate", "--compact")
    assert code == 0 and resp["status"] == "ok" and resp["versions"]["catalog"] == BUNDLE.name
    req = resp["request"]
    assert req["city"] == "Minitown" and req["start"]["place_id"] is None and req["shape"] == "loop"
    assert req["slots"][1] == {"activity": "coffee", "dwell_min": 20}
    assert resp["variants"][0]["segments"][0]["geometry"]["type"] == "LineString"


def test_plan_request_file_equals_the_golden_api_response(capsys, plan_file, tmp_path):
    expected = json.loads((EXPECTED / "M01_default.json").read_text(encoding="utf-8"))["plan"]["response"]
    body = tmp_path / "body.json"
    body.write_text(json.dumps(cli.scenario_request(SCENARIOS["M01_default"])), encoding="utf-8")
    code, resp, _ = run_json(capsys, "plan", "--bundle", BUNDLE, "--request", body, "--routing", "estimate")
    assert code == 0 and cli.compare_json(expected, resp) == []


def test_plan_from_stdin_with_overrides(capsys, monkeypatch):
    body = cli.scenario_request(SCENARIOS["M02_one_way_scenic"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(body)))
    code, resp, _ = run_json(capsys, "plan", "--bundle", BUNDLE, "--request", "-", "--variants", "1", "--lang", "en",
                             "--geometry", "polyline6", "--routing", "estimate")
    assert code == 0 and len(resp["variants"]) == 1 and resp["request"]["shape"] == "one_way"
    seg = resp["variants"][0]["segments"][0]
    assert "geometry_polyline6" in seg and "geometry" not in seg
    assert all(m["text"] and not any("а" <= ch <= "я" for ch in m["text"]) for m in resp["variants"][0]["messages"])


def test_plan_pretty_is_the_dashboard_text(capsys):
    code, out, err = run(capsys, "plan", "--bundle", BUNDLE, "--date", "2026-10-03", "--slots",
                         "sight,coffee,park,food", "--pretty", "--routing", "estimate", "--timing", "--links")
    assert code == 0, err
    assert out.startswith("Minitown · 2026-10-03 · 10:00–14:00 · Петля · Максимум мест · старт: центр города")
    assert "Окно: 4 ч 00 мин · суббота" in out and "── Вариант 1 ──" in out and "Остановок: " in out
    assert "Google Maps: https://www.google.com/maps/dir/?api=1&travelmode=walking" in out
    assert "plan: " in err


def test_plan_personalised_and_start_options(capsys):
    fav = [NAMES["Galeria Nord"], NAMES["Palace of Arts"]]
    code, resp, _ = run_json(capsys, "plan", "--bundle", BUNDLE, "--date", "2026-10-03", "--fav", ",".join(fav),
                             "--wtg", "2222222222222222222", "--strength", "0.7", "--start-place", NAMES["Pub 21"],
                             "--shape", "one_way", "--no-fill", "--routing", "estimate")
    assert code == 0
    assert resp["personalization"]["mode"] == "favourites" and resp["personalization"]["strength"] == 0.7
    assert resp["personalization"]["favourites_ignored"] == ["2222222222222222222"]
    assert resp["request"]["start"]["place_id"] == NAMES["Pub 21"]
    code, resp, _ = run_json(capsys, "plan", "--bundle", BUNDLE, "--date", "2026-10-03", "--start-latlon", "44.43,26.1",
                             "--must", NAMES["Paused Pub"], "--debug", "--routing", "estimate")
    assert code == 0 and resp["request"]["start"]["lat"] == 44.43 and "debug" in resp
    assert any(s["business_status"] == "temporarily_closed" for s in resp["variants"][0]["stops"])


def test_plan_error_is_the_api_error_body(capsys):
    code, out, err = run(capsys, "plan", "--bundle", BUNDLE, "--date", "2026-10-03", "--slots", "sight,sight",
                         "--routing", "estimate")
    body = json.loads(out)
    assert code == 1 and body["error"]["code"] == "duplicate_activity" and "HTTP 422 duplicate_activity" in err
    code, out, _ = run(capsys, "plan", "--bundle", BUNDLE, "--date", "2026-10-03", "--lang", "en", "--slots", "zoo",
                       "--routing", "estimate")
    assert code == 1 and json.loads(out)["error"]["code"] == "unknown_activity"


# --------------------------------------------------------------------------- #
# schedule / insert
# --------------------------------------------------------------------------- #
def test_schedule_from_a_saved_plan(capsys, plan_file):
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    seq = plan["variants"][0]["sequence"]
    code, resp, _ = run_json(capsys, "schedule", "--bundle", BUNDLE, "--from-plan", plan_file, "--edit", "move:-1:0",
                             "--edit", "dwell:1:45", "--routing", "estimate")
    assert code == 0 and resp["variant"]["edited"] is True and "inserted_index" not in resp
    got = [s["place_id"] for s in resp["variant"]["stops"]]
    assert got == [seq[-1]["place_id"]] + [e["place_id"] for e in seq[:-1]]
    assert resp["variant"]["stops"][1]["dwell_min"] == 45.0 and resp["variant"]["stops"][1]["dwell_fixed"] is True
    # the edit response chains: schedule it again unchanged -> same stops
    p2 = plan_file.parent / "edit.json"
    p2.write_text(json.dumps(resp), encoding="utf-8")
    code, again, _ = run_json(capsys, "schedule", "--bundle", BUNDLE, "--from-plan", p2, "--routing", "estimate")
    assert code == 0 and cli.compare_json(resp["variant"]["stops"], again["variant"]["stops"]) == []


def test_schedule_body_file_and_edit_errors(capsys, plan_file, tmp_path):
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    body = {"request": plan["request"], "sequence": plan["variants"][0]["sequence"][1:], "variant_index": 0}
    (tmp_path / "b.json").write_text(json.dumps(body), encoding="utf-8")
    code, out, _ = run(capsys, "schedule", "--bundle", BUNDLE, "--request", tmp_path / "b.json", "--pretty",
                       "--routing", "estimate")
    assert code == 0 and "── Вариант 1 (изменён) ──" in out
    code, _, err = run(capsys, "schedule", "--bundle", BUNDLE, "--from-plan", plan_file, "--edit", "move:9:0")
    assert code == 2 and "out of range" in err
    code, _, err = run(capsys, "schedule", "--bundle", BUNDLE, "--from-plan", plan_file, "--edit", "swap:1:2")
    assert code == 2 and "expected move" in err
    code, _, err = run(capsys, "schedule", "--bundle", BUNDLE)
    assert code == 2 and "--from-plan" in err
    bad = dict(body, sequence=body["sequence"] + body["sequence"][:1])
    (tmp_path / "dup.json").write_text(json.dumps(bad), encoding="utf-8")
    code, out, _ = run(capsys, "schedule", "--bundle", BUNDLE, "--request", tmp_path / "dup.json", "--routing",
                       "estimate")
    assert code == 1 and json.loads(out)["error"]["code"] == "validation_error"


def test_insert_place_and_status_policy(capsys, plan_file):
    code, resp, _ = run_json(capsys, "insert", "--bundle", BUNDLE, "--from-plan", plan_file, "--place",
                             NAMES["Souvenir House"], "--routing", "estimate")
    assert code == 0 and isinstance(resp["inserted_index"], int)
    assert resp["variant"]["stops"][resp["inserted_index"]]["place_id"] == NAMES["Souvenir House"]
    code, out, _ = run(capsys, "insert", "--bundle", BUNDLE, "--from-plan", plan_file, "--place", NAMES["Paused Pub"],
                       "--routing", "estimate")
    assert code == 1 and json.loads(out)["error"]["code"] == "place_temporarily_closed"
    code, resp, _ = run_json(capsys, "insert", "--bundle", BUNDLE, "--from-plan", plan_file, "--place",
                             NAMES["Paused Pub"], "--allow-temporarily-closed", "--dwell", "30",
                             "--routing", "estimate")
    assert code == 0
    stop = resp["variant"]["stops"][resp["inserted_index"]]
    assert stop["dwell_min"] == 30.0 and stop["business_status"] == "temporarily_closed"
    code, out, _ = run(capsys, "insert", "--bundle", BUNDLE, "--from-plan", plan_file, "--place", NAMES["Craft Corner"],
                       "--pretty", "--routing", "estimate")
    assert code == 1 and json.loads(out)["error"]["code"] == "place_closed_forever"
    code, _, err = run(capsys, "insert", "--bundle", BUNDLE, "--from-plan", plan_file)
    assert code == 2 and "--place" in err


# --------------------------------------------------------------------------- #
# catalog lookups, interest, route
# --------------------------------------------------------------------------- #
def test_search_place_config(capsys):
    code, resp, _ = run_json(capsys, "search", "museum", "--bundle", BUNDLE, "--near", "44.43,26.10", "--limit", "3")
    assert code == 0 and len(resp["results"]) == 3 and all("distance_m" in r for r in resp["results"])
    code, out, _ = run(capsys, "search", "craft corner", "--bundle", BUNDLE, "--pretty")
    assert code == 0 and "ничего не найдено" in out                       # closed_forever is hidden by default
    code, out, _ = run(capsys, "search", "craft corner", "--bundle", BUNDLE, "--pretty", "--include-closed")
    assert code == 0 and "Craft Corner" in out and "closed_forever" in out
    code, resp, _ = run_json(capsys, "place", NAMES["City History Museum"], "--bundle", BUNDLE,
                             "--photo-base-url", "https://cdn.example/p")
    assert code == 0 and resp["name"] == "City History Museum" and resp["timezone"] == "Europe/Bucharest"
    assert resp["photos"][0]["url"].startswith("https://cdn.example/p/photos_cid/")
    code, resp, _ = run_json(capsys, "place", "42", "--bundle", BUNDLE)
    assert code == 1 and resp["error"]["code"] == "unknown_place"
    code, resp, _ = run_json(capsys, "config", "--bundle", BUNDLE)
    assert code == 0 and resp["city"] == "Minitown"
    assert [a["code"] for a in resp["activities"]][:2] == ["sight", "coffee"]


def test_interest_inspection(capsys):
    fav = NAMES["Galeria Nord"]
    code, resp, _ = run_json(capsys, "interest", "--bundle", BUNDLE, "--fav", fav, "--top", "5", "--theme",
                             "culture_sights", "--json")
    assert code == 0 and resp["mode"] == "favourites" and resp["used"] == [fav] and resp["profiles"] == 1
    assert len(resp["top"]) == 5 and all(t["theme"] == "culture_sights" for t in resp["top"])
    assert sorted(t["interest"] for t in resp["top"]) == sorted((t["interest"] for t in resp["top"]))
    code, out, _ = run(capsys, "interest", "--bundle", BUNDLE, "--fav", fav, "--top", "3")
    assert code == 0 and "mode favourites" in out and "seeds: Galeria Nord" in out
    code, out, _ = run(capsys, "interest", "--bundle", BUNDLE, "--top", "3")
    assert code == 0 and "mode popularity" in out
    code, _, err = run(capsys, "interest", "--bundle", BUNDLE, "--theme", "volcanoes")
    assert code == 2 and "no places" in err


def test_route_probe_with_the_estimate(capsys):
    code, resp, _ = run_json(capsys, "route", "--coords", "44.43,26.10;44.44,26.11;44.435,26.12", "--routing",
                             "estimate", "--json")
    assert code == 0 and resp["quality"] == "estimate" and len(resp["legs"]) == 2
    assert resp["legs"][0]["provider"] == "haversine" and resp["legs"][0]["points"] == 2
    code, out, _ = run(capsys, "route", "--coords", "44.43,26.10;44.44,26.11")   # nothing configured -> estimate
    assert code == 0 and "quality estimate" in out and "chain: estimate" in out
    code, _, err = run(capsys, "route", "--coords", "44.43,26.10")
    assert code == 2 and "two points" in err


# --------------------------------------------------------------------------- #
# bundle / golden / serve / setup errors
# --------------------------------------------------------------------------- #
def test_bundle_info_and_validate(capsys, tmp_path):
    code, out, _ = run(capsys, "bundle", "info", BUNDLE)
    assert code == 0 and BUNDLE.name in out and "Minitown" in out
    code, resp, _ = run_json(capsys, "bundle", "info", "--bundle", BUNDLES, "--json")
    assert code == 0 and resp["bundle_id"] == BUNDLE.name
    code, out, _ = run(capsys, "bundle", "validate", BUNDLE)
    assert code == 0 and out.startswith(f"OK: {BUNDLE.name}")
    bad = tmp_path / BUNDLE.name
    shutil.copytree(BUNDLE, bad)
    with open(bad / "walk_catalog.parquet", "ab") as fh:
        fh.write(b"x")
    code, resp, _ = run_json(capsys, "bundle", "validate", bad, "--shallow", "--json")
    assert code == 1 and resp["ok"] is False
    code, _, err = run(capsys, "plan", "--bundle", bad, "--date", "2026-10-03")
    assert code == 2 and "failed verification" in err


def test_bundle_build_through_the_cli(capsys, tmp_path):
    argv = ["bundle", "build", "--out-root", tmp_path, "--city-slug", "minitown", "--timezone", "Europe/Bucharest",
            "--catalog-csv", SRC / "locations_minitown.csv",
            "--photo-manifest-csv", SRC / "photo_manifest_minitown.csv",
            "--text-npy", SRC / "text_minitown.npy", "--text-meta-csv", SRC / "text_minitown_metadata.csv"]
    code, out, _ = run(capsys, *argv)
    assert code == 0 and "built in" in out and "OK: minitown-" in out
    built = [p for p in tmp_path.iterdir()]
    assert len(built) == 1 and read_manifest(built[0])["interest"]["image_dim"] == 0
    assert read_manifest(built[0])["builder"]["cmd"].startswith("walk-planner bundle build --out-root")
    code, _, err = run(capsys, *argv)
    assert code == 2 and "already exists" in err
    code, _, err = run(capsys, "bundle", "build", "--out-root", tmp_path)
    assert code == 2 and "--catalog-csv" in err


def test_bundle_build_research_layout(capsys, tmp_path):
    data = tmp_path / "data"
    (data / "embedding_store").mkdir(parents=True)
    shutil.copy(SRC / "locations_minitown.csv", data / "locations_minitown_all.csv")
    shutil.copy(SRC / "text_minitown.npy", data / "embedding_store" / "location_embeddings_minitown_all.npy")
    shutil.copy(SRC / "text_minitown_metadata.csv",
                data / "embedding_store" / "location_embeddings_minitown_all_metadata.csv")
    code, out, _ = run(capsys, "bundle", "build", "--data-dir", data, "--city", "Minitown", "--timezone",
                       "Europe/Bucharest", "--no-validate")
    assert code == 0, out
    (bundle,) = list((data / "walk_bundles").iterdir())
    m = read_manifest(bundle)
    assert m["source"]["text_npy"]["file"] == "location_embeddings_minitown_all.npy" and "image_npy" not in m["source"]


def test_golden_list(capsys):
    code, out, _ = run(capsys, "golden", "list", "--scenarios", MINI / "scenarios.json", "--expected-root",
                       MINI / "expected")
    assert code == 0 and "M01_default" in out and BUNDLE.name in out


def test_serve_execs_uvicorn(capsys, monkeypatch):
    import importlib.util

    calls = {}
    real = importlib.util.find_spec
    fake = ("uvicorn", "walk_planner.service.app")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: object() if name in fake else real(name, *a))

    def fake_exec(path, argv, env):
        calls.update(path=path, argv=argv, env=env)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvpe", fake_exec)
    with pytest.raises(SystemExit):
        cli.main(["serve", "--bundle", str(BUNDLES), "--port", "9001", "--workers", "2"])
    assert calls["path"] == sys.executable
    assert calls["argv"][1:] == ["-m", "uvicorn", "walk_planner.service.app:create_app", "--factory", "--host",
                                 "127.0.0.1", "--port", "9001", "--workers", "2"]
    assert calls["env"]["WALK_BUNDLE_DIR"] == str(BUNDLES)
    assert calls["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(ROOT)


def test_serve_without_the_service_module_is_a_setup_error(capsys, monkeypatch):
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "walk_planner.service.app" else
                        (object() if name == "uvicorn" else real(name, *a)))
    code, _, err = run(capsys, "serve")
    assert code == 2 and "walk_planner.service.app is not available" in err


def test_no_bundle_and_ambiguous_city(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_research_bundles", lambda: tmp_path / "none")
    code, _, err = run(capsys, "config")
    assert code == 2 and "WALK_BUNDLE_DIR" in err
    monkeypatch.setenv("WALK_BUNDLE_DIR", str(BUNDLES))
    code, resp, _ = run_json(capsys, "config")
    assert code == 0 and resp["city"] == "Minitown"
    code, _, err = run(capsys, "config", "--city", "Berlin")
    assert code == 2 and "no bundle for city 'Berlin'" in err


def test_python_dash_m_entry_point():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-m", "walk_planner", "--version"], cwd=ROOT, capture_output=True, text=True,
                         timeout=120, env=env)
    assert out.returncode == 0 and out.stdout.strip() == "walk-planner 1.0.0"
    out = subprocess.run([sys.executable, "-m", "walk_planner", "nope"], cwd=ROOT, capture_output=True, text=True,
                         timeout=120, env=env)
    assert out.returncode == 2 and "invalid choice" in out.stderr


def test_the_research_data_default_is_computed_lazily_and_safely(monkeypatch):
    """No path arithmetic at import: a copy vendored fewer than 3 levels below "/" (e.g. /src/walk_planner)
    imports and runs; it just has no research-data default bundle."""
    monkeypatch.delenv("WALK_BUNDLE_DIR", raising=False)
    monkeypatch.setattr(cli, "__file__", "/walk_planner/cli.py")
    assert cli._research_bundles() is None and cli.default_bundle_spec() is None
    monkeypatch.setattr(cli, "__file__", "/repo/services/walk_planner/walk_planner/cli.py")       # the research layout
    assert cli._research_bundles() == Path("/repo/recommendation_system/ai_location_recommender/data/walk_bundles")
    monkeypatch.setenv("WALK_BUNDLE_DIR", " /x ")
    assert cli.default_bundle_spec() == "/x"


def test_golden_run_url_rejects_local_bundle_options(capsys):
    code, _, err = run(capsys, "golden", "run", "--url", "http://127.0.0.1:9", "--bundle", BUNDLES)
    assert code == 2 and "--url replays against a running service" in err
    code, _, err = run(capsys, "golden", "run", "--url", "127.0.0.1:9")
    assert code == 2 and "expected http(s)://host:port" in err
    code, _, err = run(capsys, "golden", "run", "--url", "http://127.0.0.1:9", "--wait", "0", "--scenarios",
                       MINI / "scenarios.json", "--expected-root", MINI / "expected")
    assert code == 2 and "cannot connect" in err
