"""Tests of the walk-planner HTTP service (walk_planner.service: app, schemas, settings, logging).

Offline: the mini fixture bundle (tests/fixtures/mini), routing stubbed through ``provider_factory`` (the
straight-line estimate, or a fake street router), sockets refused. What is covered:

* every endpoint, its happy path and its error codes (422 format / domain, 404, 405, 409, 413, 500, 503 not_ready
  and busy -- the per-worker load guard);
* golden equality: the service's responses == golden/expected for every recorded call of the mini set and,
  when the real Bucharest bundle is present, of the real set (skipped otherwise) -- the service makes the
  package facade's calls (cli.api_plan ...), so the HTTP API reproduces the golden outputs. Golden JSON is
  always compared with the golden comparator (``cli.compare_json`` / ``golden_diffs``: numbers within 1e-6):
  the committed outputs were made on macOS, and glibc's libm differs in the last bit (CI runs on Linux);
* every golden expected response (and request) validates against the pydantic models of the OpenAPI;
* the stateless editing round trip plan -> schedule -> insert through the request echo + sequence;
* readiness, start-up from WALK_BUNDLE_DIR, start-up failures (incl. a uvicorn multi-worker run that must exit
  instead of re-spawning workers; uvicorn's own lines as JSON with deploy/uvicorn-log-config.json), the per-request
  routing provider, the access log (fields, timings, no favourite ids, coarse coordinates, router steps only
  when something failed), /v1/meta without secrets, concurrency, the OpenAPI export being current.
"""
import concurrent.futures
import copy
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from walk_planner import cli, pipeline  # noqa: E402
from walk_planner.bundle import BundleError, LoadedBundle, load_bundle, resolve_bundle_dirs  # noqa: E402
from walk_planner.catalog import CityCatalog  # noqa: E402
from walk_planner.core import RoutingProvider, haversine_km  # noqa: E402
from walk_planner.messages import ERRORS, MESSAGES  # noqa: E402
from walk_planner.routing import ChainProvider, RoutingError, polyline6_decode, reset_routing_state  # noqa: E402
from walk_planner.service import schemas as S  # noqa: E402
from walk_planner.service.app import StartupError, _event_log, _uvicorn_supervisor_pid, create_app  # noqa: E402
from walk_planner.service.logging import (  # noqa: E402
    ACCESS_LOGGER,
    PIPELINE_STAGES,
    JsonFormatter,
    coarse_point,
    collect_stage_times,
    ids_digest,
    instrument_pipeline,
    uninstrument_pipeline,
)
from walk_planner.service.settings import Settings  # noqa: E402
from walk_planner.slots import ACTIVITY_CODES, SHAPES, STYLES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "tests" / "fixtures" / "mini"
MINI_BUNDLES = MINI / "bundles"
MINI_BUNDLE = sorted(p for p in MINI_BUNDLES.iterdir() if p.is_dir() and not p.name.startswith("."))[0]
MINI_EXPECTED = MINI / "expected" / MINI_BUNDLE.name
# The research repo root (services/walk_planner/tests -> parents[3]); None when this package is vendored fewer
# than 3 levels below "/" (e.g. /src/tests): there is then no real data, and those tests skip.
_UP = Path(__file__).resolve().parents
REPO = _UP[3] if len(_UP) > 3 else None
REAL_BUNDLES = (REPO / "recommendation_system" / "ai_location_recommender" / "data" / "walk_bundles"
                if REPO is not None else None)
REAL_EXPECTED_ROOT = ROOT / "golden" / "expected"
MINI_FILES = sorted(p for p in MINI_EXPECTED.glob("*.json") if p.name != "index.json")
REAL_FILES = sorted(p for p in REAL_EXPECTED_ROOT.glob("*/*.json") if p.name != "index.json")
ENV = ("WALK_BUNDLE_DIR", "PHOTO_BASE_URL", "WALK_DEFAULT_LANG", "WALK_GEOMETRY_DEFAULT", "LOG_LEVEL", "ENVIRONMENT",
       "WALK_GIT_SHA", "WALK_VERIFY_BUNDLE", "WALK_STARTUP_SMOKE_PLAN", "WALK_MAX_BODY_BYTES",
       "WALK_MAX_CONCURRENT_PLANS", "WALK_ROUTER_URL",
       "WALK_ROUTER_PROFILE", "ORS_API_KEY", "ORS_BASE_URL", "ORS_MAX_PER_MIN", "ORS_MAX_PER_DAY",
       "WALK_ROUTE_CACHE_REDIS_URL", "WALK_ROUTING_DEADLINE_S", "WALK_ROUTER_PROBE_S", "WEB_CONCURRENCY")
# mini catalog places (tests/fixtures/mini/scenarios.json M04): closed forever / temporarily closed / unknown
CLOSED_FOREVER = "9100000000006179011"
TEMP_CLOSED = "18000000000000395950"
UNKNOWN = "3333333333333333333"


def estimate(_start=None):
    return RoutingProvider()


def settings(**overrides) -> Settings:
    """Settings for a test: every field explicit, so the developer's environment cannot leak in."""
    values = dict(walk_bundle_dir=None, photo_base_url=None, walk_default_lang="ru", walk_geometry_default="geojson",
                  log_level="INFO", environment="test", walk_git_sha="0123abc", walk_verify_bundle=True,
                  walk_startup_smoke_plan=True, walk_max_body_bytes=1_048_576, walk_max_concurrent_plans=2)
    values.update(overrides)
    return Settings(**values)


