"""Tests of the acceptance tool ``python -m walk_planner golden run --url`` (walk_planner.cli.run_golden_http):
the golden expected API calls replayed against a RUNNING service and compared with the golden rules.

* through the FastAPI test client (a transport stand-in, no sockets): the mini set passes; a changed
  expected output is reported (exit 1); street routing / photo URLs on the service print a warning; a
  service that never gets ready is a setup error; a city the service does not serve fails the run;
* over real HTTP: uvicorn serving the mini bundle on a loopback port, driven by the CLI entry point.
"""
import copy
import json
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from walk_planner import cli  # noqa: E402
from walk_planner.bundle import load_bundle  # noqa: E402
from walk_planner.core import RoutingProvider  # noqa: E402
from walk_planner.service.app import create_app  # noqa: E402
from walk_planner.service.settings import Settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "tests" / "fixtures" / "mini"
BUNDLE = sorted(p for p in (MINI / "bundles").iterdir() if p.is_dir() and not p.name.startswith("."))[0]
SCENARIOS = json.loads((MINI / "scenarios.json").read_text(encoding="utf-8"))
EXPECTED = MINI / "expected"
ENV = ("WALK_BUNDLE_DIR", "WALK_ROUTER_URL", "ORS_API_KEY", "ORS_BASE_URL", "WALK_ROUTE_CACHE_REDIS_URL",
       "PHOTO_BASE_URL", "WALK_MAX_CONCURRENT_PLANS")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def settings(**over) -> Settings:
    values = dict(walk_bundle_dir=None, photo_base_url=None, walk_default_lang="ru", walk_geometry_default="geojson",
                  log_level="WARNING", environment="test", walk_git_sha="abc1234", walk_verify_bundle=True,
                  walk_startup_smoke_plan=False, walk_max_body_bytes=1_048_576, walk_max_concurrent_plans=2)
    values.update(over)
    return Settings(**values)


@pytest.fixture(scope="module")
def mini():
    return load_bundle(BUNDLE)


def client_transport(client, meta_patch=None):
    """A run_golden_http transport over the FastAPI test client; `meta_patch(meta)` edits /v1/meta answers."""
    def call(method, path, query, body, request_id=None):
        r = client.request(method, path, params=query, json=body,
                           headers={"X-Request-Id": request_id} if request_id else None)
        data = r.json()
        if path == "/v1/meta" and meta_patch is not None:
            data = meta_patch(copy.deepcopy(data))
        return r.status_code, data
    return call


@pytest.fixture(scope="module")
def client(mini):
    app = create_app(settings(), bundles=[mini], provider_factory=lambda _s: RoutingProvider(), setup_logging=False)
    with TestClient(app) as c:
        yield c


def run(transport, expected=EXPECTED, **kw):
    lines = []
    code = cli.run_golden_http("http://walk-planner.test", SCENARIOS, expected, out=lines.append, wait_s=0,
                               transport=transport, **kw)
    return code, "\n".join(lines)


def test_the_mini_service_passes(client):
    code, out = run(client_transport(client))
    assert code == 0, out
    assert f"bundle {BUNDLE.name} (Minitown)" in out and "8 scenarios: 8 pass" in out
    assert "WARNING" not in out
    for sc in SCENARIOS:
        assert f"\n{sc['id']}" in "\n" + out


def test_a_changed_expected_output_is_reported(client, tmp_path):
    exp = tmp_path / "expected"
    shutil.copytree(EXPECTED, exp)
    f = exp / BUNDLE.name / "M01_default.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    data["plan"]["response"]["variants"][0]["stops"][0]["name"] = "Elsewhere"
    data["edits"]["A"][0]["call"]["response"]["variant"]["summary"]["walk_min"] += 0.5
    f.write_text(cli.golden_dumps(data) + "\n", encoding="utf-8")
    code, out = run(client_transport(client), expected=exp, only={"M01_default", "M03_free_chill"})
    assert code == 1
    assert "M01_default.plan.response.variants[0].stops[0].name: expected \"Elsewhere\"" in out
    assert ".edits.A[0].call.response.variant.summary.walk_min" in out
    assert "2 scenarios: 1 diff, 1 pass" in out


def test_street_routing_and_photo_urls_on_the_service_are_flagged(client):
    def streets(meta):
        meta["routing"].update(chain=["osrm", "ors", "estimate"], configured=True)
        meta["settings"]["photo_base_url"] = "https://cdn.example.org/walk-media"
        return meta

    code, out = run(client_transport(client, streets), only={"M01_default"})
    assert "WARNING: http://walk-planner.test routes through streets (chain osrm -> ors -> estimate)" in out
    assert "WALK_ROUTER_URL= and ORS_API_KEY= set EMPTY" in out and "WARNING: PHOTO_BASE_URL is set" in out
    assert code == 0                                  # the replies themselves are still the estimate ones here


def test_a_service_that_is_not_ready_or_not_the_planner_is_a_setup_error(client):
    def starting(meta):
        meta.update(ready=False, state="starting")
        return meta

    with pytest.raises(cli.CliError, match="not ready"):
        run(client_transport(client, starting))
    with pytest.raises(cli.CliError, match="not the walk-planner service"):
        run(client_transport(client, lambda meta: {"service": "something-else"}))


def test_a_city_the_service_does_not_serve_fails_the_run(client):
    other = [dict(SCENARIOS[0], id="X01_elsewhere", city="Othertown")]
    lines = []
    code = cli.run_golden_http("http://walk-planner.test", other, EXPECTED, out=lines.append, wait_s=0,
                               transport=client_transport(client))
    assert code == 1 and "no bundle for city 'Othertown'" in "\n".join(lines)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_over_real_http_with_the_cli(mini, capsys):
    uvicorn = pytest.importorskip("uvicorn")
    app = create_app(settings(), bundles=[mini], setup_logging=False)          # default factory: no routing env
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on",
                                           ws="none"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        code = cli.main(["golden", "run", "--url", f"http://127.0.0.1:{port}", "--scenarios", str(MINI / "scenarios.json"),
                         "--expected-root", str(EXPECTED), "--wait", "10"])
        out = capsys.readouterr().out
    finally:
        server.should_exit = True
        thread.join(timeout=30)
    assert code == 0, out
    assert "8 scenarios: 8 pass" in out and f"against http://127.0.0.1:{port}" in out