@contextmanager
def serve(bundles, factory=estimate, **overrides):
    app = create_app(settings(**overrides), bundles=bundles, provider_factory=factory, setup_logging=False)
    with TestClient(app) as client:
        yield client


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def mini_case(name: str) -> dict:
    return load(MINI_EXPECTED / f"{name}.json")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No routing / bundle environment, and no network at all."""
    for name in ENV:
        monkeypatch.delenv(name, raising=False)

    def refuse(*_a, **_k):
        raise AssertionError("network access in a service test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(scope="module")
def mini() -> LoadedBundle:
    return load_bundle(MINI_BUNDLE)


@pytest.fixture(scope="module")
def client(mini):
    with serve([mini]) as c:
        yield c


@pytest.fixture(scope="module")
def m01(client) -> dict:
    """The M01 plan (loop from the centre, 4 slots) as the service answers it."""
    case = mini_case("M01_default")
    r = client.post("/v1/walks/plan", json=case["plan"]["body"])
    assert r.status_code == 200, r.text
    return r.json()


def access_records(caplog, endpoint=None) -> list:
    out = [r for r in caplog.records if r.name == ACCESS_LOGGER]
    return [r for r in out if endpoint is None or r.fields.get("endpoint") == endpoint]


# --------------------------------------------------------------------------- #
# golden equality (the service == the package facade == golden/expected)
# --------------------------------------------------------------------------- #
def replay(client, record: dict) -> tuple:
    method, path = record["call"].split(" ", 1)
    r = client.request(method, path, params=record["options"], json=record["body"])
    return r.status_code, r.json()


def plan_differences(expected_response: dict, actual_response: dict) -> list:
    """A plan response vs a golden one, by the golden rules (numbers within 1e-6, ``plan_id`` checked against
    its own request echo and compared only when the echo is bit-identical: the city centre is a pandas mean,
    whose last bit differs between macOS and glibc builds)."""
    return cli.golden_diffs({"plan": {"response": expected_response}}, {"plan": {"response": actual_response}})


def golden_differences(client, expected: dict) -> list:
    """Replay every recorded call of a golden file through HTTP and compare like `golden run` does."""
    actual = copy.deepcopy(expected)
    actual["plan"]["status"], actual["plan"]["response"] = replay(client, expected["plan"])
    for chain, steps in expected["edits"].items():
        for i, step in enumerate(steps):
            if step["call"] is not None:
                status, body = replay(client, step["call"])
                actual["edits"][chain][i]["call"]["status"] = status
                actual["edits"][chain][i]["call"]["response"] = body
    return cli.golden_diffs(expected, actual)


@pytest.mark.parametrize("path", MINI_FILES, ids=lambda p: p.stem)
def test_mini_golden_responses_through_http(client, path):
    expected = load(path)
    assert golden_differences(client, expected) == []


@pytest.fixture(scope="module")
def real_client():
    if REAL_BUNDLES is None or not REAL_BUNDLES.is_dir():
        pytest.skip(f"real bundles not present ({REAL_BUNDLES})")
    dirs = resolve_bundle_dirs(REAL_BUNDLES)
    bundle = load_bundle(dirs[0])
    if not (REAL_EXPECTED_ROOT / bundle.bundle_id).is_dir():
        pytest.skip(f"no golden expected set for {bundle.bundle_id}")
    with serve([bundle], walk_startup_smoke_plan=False) as c:
        c.bundle_id = bundle.bundle_id
        yield c


@pytest.mark.parametrize("path", REAL_FILES, ids=lambda p: f"{p.parent.name[:9]}/{p.stem}")
def test_real_golden_responses_through_http(real_client, path):
    if path.parent.name != real_client.bundle_id:
        pytest.skip(f"expected set of another bundle ({path.parent.name})")
    assert golden_differences(real_client, load(path)) == []


def _calls(data: dict) -> list:
    return [data["plan"]] + [s["call"] for steps in data["edits"].values() for s in steps if s["call"]]


@pytest.mark.parametrize("path", MINI_FILES + REAL_FILES, ids=lambda p: f"{p.parent.name[:9]}/{p.stem}")
def test_golden_requests_and_responses_validate_against_the_models(path):
    models = {"/v1/walks/plan": (S.PlanRequest, S.PlanResponse),
              "/v1/walks/schedule": (S.ScheduleRequest, S.EditResponse),
              "/v1/walks/insert": (S.InsertRequest, S.InsertResponse)}
    for call in _calls(load(path)):
        request_model, response_model = models[call["call"].split(" ", 1)[1]]
        request_model.model_validate(call["body"])
        if call["status"] == 200:
            response = response_model.model_validate(call["response"])
            messages = list(getattr(response, "messages", []))
            variants = response.variants if hasattr(response, "variants") else [response.variant]
            for v in variants:
                messages += v.messages
            assert {m.code for m in messages} <= set(MESSAGES)
        else:
            body = S.ErrorBody.model_validate(call["response"])
            assert body.error.code in ERRORS and ERRORS[body.error.code].http_status == call["status"]


def test_the_schema_examples_validate():
    S.PlanResponse.model_validate(S.PLAN_RESPONSE_EXAMPLE)
    for ex in S.PLAN_REQUEST_EXAMPLES.values():
        S.PlanRequest.model_validate(ex["value"])
    for ex in S.SCHEDULE_REQUEST_EXAMPLES.values():
        S.ScheduleRequest.model_validate(ex["value"])
    for ex in S.INSERT_REQUEST_EXAMPLES.values():
        S.InsertRequest.model_validate(ex["value"])
    for name, ex in S.ERROR_EXAMPLES.items():
        assert S.ErrorBody.model_validate(ex["value"]).error.code == name


def test_the_schema_enums_are_the_package_codes():
    from typing import get_args
    assert get_args(S.ActivityCode) == ACTIVITY_CODES
    assert set(get_args(S.StyleCode)) == set(STYLES) and set(get_args(S.ShapeCode)) == set(SHAPES)
    assert get_args(S.StopKind) == pipeline.STOP_KINDS


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def test_plan_matches_the_package_facade(client, mini):
    body = mini_case("M02_one_way_scenic")["plan"]["body"]
    r = client.post("/v1/walks/plan", json=body)
    call = cli.api_plan(body, mini.catalog, mini.taste, provider_factory=estimate)
    assert r.status_code == call.status == 200
    assert r.json() == call.response
    assert r.headers["content-type"] == "application/json"


def test_plan_response_options(client, m01):
    body = mini_case("M01_default")["plan"]["body"]
    en = client.post("/v1/walks/plan?lang=en", json=body).json()
    assert en["messages"][0]["text"].startswith("Search radius narrowed")
    assert en["variants"] and en["plan_id"] == m01["plan_id"]          # options do not change the plan
    assert client.post("/v1/walks/plan", json={**body, "lang": "en"}).json() == en
    poly = client.post("/v1/walks/plan", json=body, params={"geometry": "polyline6"}).json()
    for a, b in zip(poly["variants"][0]["segments"], m01["variants"][0]["segments"]):
        assert "geometry" not in a and "geometry" in b
        decoded = polyline6_decode(a["geometry_polyline6"])
        assert all(abs(x - y) < 1e-6 for p, q in zip(decoded, b["geometry"]["coordinates"]) for x, y in zip(p, q))
    S.PlanResponse.model_validate(poly)
    dbg = client.post("/v1/walks/plan", json=body, params={"debug": "true"}).json()
    assert dbg["debug"]["candidates"] and "debug" not in m01
    S.PlanResponse.model_validate(dbg)
    clash = client.post("/v1/walks/plan?lang=ru", json={**body, "lang": "en"})
    assert clash.status_code == 422 and clash.json()["error"]["params"]["field"] == "lang"


def test_plan_with_favourites_is_personalised(client):
    case = mini_case("M05_favourites")
    r = client.post("/v1/walks/plan", json=case["plan"]["body"]).json()
    assert plan_differences(case["plan"]["response"], r) == []
    assert r["personalization"]["mode"] == "favourites" and r["personalization"]["profiles"] == 1


def test_plan_that_finds_nothing_is_200_with_a_reason(client):
    body = {"date": "2026-10-03", "start": "city_center", "start_time": "03:00", "end_time": "03:30",
            "slots": ["sight"], "known_hours_only": True, "radius_km": 0.3}
    r = client.post("/v1/walks/plan", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("no_candidates", "no_route") and data["variants"] == []
    assert any(m["severity"] == "error" for m in data["messages"])
    S.PlanResponse.model_validate(data)


@pytest.mark.parametrize("patch, code", [
    ({"end_time": "10:05"}, "invalid_window"),
    ({"slots": ["museum"]}, "unknown_activity"),
    ({"slots": ["sight", "sight"]}, "duplicate_activity"),
    ({"start": None}, "start_required"),
    ({"slots": [], "must_visit_place_ids": []}, "no_slots_or_must_visits"),
    ({"must_visit_place_ids": [str(10 ** 18 + i) for i in range(11)]}, "too_many_must_visits"),
    ({"city": "Atlantis"}, "unknown_city"),
    ({"start_time": "24:00"}, "validation_error"),
    ({"start": {"place_id": UNKNOWN}}, "unknown_place"),
    ({"variants": "3"}, "validation_error"),                 # JSON types are the pipeline's: no coercion
    ({"fill_window": "yes"}, "validation_error"),
    ({"radius_km": 0.1}, "validation_error"),
])
def test_plan_domain_errors_keep_the_pipeline_codes(client, patch, code):
    body = {**mini_case("M01_default")["plan"]["body"], **patch}
    r = client.post("/v1/walks/plan", json=body)
    assert r.json()["error"]["code"] == code, r.text
    assert r.status_code == ERRORS[code].http_status
    S.ErrorBody.model_validate(r.json())


@pytest.mark.parametrize("body, loc", [
    ({"date": "2026-10-03", "must_visit_place_ids": [9100000000006179011]}, ["body", "must_visit_place_ids", 0]),
    ({"date": "2026-10-03", "favourite_place_ids": ["12a"]}, ["body", "favourite_place_ids", 0]),
    ({"date": "3 Oct"}, ["body", "date"]),
    ({"date": "2026-10-03", "variants": 9}, ["body", "variants"]),
    ({"date": "2026-10-03", "shape": "Петля"}, ["body", "shape"]),
    ({"start_time": "10:00"}, ["body", "date"]),
])
def test_plan_format_errors_are_validation_errors_with_details(client, body, loc):
    r = client.post("/v1/walks/plan", json=body)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["message"].startswith("Некорректный запрос")
    assert err["params"]["errors"][0]["loc"] == loc
    S.ErrorBody.model_validate(r.json())
    for problem in err["params"]["errors"]:
        S.ValidationProblem.model_validate(problem)


@pytest.mark.parametrize("content, ctype", [(b"{not json", "application/json"), (b"[1, 2]", "application/json"),
                                            (b'{"date": "2026-10-03"}', "text/plain"), (b"", "application/json")])
def test_unreadable_bodies_are_422(client, content, ctype):
    r = client.post("/v1/walks/plan?lang=en", content=content, headers={"content-type": ctype})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["message"].startswith("Invalid request")


# --------------------------------------------------------------------------- #
# editing round trip
# --------------------------------------------------------------------------- #
def _post(client, path, body, status=200):
    r = client.post(path, json=body)
    assert r.status_code == status, r.text
    return r.json()


def test_plan_schedule_insert_round_trip(client, m01):
    request, v0 = m01["request"], m01["variants"][0]
    seq = v0["sequence"]
    # the unchanged sequence re-times to the same route
    same = _post(client, "/v1/walks/schedule", {"request": request, "sequence": seq})
    S.EditResponse.model_validate(same)
    assert same["variant"]["edited"] is True and same["request"] == request
    assert [s["place_id"] for s in same["variant"]["stops"]] == [s["place_id"] for s in v0["stops"]]
    assert same["variant"]["summary"]["total_min"] == pytest.approx(v0["summary"]["total_min"])
    # a moved stop: the exact order is kept
    moved = seq[1:] + seq[:1]
    out = _post(client, "/v1/walks/schedule", {"request": request, "sequence": moved, "variant_index": 2})
    assert [s["place_id"] for s in out["variant"]["stops"]] == [e["place_id"] for e in moved]
    assert out["variant"]["index"] == 2
    # insert a searched place, then schedule the returned sequence: the same route
    found = client.get("/v1/walks/places/search", params={"q": "museum", "limit": 10}).json()["results"]
    pid = next(p["place_id"] for p in found if p["place_id"] not in {e["place_id"] for e in seq})
    ins = _post(client, "/v1/walks/insert", {"request": request, "sequence": seq, "place_id": pid, "dwell_min": 25})
    S.InsertResponse.model_validate(ins)
    k = ins["inserted_index"]
    stop = ins["variant"]["stops"][k]
    assert stop["place_id"] == pid and stop["kind"] == "pinned" and stop["dwell_min"] == 25 and stop["dwell_fixed"]
    assert pid in ins["places"] and len(ins["variant"]["stops"]) == len(seq) + 1
    again = _post(client, "/v1/walks/schedule", {"request": ins["request"], "sequence": ins["variant"]["sequence"]})
    assert again["variant"]["stops"] == ins["variant"]["stops"]
    # removing every stop is allowed: an empty route, no segments
    empty = _post(client, "/v1/walks/schedule", {"request": request, "sequence": []})
    assert empty["variant"]["stops"] == [] and empty["variant"]["segments"] == []
    assert [m["code"] for m in empty["variant"]["messages"]] == ["route_empty"]


def test_edit_errors(client, m01):
    request, seq = m01["request"], m01["variants"][0]["sequence"]
    base = {"request": request, "sequence": seq}

    def err(path, body, status):
        r = client.post(path, json=body)
        assert r.status_code == status, r.text
        S.ErrorBody.model_validate(r.json())
        return r.json()["error"]

    assert err("/v1/walks/insert", {**base, "place_id": seq[0]["place_id"]}, 409)["code"] == "place_already_in_route"
    assert err("/v1/walks/insert", {**base, "place_id": UNKNOWN}, 404)["code"] == "unknown_place"
    assert err("/v1/walks/insert", {**base, "place_id": CLOSED_FOREVER}, 422)["code"] == "place_closed_forever"
    assert err("/v1/walks/insert", {**base, "place_id": TEMP_CLOSED}, 422)["code"] == "place_temporarily_closed"
    ok = _post(client, "/v1/walks/insert", {**base, "place_id": TEMP_CLOSED, "allow_temporarily_closed": True})
    assert any(m["code"] == "place_temporarily_closed" for m in ok["variant"]["messages"])
    # a place gone from the catalog: 409 when the plan was built on another catalog version, else 404
    gone = [*seq, {"place_id": UNKNOWN, "kind": "pinned", "dwell_min": 30}]
    stale = {**request, "catalog_version": "minitown-20200101-deadbeef"}
    assert err("/v1/walks/schedule", {"request": stale, "sequence": gone}, 409)["code"] == "catalog_changed"
    assert err("/v1/walks/schedule", {"request": request, "sequence": gone}, 404)["code"] == "unknown_place"
    assert err("/v1/walks/schedule", {"request": request, "sequence": [seq[0], seq[0]]}, 422)["code"] == \
        "validation_error"
    closed = [{"place_id": CLOSED_FOREVER, "kind": "pinned", "dwell_min": 30}]
    assert err("/v1/walks/schedule", {"request": request, "sequence": closed}, 422)["code"] == "place_closed_forever"
    # JSON types the API refuses up front
    for bad in ({**base, "variant_index": 1.0}, {**base, "variant_index": "1"},
                {"request": request, "sequence": [{**seq[0], "place_id": int(seq[0]["place_id"])}]},
                {"sequence": seq}, {"request": "x", "sequence": seq}, {**base, "sequence": [{"kind": "slot"}]}):
        assert err("/v1/walks/schedule", bad, 422)["code"] == "validation_error"
    assert err("/v1/walks/insert", {**base, "place_id": TEMP_CLOSED, "allow_temporarily_closed": "yes"},
               422)["code"] == "validation_error"
    assert err("/v1/walks/schedule", {"request": {**request, "city": "Atlantis"}, "sequence": seq},
               422)["code"] == "unknown_city"


# --------------------------------------------------------------------------- #
# catalog endpoints
# --------------------------------------------------------------------------- #
def test_config(client):
    ru = client.get("/v1/walks/config").json()
    S.ConfigResponse.model_validate(ru)
    assert [a["code"] for a in ru["activities"]] == list(ACTIVITY_CODES) and ru["cities"] == ["Minitown"]
    assert ru["activities"][0]["label"] == ru["activities"][0]["label_ru"] and ru["lang"] == "ru"
    en = client.get("/v1/walks/config", params={"city": "minitown", "lang": "en"}).json()
    assert en["styles"][0]["label"] == en["styles"][0]["label_en"] and en["lang"] == "en"
    assert en["limits"]["max_edit_stops"] == pipeline.LIMITS["max_edit_stops"]
    assert client.get("/v1/walks/config", params={"city": "Atlantis"}).json()["error"]["code"] == "unknown_city"


def test_search(client):
    r = client.get("/v1/walks/places/search", params={"q": "museum"})
    data = r.json()
    S.SearchResponse.model_validate(data)
    assert r.status_code == 200 and data["count"] == len(data["results"]) > 0 and data["city"] == "Minitown"
    near = client.get("/v1/walks/places/search", params={"q": "museum", "lat": 44.43, "lon": 26.09, "limit": 2}).json()
    assert len(near["results"]) == 2 and all("distance_m" in x for x in near["results"])
    paused = client.get("/v1/walks/places/search", params={"q": "paused"}).json()["results"]
    assert [p["business_status"] for p in paused] == ["temporarily_closed"]
    shut = client.get("/v1/walks/places/search", params={"q": "craft corner"}).json()
    assert shut["count"] == 0
    assert client.get("/v1/walks/places/search", params={"q": "craft corner", "include_closed": "true"}).json()["count"]
    for params in ({"q": "museum", "lat": 44.43}, {"q": "museum", "limit": 0}, {"q": "museum", "limit": 51},
                   {}, {"q": ""}, {"q": "museum", "lat": "nan", "lon": 26.1}, {"q": "museum", "lat": 91, "lon": 0}):
        bad = client.get("/v1/walks/places/search", params=params)
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error", params


def test_place_detail(client, mini):
    pid = "9100000000000314187"
    r = client.get(f"/v1/walks/places/{pid}")
    assert r.status_code == 200
    detail = S.PlaceDetail.model_validate(r.json())
    assert detail.place_id == pid and len(detail.opening_hours_week) == 7
    assert r.json() == mini.catalog.place_detail(pid)
    assert client.get(f"/v1/walks/places/{pid}", params={"city": "Minitown"}).status_code == 200
    missing = client.get(f"/v1/walks/places/{UNKNOWN}?lang=en")
    assert missing.status_code == 404 and missing.json()["error"]["message"] == f"Place {UNKNOWN} is not in the catalog."
    assert client.get("/v1/walks/places/12ab").status_code == 422
    assert client.get("/v1/walks/places/" + "1" * 21).status_code == 422


def test_default_options_come_from_the_settings(mini):
    with serve([mini], walk_default_lang="en", walk_geometry_default="polyline6") as c:
        plan = c.post("/v1/walks/plan", json=mini_case("M01_default")["plan"]["body"]).json()
        assert plan["messages"][0]["text"].startswith("Search radius narrowed")
        assert "geometry_polyline6" in plan["variants"][0]["segments"][0]
        ru = c.post("/v1/walks/plan?lang=ru&geometry=geojson", json=mini_case("M01_default")["plan"]["body"]).json()
        assert plan_differences(mini_case("M01_default")["plan"]["response"], ru) == []


def test_photo_urls_follow_photo_base_url(mini, m01):
    with serve([mini], photo_base_url="https://cdn.example.org/walk-media/") as c:
        plan = c.post("/v1/walks/plan", json=mini_case("M01_default")["plan"]["body"]).json()
        card = next(iter(plan["places"].values()))
        assert card["photos"][0]["url"] == "https://cdn.example.org/walk-media/" + card["photos"][0]["key"]
        hit = c.get("/v1/walks/places/search", params={"q": "museum"}).json()["results"][0]
        assert hit["photo"]["url"].startswith("https://cdn.example.org/walk-media/photos_cid/")
    assert all(p["url"] is None for card in m01["places"].values() for p in card["photos"])


# --------------------------------------------------------------------------- #
# health / meta / service-level errors / headers
# --------------------------------------------------------------------------- #
def test_health_and_meta(client):
    assert client.get("/v1/health/live").json() == {"status": "alive"}
    ready = client.get("/v1/health/ready")
    assert ready.status_code == 200 and ready.json() == {"status": "ready", "bundles": [MINI_BUNDLE.name]}
    meta = client.get("/v1/meta").json()
    S.MetaResponse.model_validate(meta)
    assert meta["ready"] and meta["state"] == "ready" and meta["git_sha"] == "0123abc" and meta["environment"] == "test"
    assert meta["version"] == meta["versions"]["algorithm"] == "1.0.0"
    bundle = meta["bundles"][0]
    assert bundle["bundle_id"] == MINI_BUNDLE.name and bundle["places"] == 59 and bundle["rows"] == 60
    assert bundle["interest"]["loaded"] is True
    assert meta["routing"]["chain"] == ["estimate"] and meta["runtime"]["python"]
    assert meta["load"]["max_concurrent"] == 2 and meta["load"]["active"] == 0
    assert meta["settings"]["max_concurrent_plans"] == 2


def test_meta_never_shows_secrets(mini, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "sk-test-very-secret-123")
    monkeypatch.setenv("WALK_ROUTER_URL", "http://walker:pw-secret-456@osrm.invalid:5000")
    monkeypatch.setenv("WALK_ROUTE_CACHE_REDIS_URL", "redis://:redis-secret-789@redis.invalid:6379/5")
    monkeypatch.setenv("ORS_BASE_URL", "https://ors.invalid/ors?api_key=qs-secret-000#frag-secret-333")
    monkeypatch.setenv("WALK_ROUTER_PROBE_S", "0")
    reset_routing_state()
    try:
        with serve([mini], photo_base_url="https://cdn:photo-secret-111@cdn.invalid/media?sig=sig-secret-222") as c:
            text = c.get("/v1/meta").text
        meta = json.loads(text)
        S.MetaResponse.model_validate(meta)
        assert meta["routing"]["chain"] == ["osrm", "ors", "estimate"]
        for secret in ("sk-test-very-secret-123", "pw-secret-456", "redis-secret-789", "qs-secret-000",
                       "frag-secret-333", "photo-secret-111", "sig-secret-222", "api_key", "?"):
            assert secret not in text, secret
        assert [r["url"] for r in meta["routing"]["routers"]] == ["http://osrm.invalid:5000", "https://ors.invalid/ors"]
        assert meta["settings"]["photo_base_url"] == "https://cdn.invalid/media"
    finally:
        reset_routing_state()


def test_not_ready_before_startup(mini):
    app = create_app(settings(), bundles=[mini], provider_factory=estimate, setup_logging=False)
    c = TestClient(app)                              # no `with`: the lifespan (start-up) has not run
    assert c.get("/v1/health/live").status_code == 200
    r = c.get("/v1/health/ready")
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    assert r.json()["error"] == {"code": "not_ready", "message": "Сервис ещё загружается — повторите через минуту.",
                                 "params": {"state": "starting"}}
    plan = c.post("/v1/walks/plan?lang=en", json={"date": "2026-10-03"})
    assert plan.status_code == 503 and plan.json()["error"]["message"].startswith("The service is still starting")
    meta = c.get("/v1/meta").json()
    assert meta["ready"] is False and meta["state"] == "starting" and meta["bundles"] == []


def test_service_level_errors(client):
    r = client.get("/v1/walks/plan")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed" and "allow" in r.headers
    r = client.get("/v1/nothing-here?lang=en")
    assert r.status_code == 404 and r.json()["error"] == {"code": "not_found", "message": "No such API path.",
                                                          "params": {"path": "/v1/nothing-here"}}


def test_request_id_and_process_time_headers(client):
    r = client.get("/v1/health/live", headers={"X-Request-Id": "gw-42.abc"})
    assert r.headers["x-request-id"] == "gw-42.abc" and float(r.headers["x-process-time-ms"]) >= 0
    generated = client.get("/v1/health/live", headers={"X-Request-Id": "not a valid id!"}).headers["x-request-id"]
    assert len(generated) == 32 and int(generated, 16) >= 0
    err = client.post("/v1/walks/plan", json={})
    assert err.status_code == 422 and len(err.headers["x-request-id"]) == 32


def test_large_bodies_get_413(mini):
    with serve([mini], walk_max_body_bytes=4096) as c:
        body = {"date": "2026-10-03", "start": "city_center", "favourite_place_ids": [str(10 ** 19 + i) for i in range(400)]}
        r = c.post("/v1/walks/plan", json=body)
        assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
        assert r.json()["error"]["params"] == {"max_bytes": 4096}
        small = json.dumps(mini_case("M01_default")["plan"]["body"]).encode()
        chunked = c.post("/v1/walks/plan", content=iter([small[:50], small[50:]]),
                         headers={"content-type": "application/json"})
        assert chunked.status_code == 200, chunked.text
        big = json.dumps(body).encode()
        chunked = c.post("/v1/walks/plan", content=iter([big[i:i + 1000] for i in range(0, len(big), 1000)]),
                         headers={"content-type": "application/json"})
        assert chunked.status_code == 413


def test_the_load_guard_answers_busy_at_once_and_recovers(mini, m01, caplog):
    """WALK_MAX_CONCURRENT_PLANS=1: while one plan runs (held inside its routing provider), another plan and an
    edit get 503 busy + Retry-After: 2 immediately; search is not limited; afterwards plans run again."""
    entered, release = threading.Event(), threading.Event()

    def slow(_start):
        entered.set()
        assert release.wait(20), "the test never released the first plan"
        return RoutingProvider()

    body = mini_case("M01_default")["plan"]["body"]
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)
    with serve([mini], factory=slow, walk_max_concurrent_plans=1) as c:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(c.post, "/v1/walks/plan", json=body)
            try:
                assert entered.wait(20)
                t0 = time.perf_counter()
                busy = c.post("/v1/walks/plan?lang=en", json=body)
                busy_ms = (time.perf_counter() - t0) * 1000.0
                edit = c.post("/v1/walks/schedule", json={"request": m01["request"],
                                                          "sequence": m01["variants"][0]["sequence"]})
                search = c.get("/v1/walks/places/search", params={"q": "museum"})
                load_during = c.get("/v1/meta").json()["load"]
            finally:
                release.set()
            assert first.result(timeout=60).status_code == 200
        entered.clear()
        release.set()
        again = c.post("/v1/walks/plan", json=body)
        load_after = c.get("/v1/meta").json()["load"]
    assert busy.status_code == 503 and busy.headers["retry-after"] == "2" and busy_ms < 2000
    assert busy.json() == {"error": {"code": "busy", "message": "The service is busy with other routes — please retry "
                                                                "in a couple of seconds.",
                                     "params": {"max_concurrent": 1, "retry_after_s": 2}}}
    S.ErrorBody.model_validate(busy.json())
    assert edit.status_code == 503 and edit.json()["error"]["code"] == "busy"            # edits share the guard
    assert search.status_code == 200                                                    # cheap: never limited
    assert load_during == {"max_concurrent": 1, "active": 1, "peak": 1, "admitted": 1, "rejected_busy": 2}
    assert again.status_code == 200
    assert load_after == {"max_concurrent": 1, "active": 0, "peak": 1, "admitted": 2, "rejected_busy": 2}
    refused = [r.fields for r in access_records(caplog) if r.fields.get("error") == "busy"]
    assert len(refused) == 2 and {r["status"] for r in refused} == {503}


def test_unexpected_errors_are_500_without_internals(mini, caplog):
    def broken(_start):
        raise RuntimeError("boom: internal detail")

    caplog.set_level(logging.INFO, logger="walk_planner.service")
    with serve([mini], factory=broken) as c:
        r = c.post("/v1/walks/plan", json=mini_case("M01_default")["plan"]["body"], headers={"X-Request-Id": "req-500"})
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Внутренняя ошибка сервиса — повторите позже.",
                                  "params": {"request_id": "req-500"}}}
    assert "boom" not in r.text and "Traceback" not in r.text
    crash = [rec for rec in caplog.records if getattr(rec, "fields", {}).get("event") == "unhandled_exception"]
    assert crash and crash[0].exc_info and "boom: internal detail" in JsonFormatter().format(crash[0])
    line = access_records(caplog)[-1].fields
    assert line["status"] == 500 and line["error"] == "internal_error" and line["request_id"] == "req-500"


# --------------------------------------------------------------------------- #
# routing provider per request
# --------------------------------------------------------------------------- #
class FakeStreetRouter:
    """A street router stand-in: legs 1.2x the straight line at 5 km/h, a midpoint in the geometry."""

    name = "osrm"
    data_version = "fake-osm-20261001"
    dataset = "fake-osm-20261001"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0
        if fail:                                     # a router that never answered: dataset unknown
            self.data_version = self.dataset = None

    def legs(self, coords, budget_s=None):
        self.calls += 1
        if self.fail:
            raise RoutingError("timeout", "fake timeout", provider="osrm")
        out = []
        for a, b in zip(coords, coords[1:]):
            km = haversine_km(a[0], a[1], b[0], b[1]) * 1.2
            mid = [(a[1] + b[1]) / 2, (a[0] + b[0]) / 2]
            out.append({"geometry": [[a[1], a[0]], mid, [b[1], b[0]]], "duration_min": km / 5.0 * 60.0,
                        "distance_km": km, "quality": "streets", "provider": "osrm"})
        return out


def test_street_routing_flows_into_the_response_and_never_changes_the_plan(mini, m01, caplog):
    router = FakeStreetRouter()
    starts = []

    def factory(start):
        starts.append(start)
        return ChainProvider([router], cache=None, deadline_s=6.0, start=start)

    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)
    with serve([mini], factory=factory) as c:
        plan = c.post("/v1/walks/plan", json=mini_case("M01_default")["plan"]["body"]).json()
        edit = c.post("/v1/walks/schedule", json={"request": plan["request"],
                                                  "sequence": plan["variants"][0]["sequence"]}).json()
    assert plan["versions"]["routing"] == edit["versions"]["routing"] == "fake-osm-20261001"
    for v in plan["variants"] + [edit["variant"]]:
        assert v["summary"]["routing"] == "streets"
        assert {s["quality"] for s in v["segments"]} == {"streets"} and {s["provider"] for s in v["segments"]} == {"osrm"}
        assert "routing_estimate" not in [m["code"] for m in v["messages"]]
    assert [[s["place_id"] for s in v["stops"]] for v in plan["variants"]] == \
        [[s["place_id"] for s in v["stops"]] for v in m01["variants"]]
    center = (m01["request"]["start"]["lat"], m01["request"]["start"]["lon"])
    # one provider per request; the router is called once per assembled variant (duplicates are dropped
    # after routing) plus once for the edit
    assert starts == [center, center] and router.calls == plan["request"]["variants"] + 1
    line = access_records(caplog, "plan")[-1].fields
    assert line["routing"]["chain"] == ["osrm"] and line["routing"]["legs"]["estimate"] == 0
    assert line["routing"]["data_version"] == "fake-osm-20261001" and line["routing_quality"] == ["streets"]
    assert "event_log" not in line["routing"]                     # routine: no router step list


def test_a_failing_router_falls_back_to_the_estimate_and_says_so(mini, m01, caplog):
    factory = lambda start: ChainProvider([FakeStreetRouter(fail=True)], cache=None, start=start)  # noqa: E731
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)
    with serve([mini], factory=factory) as c:
        plan = c.post("/v1/walks/plan", json=mini_case("M01_default")["plan"]["body"]).json()
    assert plan["variants"] == m01["variants"] and plan["versions"]["routing"] is None
    line = access_records(caplog, "plan")[-1].fields
    assert line["routing"]["failed"] == ["osrm"] and line["routing"]["event_log"][0]["outcome"] == "timeout"


def test_the_access_line_lists_router_steps_only_when_something_failed():
    ok = [{"provider": "cache", "outcome": "partial", "legs": 8, "hits": 5}, {"provider": "osrm", "outcome": "ok",
                                                                            "ms": 3.2, "legs": 8},
          {"provider": "cache", "outcome": "hit", "legs": 8}, {"provider": "osrm", "outcome": "skipped"}]
    assert _event_log(ok) is None and _event_log([]) is None and _event_log(None) is None
    failed = ok + [{"provider": "osrm", "outcome": "timeout", "ms": 2001.0, "detail": "x" * 500},
                   {"provider": "haversine", "outcome": "estimate", "legs": 3}]
    steps = _event_log(failed * 3)                                 # 18 steps
    assert len(steps) == 11 and steps[-1] == {"truncated": 8}
    assert steps[4]["outcome"] == "timeout" and len(steps[4]["detail"]) == 201 and steps[4]["detail"].endswith("…")
    assert _event_log([{"provider": "haversine", "outcome": "estimate", "legs": 2}]) is not None
    assert _event_log([{"provider": "osrm", "outcome": "deadline"}]) is not None


def test_default_provider_factory_reads_the_environment(mini):
    app = create_app(settings(), bundles=[mini], setup_logging=False)     # the default factory: make_provider
    with TestClient(app) as c:                     # nothing configured -> the plain estimate, no network
        plan = c.post("/v1/walks/plan", json=mini_case("M01_default")["plan"]["body"]).json()
    assert plan_differences(mini_case("M01_default")["plan"]["response"], plan) == []


# --------------------------------------------------------------------------- #
# logging / telemetry
# --------------------------------------------------------------------------- #
def test_access_line_fields_timings_and_privacy(client, caplog):
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)
    body = mini_case("M05_favourites")["plan"]["body"]
    point = {**body, "start": {"lat": 44.4251234, "lon": 26.0912345}}
    r = client.post("/v1/walks/plan", json=point, headers={"X-Request-Id": "log-test-1"})
    assert r.status_code == 200
    rec = access_records(caplog, "plan")[-1]
    line = json.loads(JsonFormatter().format(rec))
    assert line["request_id"] == "log-test-1" and line["status"] == 200 and line["method"] == "POST"
    assert line["path"] == "/v1/walks/plan" and line["duration_ms"] > 0 and line["bytes_out"] == len(r.content)
    assert line["city"] == "Minitown" and line["bundle_id"] == MINI_BUNDLE.name and line["plan_status"] == "ok"
    t = line["timings"]
    assert set(t) == {"interest_ms", "candidates_ms", "solver_ms", "router_ms", "render_ms", "plan_ms"}
    assert t["interest_ms"] > 0 and t["candidates_ms"] > 0 and t["solver_ms"] > 0 and t["router_ms"] == 0
    assert t["plan_ms"] >= t["interest_ms"] + t["candidates_ms"] + t["solver_ms"] - 0.5
    assert line["stops"] == [len(v["stops"]) for v in r.json()["variants"]]
    assert line["routing_quality"] == ["estimate"] * len(r.json()["variants"])
    assert line["start"] == [44.43, 26.09] and line["start_kind"] == "point"
    pers = line["personalization"]
    assert pers["favourites"] == 2 and pers["want_to_go"] == 2 and pers["mode"] == "favourites"
    assert len(pers["ids_digest"]) == 12
    text = json.dumps(line)
    for pid in body["favourite_place_ids"] + body["want_to_go_place_ids"]:
        assert pid not in text
    assert "44.4251234" not in text and "26.0912345" not in text


def test_edit_and_error_access_lines(client, m01, caplog):
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)
    client.post("/v1/walks/schedule", json={"request": m01["request"], "sequence": m01["variants"][0]["sequence"]})
    edit = access_records(caplog, "schedule")[-1].fields
    assert edit["status"] == 200 and set(edit["timings"]) == {"interest_ms", "candidates_ms", "solver_ms",
                                                              "router_ms", "render_ms", "edit_ms"}
    assert edit["stops_in"] == len(m01["variants"][0]["sequence"]) and edit["stops"] == [edit["stops_in"]]
    client.post("/v1/walks/plan", json={"date": "2026-10-03", "slots": ["museum"], "start": "city_center"})
    bad = access_records(caplog)[-1].fields
    assert bad["status"] == 422 and bad["error"] == "unknown_activity"
    client.get("/v1/walks/places/search", params={"q": "museum", "lat": 44.4321, "lon": 26.0987})
    search = access_records(caplog, "search")[-1].fields
    assert search["near"] is True and search["q_len"] == 6 and "44.4321" not in json.dumps(search)


def test_json_formatter_and_privacy_helpers():
    rec = logging.LogRecord("x", logging.WARNING, __file__, 1, "hello %s", ("world",), None)
    rec.fields = {"event": "e", "value": float("nan"), "n": 3}
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "hello world" and out["level"] == "WARNING" and out["value"] == "nan" and out["n"] == 3
    assert out["ts"].endswith("Z")
    assert coarse_point((44.4251234, 26.0912345)) == [44.43, 26.09] and coarse_point("city_center") is None
    assert ids_digest(["2", "1", "2"]) == ids_digest(["1", "2"]) != ids_digest(["1"]) and ids_digest([]) is None


def test_stage_instrumentation_is_transparent_and_reference_counted():
    original = {name: getattr(pipeline, name) for name in PIPELINE_STAGES}
    base_wrapped = hasattr(pipeline.resolve_interest, "__walk_stage__")
    instrument_pipeline()
    try:
        assert all(hasattr(getattr(pipeline, name), "__walk_stage__") for name in PIPELINE_STAGES)
        with collect_stage_times() as times:
            pipeline.distances_from.__wrapped__                       # the original stays reachable
            times.add("x", 0.001)
        assert times.ms("x") == 1.0
    finally:
        uninstrument_pipeline()
    assert hasattr(pipeline.resolve_interest, "__walk_stage__") == base_wrapped
    if not base_wrapped:
        assert all(getattr(pipeline, name) is fn for name, fn in original.items())


# --------------------------------------------------------------------------- #
# start-up
# --------------------------------------------------------------------------- #
def test_startup_from_walk_bundle_dir(monkeypatch):
    monkeypatch.setenv("WALK_BUNDLE_DIR", str(MINI_BUNDLES))         # a root of bundles -> newest per city
    monkeypatch.setenv("WALK_DEFAULT_LANG", "EN")
    monkeypatch.setenv("PHOTO_BASE_URL", "")
    monkeypatch.setenv("WALK_GIT_SHA", "abc1234")
    app = create_app(setup_logging=False)
    with TestClient(app) as c:
        meta = c.get("/v1/meta").json()
        plan = c.post("/v1/walks/plan", json=mini_case("M01_default")["plan"]["body"]).json()
    assert meta["bundles"][0]["bundle_id"] == MINI_BUNDLE.name and meta["git_sha"] == "abc1234"
    assert meta["settings"]["default_lang"] == "en" and meta["settings"]["photo_base_url"] is None
    assert meta["startup_ms"] > 0
    assert plan["messages"][0]["text"].startswith("Search radius narrowed")       # WALK_DEFAULT_LANG=en


@pytest.mark.parametrize("make, error", [
    (lambda tmp: settings(), StartupError),                                          # WALK_BUNDLE_DIR unset
    (lambda tmp: settings(walk_bundle_dir=str(tmp)), BundleError),                   # an empty directory
    (lambda tmp: settings(walk_bundle_dir=str(tmp / "missing")), BundleError),       # no such path
])
def test_startup_fails_loudly(tmp_path, make, error):
    app = create_app(make(tmp_path), provider_factory=estimate, setup_logging=False)
    with pytest.raises(error):
        with TestClient(app):
            pass
    assert app.state.walk.phase == "failed" and app.state.walk.error


def test_startup_refuses_a_tampered_bundle(tmp_path):
    copy_dir = tmp_path / MINI_BUNDLE.name
    shutil.copytree(MINI_BUNDLE, copy_dir)
    target = copy_dir / "interest" / "has_image.npy"
    data = bytearray(target.read_bytes())
    data[-1] ^= 0xFF
    target.write_bytes(bytes(data))
    app = create_app(settings(walk_bundle_dir=str(copy_dir)), provider_factory=estimate, setup_logging=False)
    with pytest.raises(BundleError, match="sha256 mismatch"):
        with TestClient(app):
            pass


def test_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "warn")
    monkeypatch.setenv("WALK_GEOMETRY_DEFAULT", " Polyline6 ")
    monkeypatch.setenv("WALK_VERIFY_BUNDLE", "false")
    monkeypatch.setenv("ORS_API_KEY", "never-a-setting")
    s = Settings()
    assert s.log_level == "WARNING" and s.log_level_no == logging.WARNING
    assert s.walk_geometry_default == "polyline6" and s.walk_verify_bundle is False and s.walk_bundle_dir is None
    assert "never-a-setting" not in json.dumps(s.model_dump()) + json.dumps(s.public())
    monkeypatch.setenv("LOG_LEVEL", "chatty")
    with pytest.raises(ValueError):
        Settings()
    monkeypatch.setenv("LOG_LEVEL", "info")
    monkeypatch.setenv("PHOTO_BASE_URL", "cdn.example.org/media")             # no scheme: refused at start-up
    with pytest.raises(ValueError, match="PHOTO_BASE_URL"):
        Settings()
    monkeypatch.setenv("PHOTO_BASE_URL", "/walk-media")
    assert Settings().photo_base_url == "/walk-media"


def _other_city(mini: LoadedBundle) -> LoadedBundle:
    rows = mini.catalog.rows.drop(columns=["wp_hours"]).copy()
    rows["city"] = "Othertown"
    catalog = CityCatalog.from_frame(rows, city="Othertown", version="othertown-test")
    return LoadedBundle(dir=MINI_BUNDLE, manifest={}, catalog=catalog, taste=None, bundle_id="othertown-test",
                        city="Othertown")


def test_several_cities(mini):
    other = _other_city(mini)
    body = mini_case("M01_default")["plan"]["body"]
    with serve([mini, other]) as c:
        assert c.get("/v1/walks/config", params={"city": "Othertown"}).json()["cities"] == ["Minitown", "Othertown"]
        no_city = c.post("/v1/walks/plan", json={k: v for k, v in body.items() if k != "city"})
        assert no_city.status_code == 422 and no_city.json()["error"]["params"]["field"] == "city"
        assert c.get("/v1/walks/config").status_code == 422
        r = c.post("/v1/walks/plan", json={**body, "city": "othertown", "favourite_place_ids": ["9100000000000104729"]})
        assert r.status_code == 200
        data = r.json()
        assert data["request"]["city"] == "Othertown" and data["versions"]["catalog"] == "othertown-test"
        assert "personalization_unavailable" in [m["code"] for m in data["messages"]]      # no taste artifacts
        unknown = c.post("/v1/walks/plan", json={**body, "city": "Atlantis"}).json()["error"]
        assert unknown["code"] == "unknown_city" and unknown["params"]["available"] == ["Minitown", "Othertown"]
        assert c.get("/v1/walks/places/9100000000000314187").status_code == 200
    with pytest.raises(StartupError, match="two bundles"):
        with serve([mini, mini]):
            pass


def test_supervisor_detection_is_off_outside_uvicorn_workers():
    assert _uvicorn_supervisor_pid() is None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("workers", [1, 2])
def test_a_fatal_startup_ends_uvicorn_instead_of_respawning_workers(tmp_path, workers):
    pytest.importorskip("uvicorn")
    env = {k: v for k, v in os.environ.items() if k not in ENV}
    env.update(WALK_BUNDLE_DIR=str(tmp_path / "no-bundle-here"), PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    cmd = [sys.executable, "-m", "uvicorn", "walk_planner.service.app:create_app", "--factory", "--host",
           "127.0.0.1", "--port", str(_free_port()), "--workers", str(workers),
           "--log-config", str(ROOT / "deploy" / "uvicorn-log-config.json")]                    # as the image runs it
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        out, _ = proc.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        pytest.fail("uvicorn kept running after a fatal start-up error:\n" + out.decode(errors="replace")[-3000:])
    text = out.decode(errors="replace")
    assert '"event": "startup_failed"' in text and "bundle path does not exist" in text
    # every line -- the workers' and uvicorn's supervisor's own ("Started parent process" ...) -- is JSON
    lines = [ln for ln in text.splitlines() if ln.strip()]
    records = [json.loads(ln) for ln in lines]
    assert all({"ts", "level", "logger", "msg"} <= set(r) for r in records)
    assert any(r["logger"].startswith("uvicorn") for r in records)
    if workers == 1:
        assert proc.returncode == 3                     # uvicorn's STARTUP_FAILURE
    else:
        assert '"event": "stopping_supervisor"' in text and text.count('"event": "startup_failed"') <= workers
    assert time.monotonic() - t0 < 90


# --------------------------------------------------------------------------- #
# concurrency
# --------------------------------------------------------------------------- #
def test_concurrent_plans_equal_sequential_ones(mini):
    cases = [mini_case(p.stem) for p in MINI_FILES]
    jobs = [c["plan"] for c in cases] * 2 + [cases[0]["plan"]] * 8
    with serve([mini], walk_max_concurrent_plans=8) as client:          # 8 threads: no request is refused
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda rec: replay(client, rec), jobs))
    for rec, (status, body) in zip(jobs, results):
        assert status == rec["status"]
        if status == 200:
            assert plan_differences(rec["response"], body) == []
        else:
            assert cli.compare_json(rec["response"], body) == []


# --------------------------------------------------------------------------- #
# OpenAPI
# --------------------------------------------------------------------------- #
def test_openapi_document_is_current():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import export_openapi
    finally:
        sys.path.remove(str(ROOT / "tools"))
    committed = ROOT / "docs" / "openapi.json"
    assert committed.is_file(), "run python tools/export_openapi.py"
    stored = json.loads(committed.read_text(encoding="utf-8"))
    import fastapi
    import pydantic
    made_with = stored["info"].get("x-generated-with", {})
    if (made_with.get("fastapi"), made_with.get("pydantic")) != (fastapi.__version__, pydantic.__version__):
        pytest.skip(f"docs/openapi.json was generated with other fastapi / pydantic versions ({made_with})")
    assert export_openapi.render(export_openapi.build_openapi()) == committed.read_text(encoding="utf-8")


def test_openapi_documents_the_contract(client):
    doc = client.get("/openapi.json").json()
    assert set(doc["paths"]) == {"/v1/health/live", "/v1/health/ready", "/v1/meta", "/v1/walks/config",
                                 "/v1/walks/plan", "/v1/walks/schedule", "/v1/walks/insert",
                                 "/v1/walks/places/search", "/v1/walks/places/{place_id}"}
    schemas = doc["components"]["schemas"]
    assert "HTTPValidationError" not in schemas                            # our error envelope everywhere
    ids = schemas["PlanRequest"]["properties"]["favourite_place_ids"]["anyOf"][0]["items"]
    assert ids["type"] == "string" and ids["pattern"] == S.PLACE_ID_PATTERN
    for path, ops in doc["paths"].items():
        for op in ops.values():
            assert "500" in op["responses"], path                                     # any endpoint may fail
    for path in ("/v1/walks/plan", "/v1/walks/schedule", "/v1/walks/insert"):
        responses = doc["paths"][path]["post"]["responses"]
        assert {"400", "413", "422", "500", "503"} <= set(responses), path
        assert "Retry-After" in responses["503"]["headers"]
        assert set(responses["503"]["content"]["application/json"]["examples"]) == {"not_ready", "busy"}
        for status, response in responses.items():
            if status != "200":
                assert response["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorBody"}
    ready = doc["paths"]["/v1/health/ready"]["get"]["responses"]["503"]["content"]["application/json"]
    assert set(ready["examples"]) == {"not_ready"}
    plan = doc["paths"]["/v1/walks/plan"]["post"]
    assert plan["operationId"] == "plan_walk"
    example = plan["requestBody"]["content"]["application/json"]["examples"]["default_loop"]["value"]
    assert example["slots"][0] == {"activity": "sight", "dwell_min": None}           # nulls survive
    assert plan["responses"]["200"]["content"]["application/json"]["example"] == S.PLAN_RESPONSE_EXAMPLE
