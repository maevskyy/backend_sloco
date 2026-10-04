"""Offline tests of the production routing chain (walk_planner/routing.py).

Real response shapes, no network:
 * OSRM ``steps=true`` = a real FOSSGIS foot answer (OSRM 5.27.1) saved in fixtures/route_steps_fossgis.json
   (Piața Universității -> Macca-Villacrosse -> Radu Vodă -> Poenaru Bordea -> back);
 * OSRM ``overview=by_legs`` = the same legs re-shaped the way v26.x answers it (legs[i].geometry, no
   route-level geometry);
 * ORS = the geojson shape (features[0].properties.way_points / segments) built from the same legs.
HTTP goes through injected fake sessions; sockets are blocked for every test in this module.
"""

import copy
import hashlib
import json
import socket
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import walk_planner
from walk_planner import (
    Candidate,
    RoutingProvider,
    WalkRequest,
    haversine_km,
    plan_sequence,
    plan_variants,
)
from walk_planner import core, routing
from walk_planner.routing import (
    DEFAULT_ORS_BASE_URL,
    Breaker,
    ChainProvider,
    LegCache,
    ORSProvider,
    ORSRouter,
    OSRMRouter,
    RateLimiter,
    RoutingError,
    make_provider,
    polyline6_decode,
    polyline6_encode,
    routing_status,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "route_steps_fossgis.json"
REAL = json.loads(FIXTURE.read_text(encoding="utf-8"))
SEQ = [tuple(p) for p in REAL["seq"]]                    # start, 3 places, start  (lat, lon)
REAL_LEGS = REAL["resp"]["routes"][0]["legs"]


@pytest.fixture(autouse=True)
def _offline_and_fresh(monkeypatch):
    """No socket may open; process-wide routers / caches / counters start empty for every test."""
    def deny(*_a, **_k):
        raise RuntimeError("network access in tests")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    routing.reset_routing_state()
    yield
    routing.reset_routing_state()


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class Resp:
    def __init__(self, status, payload=None, headers=None, text=None):
        self.status_code, self._p = status, payload
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


def _query(url):
    q = urlsplit(url).query
    return dict(kv.split("=", 1) for kv in q.split("&")) if q else {}


class FakeSession:
    """Records every call; ``handler(method, url, query_or_json_body)`` returns a ``Resp`` or raises."""

    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, url, timeout=None, **_kw):
        q = _query(url)
        self.calls.append({"method": "GET", "url": url, "query": q, "timeout": timeout})
        return self.handler("GET", url, q)

    def post(self, url, json=None, headers=None, timeout=None, **_kw):
        self.calls.append({"method": "POST", "url": url, "json": json, "headers": headers, "timeout": timeout})
        return self.handler("POST", url, json)


class FakeRedis:
    def __init__(self):
        self.store, self.ex, self.fail = {}, {}, False

    def mget(self, keys):
        if self.fail:
            raise ConnectionError("redis down")
        return [self.store.get(k) for k in keys]

    def pipeline(self, transaction=True):
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def set(self, k, v, ex=None):
        self.ops.append((k, v, ex))
        return self

    def execute(self):
        if self.r.fail:
            raise ConnectionError("redis down")
        for k, v, ex in self.ops:
            self.r.store[k], self.r.ex[k] = v.encode(), ex
        return [True] * len(self.ops)


def osrm_by_legs_payload(n_legs=None, data_version=None):
    """REAL legs re-shaped as OSRM >= v26.4 answers overview=by_legs&geometries=geojson&steps=false."""
    r = copy.deepcopy(REAL["resp"])
    route = r["routes"][0]
    for leg in route["legs"]:
        leg["geometry"] = {"type": "LineString", "coordinates": routing._chain_steps(leg["steps"])}
        leg["steps"] = []
    route.pop("geometry", None)
    if n_legs is not None:
        route["legs"] = route["legs"][:n_legs]
    if data_version:
        r["data_version"] = data_version
    return r


def ors_payload():
    """An ORS geojson answer for SEQ built from the REAL OSRM legs (same numbers, ORS shape)."""
    legs = [routing._chain_steps(leg["steps"]) for leg in REAL_LEGS]
    geom, wps = list(legs[0]), [0, len(legs[0]) - 1]
    for g in legs[1:]:
        geom += g[1:]
        wps.append(len(geom) - 1)
    segs = [{"distance": leg["distance"], "duration": leg["duration"]} for leg in REAL_LEGS]
    return {"features": [{"geometry": {"coordinates": geom}, "properties": {"way_points": wps, "segments": segs}}]}


def generic_osrm(method, url, query, data_version=None):
    """An OSRM by_legs answer for ANY request: 3-point legs, street distance = 1.25 x straight line, 5 km/h."""
    lonlat = [tuple(map(float, p.split(","))) for p in urlsplit(url).path.rsplit("/", 1)[-1].split(";")]
    legs = []
    for a, b in zip(lonlat, lonlat[1:]):
        m = haversine_km(a[1], a[0], b[1], b[0]) * 1000.0 * 1.25
        legs.append({"distance": m, "duration": m / (5000.0 / 3600.0), "summary": "", "steps": [],
                     "geometry": {"type": "LineString",
                                  "coordinates": [list(a), [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], list(b)]}})
    body = {"code": "Ok", "routes": [{"legs": legs}], "waypoints": []}
    if data_version:
        body["data_version"] = data_version
    return Resp(200, body)


def generic_ors(method, url, body):
    n = len(body["coordinates"])
    geom = [list(c) for c in body["coordinates"]]
    return Resp(200, {"features": [{"geometry": {"coordinates": geom},
                                    "properties": {"way_points": list(range(n)),
                                                   "segments": [{"distance": 111.0, "duration": 80.0}] * (n - 1)}}]})


def _osrm(handler, **kw):
    kw.setdefault("retry_backoff_s", (0.0, 0.0))
    return OSRMRouter("http://osrm:5000", session=FakeSession(handler), **kw)


def _ors(handler, **kw):
    kw.setdefault("limiter", RateLimiter(1000, 1000))
    return ORSRouter("k", session=FakeSession(handler), **kw)


def _routers(osrm_handler, ors_handler):
    return _osrm(osrm_handler), _ors(ors_handler)


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# --------------------------------------------------------------------------- #
# polyline6
# --------------------------------------------------------------------------- #
def test_polyline_matches_the_google_reference_example():
    pts = [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]]          # [lon, lat]
    assert routing.polyline_encode(pts, precision=5) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert routing.polyline_decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5) == pts


def test_polyline6_roundtrip_on_real_geometry():
    g = routing._chain_steps(REAL_LEGS[1]["steps"])
    enc = polyline6_encode(g)
    dec = polyline6_decode(enc)
    assert len(dec) == len(g)
    assert max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(dec, g)) < 2e-6
    assert dec == g                                           # OSRM coordinates have 6 decimals: exact
    assert len(enc) < len(json.dumps(g)) / 3                  # ~3-4x smaller than JSON floats


def test_polyline6_decode_rejects_malformed_input():
    enc = polyline6_encode([[26.1, 44.4], [26.2, 44.5]])
    with pytest.raises(ValueError):
        polyline6_decode(enc[:-1])                            # truncated
    with pytest.raises(ValueError):
        polyline6_decode("\x01\x02")                           # outside the alphabet


# --------------------------------------------------------------------------- #
# OSRM adapter
# --------------------------------------------------------------------------- #
def test_osrm_steps_split_equals_full_overview():
    route = REAL["resp"]["routes"][0]
    chain = []
    for leg in route["legs"]:
        g = routing._chain_steps(leg["steps"])
        chain += g if not chain else g[1:]
    assert chain == route["geometry"]["coordinates"]          # verified on the real response


def test_osrm_by_legs_one_request_and_contract():
    o = _osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload()))
    legs = o.legs(SEQ)
    calls = o.session.calls
    assert len(calls) == 1
    q = calls[0]["query"]
    assert (q["overview"], q["steps"], q["geometries"], q["generate_hints"]) == ("by_legs", "false", "geojson", "false")
    assert q["radiuses"] == ";".join(["1000"] * len(SEQ))     # one radius per point, ";" kept literal
    assert "/route/v1/foot/26.102500,44.435500;" in calls[0]["url"]          # lon,lat order
    assert calls[0]["timeout"] == (0.5, 2.0)
    assert len(legs) == len(SEQ) - 1
    for i, leg in enumerate(legs):
        assert set(leg) == {"geometry", "duration_min", "distance_km", "quality", "provider"}
        assert (leg["quality"], leg["provider"]) == ("streets", "osrm")
        if i:
            assert leg["geometry"][0] == legs[i - 1]["geometry"][-1]       # legs chain at the waypoints
    assert legs[0]["distance_km"] == pytest.approx(0.5666, abs=1e-3)
    assert legs[1]["duration_min"] == pytest.approx(REAL_LEGS[1]["duration"] / 60.0)


def test_osrm_old_server_falls_back_to_steps_once():
    def h(m, u, q):
        if q["overview"] == "by_legs":
            return Resp(400, {"code": "InvalidQuery", "message": "Query string malformed close to position 146"})
        return Resp(200, REAL["resp"])
    o = _osrm(h)
    legs = o.legs(SEQ)
    assert [c["query"]["overview"] for c in o.session.calls] == ["by_legs", "false"] and o.by_legs is False
    assert o.session.calls[1]["query"]["steps"] == "true"
    assert len(legs) == 4 and legs[1]["duration_min"] == pytest.approx(REAL_LEGS[1]["duration"] / 60.0)
    o.legs(SEQ)
    assert len(o.session.calls) == 3                          # remembers: no second by_legs probe
    assert o.status()["mode"] == "steps"


def test_osrm_bad_input_does_not_switch_a_modern_server_to_steps():
    o = _osrm(lambda m, u, q: Resp(400, {"code": "InvalidValue", "message": "Invalid coordinate value."}))
    with pytest.raises(RoutingError) as e:
        o.legs(SEQ)
    assert e.value.kind == "bad_request" and o.by_legs is True   # steps failed too -> stays by_legs
    assert o.breaker.snapshot()["state"] == "closed"            # the server answered: not a health failure


def test_osrm_no_segment_is_an_input_error():
    o = _osrm(lambda m, u, q: Resp(400, {"code": "NoSegment", "message": "Could not find a matching segment"}))
    with pytest.raises(RoutingError) as e:
        o.legs(SEQ)
    assert e.value.kind == "no_segment" and e.value.code == "NoSegment"
    assert len(o.session.calls) == 1                           # deterministic: no retry
    assert o.breaker.snapshot()["consecutive_failures"] == 0


def test_osrm_leg_count_mismatch_raises():
    o = _osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload(n_legs=2)))
    with pytest.raises(RoutingError) as e:
        o.legs(SEQ)
    assert e.value.kind == "mismatch"


def test_osrm_retries_once_on_connect_error_and_gateway_errors_only():
    import requests
    seen = []

    def flaky(m, u, q):
        seen.append(1)
        if len(seen) == 1:
            raise requests.exceptions.ConnectionError("stale keep-alive connection")
        return Resp(200, osrm_by_legs_payload())
    o = _osrm(flaky)
    assert len(o.legs(SEQ)) == 4 and len(o.session.calls) == 2      # one retry, then fine

    gw = iter([Resp(503, {}), Resp(200, osrm_by_legs_payload())])
    o = _osrm(lambda m, u, q: next(gw))
    assert len(o.legs(SEQ)) == 4 and len(o.session.calls) == 2

    o = _osrm(lambda m, u, q: Resp(502, {}))
    with pytest.raises(RoutingError) as e:
        o.legs(SEQ)
    assert e.value.kind == "http_5xx" and len(o.session.calls) == 2      # 1 + 1 retry, then give up

    o = _osrm(lambda m, u, q: Resp(400, {"code": "NoRoute", "message": "Impossible route between points"}))
    with pytest.raises(RoutingError):
        o.legs(SEQ)
    assert len(o.session.calls) == 1                                    # no retry on 4xx


def test_osrm_non_osrm_answers_are_parse_errors_that_open_the_breaker():
    o = _osrm(lambda m, u, q: Resp(404, ValueError("not json"), text="<html>404</html>"), retries=0)
    for _ in range(3):
        with pytest.raises(RoutingError) as e:
            o.legs(SEQ)
        assert e.value.kind == "parse"
    assert o.breaker.snapshot()["state"] == "open"
    with pytest.raises(RoutingError) as e:
        o.legs(SEQ)
    assert e.value.kind == "breaker_open" and len(o.session.calls) == 3   # no HTTP while open
    o2 = _osrm(lambda m, u, q: Resp(403, {"message": "Forbidden"}))       # a proxy's JSON, not OSRM's
    with pytest.raises(RoutingError) as e:
        o2.legs(SEQ)
    assert e.value.kind == "parse"


def test_osrm_timeouts_are_capped_by_the_routing_budget():
    o = _osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload()))
    o.legs(SEQ, budget_s=0.3)
    connect, read = o.session.calls[0]["timeout"]
    assert connect <= 0.3 + 1e-9 and read <= 0.3 + 1e-9
    with pytest.raises(RoutingError) as e:
        o.legs(SEQ, budget_s=0.01)                              # not even one attempt fits
    assert e.value.kind == "deadline" and len(o.session.calls) == 1
    assert o.breaker.snapshot()["consecutive_failures"] == 0    # no information about the server


def test_osrm_data_version_from_answers_and_probe():
    o = _osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload(data_version="romania-260930")))
    assert o.dataset == "unknown"
    o.legs(SEQ)
    assert o.data_version == "romania-260930" and o.dataset == "romania-260930"

    def nearest(m, u, q):
        assert "/nearest/v1/foot/" in u and q["number"] == "1"
        return Resp(200, {"code": "Ok", "data_version": "romania-261031", "waypoints": [{"name": "x"}]})
    p = _osrm(nearest)
    assert p.probe() == "romania-261031" and p.last_probe["ok"] is True
    down = _osrm(lambda m, u, q: (_ for _ in ()).throw(ConnectionError("refused")))
    assert down.probe() is None and down.last_probe["ok"] is False      # never raises
    assert down.breaker.snapshot()["consecutive_failures"] == 0         # probes don't touch the breaker


def test_osrm_background_probe_runs_at_most_once_per_interval():
    o = _osrm(lambda m, u, q: Resp(200, {"code": "Ok", "data_version": "romania-260930", "waypoints": []}))
    assert o.maybe_probe_async(0) is False                    # disabled
    assert o.maybe_probe_async(60.0) is True
    deadline = time.monotonic() + 5.0
    while o.last_probe is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert o.last_probe["ok"] and o.data_version == "romania-260930"
    assert o.maybe_probe_async(60.0) is False                 # too soon
    assert len(o.session.calls) == 1


# --------------------------------------------------------------------------- #
# ORS adapter
# --------------------------------------------------------------------------- #
def test_ors_new_host_one_request_and_split():
    r = _ors(lambda m, u, body: Resp(200, ors_payload()))
    legs = r.legs(SEQ)
    calls = r.session.calls
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson"
    assert calls[0]["headers"]["Authorization"] == "k" and calls[0]["timeout"] == (2.0, 8.0)
    assert calls[0]["json"]["coordinates"][0] == [26.1025, 44.4355]          # [lon, lat]
    assert [len(leg["geometry"]) for leg in legs] == [len(routing._chain_steps(leg["steps"])) for leg in REAL_LEGS]
    assert {(leg["quality"], leg["provider"]) for leg in legs} == {("streets", "ors")}
    assert legs[2]["distance_km"] == pytest.approx(REAL_LEGS[2]["distance"] / 1000.0)


def test_ors_chunks_long_routes_at_50_waypoints():
    coords = [(44.43 + i * 1e-4, 26.10) for i in range(120)]
    r = _ors(generic_ors)
    legs = r.legs(coords)
    calls = r.session.calls
    assert len(legs) == 119 and [len(c["json"]["coordinates"]) for c in calls] == [50, 50, 22]
    assert calls[1]["json"]["coordinates"][0] == calls[0]["json"]["coordinates"][-1]   # chunks share a point
    assert r.limiter.snapshot()["remaining_minute"] == 1000 - 3                         # one token per request


def test_ors_quota_403_opens_the_breaker_until_the_quota_resets():
    reset = time.time() + 7200
    r = _ors(lambda m, u, b: Resp(403, {"error": "Quota exceeded"},
                                  headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "2000",
                                           "X-RateLimit-Reset": str(int(reset))}))
    with pytest.raises(RoutingError) as e:
        r.legs(SEQ)
    assert e.value.kind == "quota"
    snap = r.breaker.snapshot()
    assert snap["state"] == "open" and 7000 < snap["open_for_s"] <= 7200
    assert r.status()["quota"]["remaining"] == 0 and r.status()["quota"]["limit"] == 2000
    with pytest.raises(RoutingError) as e:
        r.legs(SEQ)
    assert e.value.kind == "breaker_open" and len(r.session.calls) == 1

    r = _ors(lambda m, u, b: Resp(403, {"error": "Quota exceeded"}))       # no reset header -> 1 h
    with pytest.raises(RoutingError):
        r.legs(SEQ)
    assert 3500 < r.breaker.snapshot()["open_for_s"] <= 3600


def test_ors_429_is_rate_limited_for_retry_after():
    r = _ors(lambda m, u, b: Resp(429, {"error": "Rate limit exceeded"}, headers={"Retry-After": "20"}))
    with pytest.raises(RoutingError) as e:
        r.legs(SEQ)
    assert e.value.kind == "rate_limited"
    assert 15 < r.breaker.snapshot()["open_for_s"] <= 20
    r = _ors(lambda m, u, b: Resp(429, {"error": "Rate limit exceeded"}))
    with pytest.raises(RoutingError):
        r.legs(SEQ)
    assert 55 < r.breaker.snapshot()["open_for_s"] <= 60


def test_ors_local_limiter_blocks_without_an_http_call():
    r = _ors(lambda m, u, b: Resp(200, ors_payload()), limiter=RateLimiter(per_minute=1, per_day=100))
    r.legs(SEQ)
    with pytest.raises(RoutingError) as e:
        r.legs(SEQ)
    assert (e.value.kind, e.value.detail) == ("rate_limited", "local limiter")
    assert len(r.session.calls) == 1 and r.breaker.snapshot()["state"] == "closed"


def test_ors_error_codes_map_to_kinds_and_coordinates_are_scrubbed():
    msg = ("Could not find routable point within a radius of 350.0 meters of specified coordinate 0: "
           "26.1025000 44.4355000.")
    r = _ors(lambda m, u, b: Resp(404, {"error": {"code": 2010, "message": msg}}))
    with pytest.raises(RoutingError) as e:
        r.legs(SEQ)
    assert e.value.kind == "no_segment" and e.value.code == "2010"
    assert "44.4355" not in e.value.detail and "26.1025" not in str(e.value)
    r = _ors(lambda m, u, b: Resp(404, {"error": {"code": 2009, "message": "Route could not be found"}}))
    with pytest.raises(RoutingError) as e:
        r.legs(SEQ)
    assert e.value.kind == "no_route"
    r = _ors(lambda m, u, b: Resp(400, {"error": {"code": 2004, "message": "Request parameters exceed limits"}}))
    with pytest.raises(RoutingError) as e:
        r.legs(SEQ)
    assert e.value.kind == "bad_request" and r.breaker.snapshot()["state"] == "closed"


@pytest.mark.parametrize("resp", [
    Resp(404, ValueError("not JSON"), text="<html><body><h1>404 Not Found</h1></body></html>"),   # wrong base URL
    Resp(404, {"message": "no Route matched with those values"}),                                  # a gateway's JSON
    Resp(404, {"error": "Not Found"}),
    Resp(405, {"error": {"message": "Method Not Allowed"}}),                                       # no ORS code
    Resp(400, {"error": {"code": "2004", "message": "a code that is not an integer"}}),
    Resp(400, {"error": {"code": True}}),
    Resp(400, ["not", "an", "object"]),
    Resp(302, ValueError("not JSON"), text=""),
])
def test_ors_4xx_that_is_not_an_ors_error_answer_is_a_configuration_error(resp):
    # v0 took e.g. {"error": "Not Found"} for a rejected input ("bad_request" = server healthy), so a wrong
    # ORS_BASE_URL cost one request (and routing budget) on every plan and never opened the breaker
    r = _ors(lambda m, u, b: resp)
    with pytest.raises(RoutingError) as e:
        r.legs(SEQ)
    assert (e.value.kind, e.value.breaker_effect) == ("config", "fail") and "ORS_BASE_URL" in e.value.detail
    snap = r.breaker.snapshot()
    assert snap["state"] == "open" and 3500 < snap["open_for_s"] <= routing.CONFIG_ERROR_COOLDOWN_S == 3600
    with pytest.raises(RoutingError) as e:                       # not asked again during the cooldown
        r.legs(SEQ)
    assert e.value.kind == "breaker_open" and len(r.session.calls) == 1


def test_ors_error_answers_and_non_json_200_keep_their_kinds():
    for status, code, kind in ((400, 2003, "bad_request"), (404, 2010, "no_segment"), (404, 2009, "no_route"),
                               (413, 2004, "bad_request")):
        r = _ors(lambda m, u, b: Resp(status, {"error": {"code": code, "message": "x"}, "info": {}}))
        with pytest.raises(RoutingError) as e:
            r.legs(SEQ)
        assert e.value.kind == kind and r.breaker.snapshot()["state"] == "closed"
    r = _ors(lambda m, u, b: Resp(200, ValueError("not JSON"), text="<html>captive portal</html>"))
    with pytest.raises(RoutingError) as e:
        r.legs(SEQ)
    assert e.value.kind == "parse" and r.breaker.snapshot()["state"] == "closed"     # 1 of 2 failures


def test_chain_with_a_misconfigured_ors_falls_back_and_stops_asking_it():
    ors = _ors(lambda m, u, b: Resp(404, ValueError("not JSON"), text="<html>Not Found</html>"))
    for _request in range(2):
        chain = ChainProvider([ors])
        legs = chain.route_legs(SEQ)
        assert len(legs) == len(SEQ) - 1 and {(leg["quality"], leg["provider"]) for leg in legs} == \
            {("estimate", "haversine")}
    assert len(ors.session.calls) == 1
    assert [ev["outcome"] for ev in chain.events] == ["breaker_open", "estimate"]
    assert routing_status(env={})["counters"].get("ors.config") == 1


def test_ors_401_and_missing_key():
    r = _ors(lambda m, u, b: Resp(401, {"error": "Authorization field missing"}))
    with pytest.raises(RoutingError) as e:
        r.legs(SEQ)
    assert e.value.kind == "auth" and 3500 < r.breaker.snapshot()["open_for_s"] <= 3600
    s = FakeSession(lambda m, u, b: Resp(200, ors_payload()))
    with pytest.raises(RoutingError) as e:
        ORSRouter("", session=s).legs(SEQ)
    assert e.value.kind == "disabled" and not s.calls
    assert "secret" not in repr(ORSRouter("secret-key"))


def test_ors_timeouts_are_not_retried_and_two_open_the_breaker():
    import requests

    def slow(m, u, b):
        raise requests.exceptions.ReadTimeout("read timed out")
    r = _ors(slow)
    for n in (1, 2):
        with pytest.raises(RoutingError) as e:
            r.legs(SEQ)
        assert e.value.kind == "timeout" and len(r.session.calls) == n       # no retries: every call costs quota
    snap = r.breaker.snapshot()
    assert snap["state"] == "open" and 295 < snap["open_for_s"] <= 300


def test_ors_leg_count_mismatch_raises():
    bad = ors_payload()
    bad["features"][0]["properties"]["segments"] = bad["features"][0]["properties"]["segments"][:2]
    r = _ors(lambda m, u, b: Resp(200, bad))
    with pytest.raises(RoutingError) as e:
        r.legs(SEQ)
    assert e.value.kind == "mismatch"


# --------------------------------------------------------------------------- #
# Breaker / limiter
# --------------------------------------------------------------------------- #
def test_breaker_opens_after_threshold_then_lets_one_probe_through():
    clk = FakeClock(0.0)
    b = Breaker(threshold=3, cooldown_s=30.0, probe_timeout_s=10.0, clock=clk)
    b.failure()
    b.failure()
    assert b.allow()                                        # 2 < threshold
    b.failure()
    assert not b.allow() and b.snapshot()["state"] == "open"
    clk.t = 31.0
    assert b.snapshot()["state"] == "half_open"
    assert b.allow() and not b.allow()                      # exactly one probe
    b.failure()                                             # the probe failed -> open again
    assert not b.allow() and b.snapshot()["state"] == "open"
    clk.t = 62.0
    assert b.allow()
    b.success()
    assert b.allow() and b.allow() and b.snapshot() == {"state": "closed", "open_for_s": 0.0,
                                                         "consecutive_failures": 0}


def test_breaker_explicit_cooldown_probe_timeout_and_release():
    clk = FakeClock(0.0)
    b = Breaker(threshold=3, cooldown_s=30.0, probe_timeout_s=10.0, clock=clk)
    b.failure(cooldown_s=5.0)                               # opens at once (e.g. a quota answer)
    assert not b.allow()
    clk.t = 6.0
    assert b.allow() and not b.allow()                      # probe out
    clk.t = 17.0
    assert b.allow()                                        # a probe that never reported frees the slot
    b.release()                                             # ... and a call that did not happen frees it too
    assert b.allow()
    b.failure(cooldown_s=3600.0)                            # quota: 1 h
    b.failure(cooldown_s=60.0)                              # a late 429 answer must not shorten it
    assert b.snapshot()["open_for_s"] == pytest.approx(3600.0)


def test_ors_ignores_nonsense_quota_headers():
    r = _ors(lambda m, u, b: Resp(403, {"error": "Quota exceeded"},
                                  headers={"x-ratelimit-reset": "1e20", "x-ratelimit-remaining": "abc"}))
    with pytest.raises(RoutingError):
        r.legs(SEQ)
    st = r.status()
    assert st["quota"] == {"remaining": None, "limit": None, "reset_at": None}
    assert 3500 < st["breaker"]["open_for_s"] <= 3600                   # falls back to 1 h


def test_rate_limiter_sliding_minute_and_day_windows():
    clk = FakeClock(0.0)
    lim = RateLimiter(per_minute=2, per_day=3, clock=clk)
    assert lim.try_acquire() and lim.try_acquire() and not lim.try_acquire()
    clk.t = 61.0
    assert lim.try_acquire() and not lim.try_acquire()      # minute freed, day (3) used up
    assert lim.snapshot()["remaining_day"] == 0
    clk.t = 86400.0 + 62.0
    assert lim.try_acquire() and lim.snapshot()["remaining_day"] == 2


# --------------------------------------------------------------------------- #
# Leg cache
# --------------------------------------------------------------------------- #
def _leg(provider="osrm", quality="streets", n=3):
    return {"geometry": [[round(26.1 + i * 1e-3, 6), round(44.43 + i * 1e-3, 6)] for i in range(n)],
            "duration_min": 7.625, "distance_km": 0.5666, "quality": quality, "provider": provider}


def test_leg_cache_key_format_is_directional():
    c = LegCache()
    a, b = (44.43, 26.1), (44.44, 26.11)
    raw = "44.43000,26.10000>44.44000,26.11000"
    assert c.key(a, b, "romania-260930") == "wr:v1:romania-260930:" + hashlib.sha1(raw.encode()).hexdigest()
    assert c.key(b, a, "romania-260930") != c.key(a, b, "romania-260930")
    assert c.key((44.430001, 26.1), b, "x") == c.key(a, b, "x")             # 5 decimals (~1 m)


def test_leg_cache_ttls_by_provider_and_start_and_estimates_never():
    r = FakeRedis()
    c = LegCache(redis_client=r)
    n = c.set_many([("k_osrm", _leg("osrm"), False), ("k_ors", _leg("ors"), False),
                    ("k_start", _leg("osrm"), True), ("k_est", _leg("haversine", "estimate"), False)])
    assert n == 3 and r.ex == {"k_osrm": 30 * 86400, "k_ors": 86400, "k_start": 3600}
    got = c.get_many(["k_osrm", "k_est", "nope"])
    assert got[1] is None and got[2] is None
    assert (got[0]["quality"], got[0]["provider"], got[0]["cached"]) == ("streets", "osrm", True)
    assert got[0]["duration_min"] == pytest.approx(7.625) and got[0]["distance_km"] == pytest.approx(0.5666)
    assert got[0]["geometry"] == _leg()["geometry"]
    assert c.set_many([("k_again", got[0], False)]) == 0                  # cache hits are not re-written


def test_leg_cache_l1_expires_and_evicts_least_recently_used():
    clk = FakeClock(1_000_000.0)
    c = LegCache(max_entries=2, clock=clk)
    c.set_many([("a", _leg(), True), ("b", _leg(), False)])
    clk.t += 3601                                            # the start-touching leg is gone after 1 h
    assert [x is not None for x in c.get_many(["a", "b"])] == [False, True]
    c.set_many([("c", _leg(), False), ("d", _leg(), False)])                 # b is evicted (LRU, size 2)
    assert [x is not None for x in c.get_many(["b", "c", "d"])] == [False, True, True]
    assert len(c) == 2


def test_leg_cache_redis_is_shared_and_its_errors_never_fail_a_plan():
    r = FakeRedis()
    worker1, worker2 = LegCache(redis_client=r), LegCache(redis_client=r)
    worker1.set_many([("k", _leg(), False)])
    hit = worker2.get_many(["k"])[0]                         # L2 hit in another process
    assert hit is not None and worker2.status()["hits_l2"] == 1
    r.store.clear()
    assert worker2.get_many(["k"])[0] is not None and worker2.status()["hits_l1"] == 1   # now in its L1
    r.fail = True
    assert worker2.get_many(["x", "y"]) == [None, None]
    assert worker2.set_many([("z", _leg(), False)]) == 1     # L1 still written
    assert worker2.status()["errors"] >= 2


def test_leg_cache_corrupt_entries_are_misses():
    r = FakeRedis()
    c = LegCache(redis_client=r)
    r.store["bad_json"] = b"{not json"
    r.store["bad_geom"] = json.dumps({"d": 1, "m": 1, "g": "\x01", "p": "osrm", "x": time.time() + 99}).encode()
    assert c.get_many(["bad_json", "bad_geom"]) == [None, None]
    assert c.status()["errors"] == 2


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #
def test_chain_primary_ok_then_cache_hit_needs_no_call():
    o, r = _routers(lambda m, u, q: Resp(200, osrm_by_legs_payload()), lambda m, u, b: Resp(500, {}))
    cache = LegCache()
    first = ChainProvider([o, r], cache=cache, start=SEQ[0])
    legs = first.route_legs(SEQ)
    assert {leg["provider"] for leg in legs} == {"osrm"}
    again = ChainProvider([o, r], cache=cache).route_legs(SEQ)
    assert len(o.session.calls) == 1 and len(r.session.calls) == 0
    assert all(leg["cached"] and leg["quality"] == "streets" for leg in again)
    assert again[2]["distance_km"] == pytest.approx(legs[2]["distance_km"], abs=1e-9)
    assert again[2]["geometry"] == legs[2]["geometry"]
    assert first.events[-1]["outcome"] == "ok"


def test_chain_primary_down_uses_ors_then_breaker_skips_primary():
    import requests

    def down(m, u, q):
        raise requests.exceptions.ConnectionError("connection refused")
    o, r = _routers(down, lambda m, u, b: Resp(200, ors_payload()))
    for _ in range(3):                                       # 3 requests, OSRM tried (with its retry) in each
        legs = ChainProvider([o, r]).route_legs(SEQ)
        assert {leg["provider"] for leg in legs} == {"ors"}
    assert len(o.session.calls) == 6 and not o.breaker.allow()     # opened after 3 failed calls
    ch = ChainProvider([o, r])
    ch.route_legs(SEQ)
    assert len(o.session.calls) == 6                                  # skipped while open
    assert [e["outcome"] for e in ch.events] == ["breaker_open", "ok"]


def test_chain_everything_down_returns_flagged_estimates_and_keeps_cached_streets():
    o, r = _routers(lambda m, u, q: Resp(200, osrm_by_legs_payload(n_legs=2)),
                    lambda m, u, b: Resp(403, {"error": "Quota exceeded"}))
    cache = LegCache()
    ChainProvider([o], cache=cache).route_legs(SEQ[:3])              # warm the first 2 legs
    o.session.handler = lambda m, u, q: Resp(503, {})
    ch = ChainProvider([o, r], cache=cache)
    legs = ch.route_legs(SEQ)
    assert [leg["quality"] for leg in legs] == ["streets", "streets", "estimate", "estimate"]
    assert legs[2]["provider"] == "haversine" and len(legs[2]["geometry"]) == 2
    est = RoutingProvider().route([SEQ[2], SEQ[3]])
    assert legs[2]["duration_min"] == est["duration_min"]            # exactly the straight-line estimate
    assert [e["outcome"] for e in ch.events] == ["partial", "http_5xx", "quota", "estimate"]
    assert ch.summary()["legs"] == {"streets": 2, "estimate": 2}


def test_chain_no_segment_does_not_burn_ors_quota():
    o, r = _routers(lambda m, u, q: Resp(400, {"code": "NoSegment"}), lambda m, u, b: Resp(200, ors_payload()))
    ch = ChainProvider([o, r])
    legs = ch.route_legs(SEQ)
    assert len(r.session.calls) == 0 and all(leg["quality"] == "estimate" for leg in legs)
    assert [e["outcome"] for e in ch.events] == ["no_segment", "estimate"]
    ch.route_legs(SEQ)                                       # next variant: OSRM is NOT marked failed
    assert len(o.session.calls) == 2


def test_chain_deadline_stops_trying():
    o, r = _routers(lambda m, u, q: Resp(200, osrm_by_legs_payload()), lambda m, u, b: Resp(200, {}))
    ch = ChainProvider([o, r], deadline_s=0.0)
    assert all(leg["quality"] == "estimate" for leg in ch.route_legs(SEQ)) and not o.session.calls
    assert ch.events[0]["outcome"] == "deadline"


class _TimedRouter:
    """A duck-typed router that takes ``cost_s`` of (fake) time and then answers or fails."""

    def __init__(self, name, clock, cost_s, fail_kind=None):
        self.name, self.clock, self.cost_s, self.fail_kind = name, clock, cost_s, fail_kind
        self.budgets = []

    def legs(self, coords, budget_s=None):
        self.budgets.append(budget_s)
        self.clock.t += min(self.cost_s, budget_s)
        if self.fail_kind:
            raise RoutingError(self.fail_kind, provider=self.name)
        return [{"geometry": [[a[1], a[0]], [b[1], b[0]]], "duration_min": 1.0, "distance_km": 0.1,
                 "quality": "streets", "provider": self.name} for a, b in zip(coords, coords[1:])]


def test_chain_budget_is_shared_by_all_variants_of_a_request():
    clk = FakeClock(0.0)
    osrm = _TimedRouter("osrm", clk, cost_s=4.0, fail_kind="timeout")
    ors = _TimedRouter("ors", clk, cost_s=1.0)
    ch = ChainProvider([osrm, ors], deadline_s=6.0, clock=clk)
    qualities = []
    for _ in range(3):                                       # variant 1, 2, 3 of ONE request
        clk.t += 5.0                                         # planning CPU between variants is not charged
        qualities.append({leg["provider"] for leg in ch.route_legs(SEQ)})
    assert qualities == [{"ors"}, {"ors"}, {"haversine"}]
    assert osrm.budgets == [6.0]                             # failed once -> skipped for the other variants
    assert ors.budgets == [pytest.approx(2.0), pytest.approx(1.0)]   # every call capped by what is left
    assert ch.spent_s == pytest.approx(6.0)
    assert [e["outcome"] for e in ch.events] == ["timeout", "ok", "skipped", "ok", "skipped", "deadline", "estimate"]


def test_failed_router_not_retried_for_next_variant_in_same_request():
    import requests

    def slow_fail(m, u, q):
        raise requests.exceptions.ReadTimeout("read timeout")
    o, r = _routers(slow_fail, lambda m, u, b: Resp(200, ors_payload()))
    ch = ChainProvider([o, r])                               # one planning request, 3 variants
    for _ in range(3):
        assert {leg["provider"] for leg in ch.route_legs(SEQ)} == {"ors"}
    assert len(o.session.calls) == 2                         # one legs() call (+ its single retry) per request


def test_chain_does_not_trust_a_router_with_the_wrong_leg_count():
    class Short:
        name = "short"

        def legs(self, coords, budget_s=None):
            return [_leg()]
    o = _osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload()))
    ch = ChainProvider([Short(), o])
    legs = ch.route_legs(SEQ)
    assert len(legs) == 4 and {leg["provider"] for leg in legs} == {"osrm"}
    assert ch.events[0]["outcome"] == "mismatch"


def test_chain_survives_a_broken_cache_and_a_crashing_router():
    class BrokenCache(LegCache):
        def get_many(self, keys):
            raise RuntimeError("boom")

    class Crashing:
        name = "crashing"

        def legs(self, coords, budget_s=None):
            raise KeyError("bug")
    o = _osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload()))
    legs = ChainProvider([Crashing(), o], cache=BrokenCache()).route_legs(SEQ)
    assert len(legs) == 4 and {leg["provider"] for leg in legs} == {"osrm"}


def test_chain_walk_minutes_stays_the_estimate_and_route_joins_legs():
    o = _osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload()))
    ch = ChainProvider([o])
    base = RoutingProvider()
    assert ch.walk_minutes(SEQ[0], SEQ[1]) == base.walk_minutes(SEQ[0], SEQ[1])   # the optimizer never routes
    assert not o.session.calls
    path = ch.route(SEQ)
    assert path["quality"] == "streets" and path["provider"] == "osrm"
    assert path["geometry"] == REAL["resp"]["routes"][0]["geometry"]["coordinates"]
    assert path["duration_min"] == pytest.approx(sum(leg["duration"] for leg in REAL_LEGS) / 60.0)
    assert ch.route_legs(SEQ[:1]) == [] and ch.route_legs([]) == []


def test_a_walk_request_with_a_chain_can_be_copied():
    from dataclasses import asdict, replace
    ch = ChainProvider([_osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload()))], cache=LegCache())
    req = WalkRequest(candidates=[], start=SEQ[0], provider=ch)
    assert copy.deepcopy(req).provider is ch and replace(req, shape="loop").provider is ch
    assert asdict(req)["provider"] == {"walk_kmh": ch.walk_kmh, "detour": ch.detour}


def test_chain_legs_touching_the_start_are_cached_briefly():
    rds = FakeRedis()
    o = _osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload(data_version="romania-260930")))
    ch = ChainProvider([o], cache=LegCache(redis_client=rds), start=SEQ[0])
    ch.route_legs(SEQ)
    keys = [LegCache().key(a, b, "romania-260930") for a, b in zip(SEQ, SEQ[1:])]
    assert [rds.ex[k] for k in keys] == [3600, 30 * 86400, 30 * 86400, 3600]


def test_chain_cache_namespace_follows_the_osrm_data_version():
    o = _osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload(data_version="romania-260930")))
    cache = LegCache()
    first = ChainProvider([o], cache=cache)
    assert first.dataset == "unknown"
    first.route_legs(SEQ)
    assert first.dataset == "romania-260930" and first.data_version == "romania-260930"
    second = ChainProvider([o], cache=cache)
    assert all(leg["cached"] for leg in second.route_legs(SEQ)) and len(o.session.calls) == 1


def test_chain_is_thread_safe_with_shared_routers_and_cache():
    o = _osrm(lambda m, u, q: generic_osrm(m, u, q))
    cache = LegCache()

    def one(i):
        pts = [(44.43 + 0.001 * (i % 7), 26.10), (44.44, 26.11 + 0.001 * (i % 5)), (44.45, 26.12)]
        ch = ChainProvider([o], cache=cache)
        return [leg["quality"] for leg in ch.route_legs(pts)]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(one, range(200)))
    assert all(q == ["streets", "streets"] for q in results)
    assert o.breaker.snapshot()["state"] == "closed"
    assert len(o.session.calls) < 200                        # repeated pairs were served by the cache


# --------------------------------------------------------------------------- #
# Integration with the UNCHANGED planner core
# --------------------------------------------------------------------------- #
def test_plan_sequence_accepts_chain_legs():
    o = _osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload()))
    stops = [Candidate(place_id=f"p{i}", name=n, lat=la, lon=lo, dwell_min=20.0)
             for i, (n, (la, lo)) in enumerate(zip(REAL["names"][1:4], SEQ[1:4]))]
    req = WalkRequest(candidates=stops, start=SEQ[0], shape="loop", time_budget_min=180.0,
                      provider=ChainProvider([o]))
    plan = plan_sequence(stops, req)
    assert len(plan.segments) == 4 and [s.from_order for s in plan.segments] == [-1, 0, 1, 2]
    assert plan.total_walk_min == pytest.approx(sum(leg["duration"] for leg in REAL_LEGS) / 60)
    assert plan.segments[1].geometry[0] == [26.098643, 44.433102]    # snapped waypoint, [lon, lat]
    assert {(s.quality, s.provider) for s in plan.segments} == {("streets", "osrm")}
    assert plan.routing_quality == "streets"


def test_plan_routing_quality_mixed_when_only_cached_legs_are_streets():
    o = _osrm(lambda m, u, q: Resp(200, osrm_by_legs_payload(n_legs=2)))
    cache = LegCache()
    ChainProvider([o], cache=cache).route_legs(SEQ[:3])
    o.session.handler = lambda m, u, q: Resp(503, {})
    stops = [Candidate(place_id=f"p{i}", name=f"P{i}", lat=la, lon=lo, dwell_min=20.0)
             for i, (la, lo) in enumerate(SEQ[1:4])]
    plan = plan_sequence(stops, WalkRequest(candidates=stops, start=SEQ[0], shape="loop", time_budget_min=180.0,
                                            provider=ChainProvider([o], cache=cache)))
    assert [s.quality for s in plan.segments] == ["streets", "streets", "estimate", "estimate"]
    assert [s.provider for s in plan.segments] == ["osrm", "osrm", "haversine", "haversine"]
    assert plan.routing_quality == "mixed"


def test_routing_never_changes_which_places_are_planned():
    # the optimizer uses walk_minutes (estimate) only: with street legs the variants visit the SAME places in
    # the SAME order, one router call per variant; only times/geometry differ
    cands = [Candidate(place_id=f"s{k}", name=f"S{k}", lat=44.43 + 0.001 * (k + 1), lon=26.10 + 0.0005 * k,
                       slot=0, interest=0.9 - 0.05 * k, dwell_min=30) for k in range(4)]
    cands += [Candidate(place_id=f"c{k}", name=f"C{k}", lat=44.43 + 0.001 * (k + 1), lon=26.10 - 0.0005 * k,
                        slot=1, interest=0.8 - 0.05 * k, dwell_min=20) for k in range(4)]
    kw = dict(candidates=cands, mode="slots", n_slots=2, start=(44.43, 26.10), shape="loop",
              time_budget_min=240, fill_window=False)
    o = _osrm(lambda m, u, q: generic_osrm(m, u, q))
    routed = plan_variants(WalkRequest(provider=ChainProvider([o]), **kw), n=3)
    estimated = plan_variants(WalkRequest(**kw), n=3)
    assert [[s.place_id for s in p.stops] for p in routed] == [[s.place_id for s in p.stops] for p in estimated]
    assert all(p.routing_quality == "streets" for p in routed)
    assert all(p.routing_quality == "estimate" for p in estimated)
    assert len(o.session.calls) == 3


# --------------------------------------------------------------------------- #
# make_provider / routing_status
# --------------------------------------------------------------------------- #
def test_make_provider_with_nothing_configured_is_the_plain_estimator():
    for env in ({}, {"ORS_BASE_URL": "https://example.org/ors"}, {"WALK_ROUTE_CACHE_REDIS_URL": "redis://r:6379/5"},
                {"ORS_API_KEY": "   ", "WALK_ROUTER_URL": ""}):
        p = make_provider(env)
        assert type(p) is RoutingProvider
    assert routing_status({})["chain"] == ["estimate"] and routing_status({})["configured"] is False


def test_make_provider_builds_the_chain_from_env_with_shared_singletons():
    env = {"WALK_ROUTER_URL": "http://osrm-foot:5000/", "WALK_ROUTER_PROFILE": "foot", "ORS_API_KEY": "secret-key",
           "ORS_MAX_PER_MIN": "7", "ORS_MAX_PER_DAY": "70", "WALK_ROUTING_DEADLINE_S": "2.5",
           "WALK_ROUTER_PROBE_S": "0"}
    p = make_provider(env, start=(44.43, 26.10))
    assert isinstance(p, ChainProvider) and [r.name for r in p.routers] == ["osrm", "ors"]
    osrm, ors = p.routers
    assert osrm.base_url == "http://osrm-foot:5000" and osrm.profile == "foot"
    assert ors.base_url == DEFAULT_ORS_BASE_URL and (ors.limiter.per_minute, ors.limiter.per_day) == (7, 70)
    assert p.deadline_s == 2.5 and p.dataset == "unknown"
    assert p._touches_start((44.43, 26.10), (44.5, 26.2)) and not p._touches_start((44.5, 26.2), (44.6, 26.3))
    q = make_provider(env)
    assert q is not p and q.routers[0] is osrm and q.routers[1] is ors and q.cache is p.cache   # process-wide
    only_ors = make_provider({"ORS_API_KEY": "k2", "ORS_BASE_URL": "https://ors.example/ors/"})
    assert [r.name for r in only_ors.routers] == ["ors"] and only_ors.routers[0].base_url == "https://ors.example/ors"
    assert only_ors.dataset == "ors" and only_ors.deadline_s == 6.0


def test_make_provider_ignores_bad_env_values():
    p = make_provider({"ORS_API_KEY": "k", "ORS_MAX_PER_MIN": "lots", "ORS_MAX_PER_DAY": "inf",
                       "WALK_ROUTING_DEADLINE_S": "-1"})
    assert (p.routers[0].limiter.per_minute, p.routers[0].limiter.per_day) == (35, 1800)
    assert p.deadline_s == 6.0


def test_make_provider_redis_cache_optional(monkeypatch):
    url = "redis://:hunter2@redis:6379/5"
    monkeypatch.setitem(sys.modules, "redis", None)              # package not installed
    p = make_provider({"ORS_API_KEY": "k", "WALK_ROUTE_CACHE_REDIS_URL": url})
    assert p.cache is not None and p.cache.redis is None         # L1 only, no crash
    routing.reset_routing_state()
    made = []
    fake_mod = types.SimpleNamespace(Redis=types.SimpleNamespace(
        from_url=lambda u, **kw: made.append((u, kw)) or FakeRedis()))
    monkeypatch.setitem(sys.modules, "redis", fake_mod)
    p = make_provider({"ORS_API_KEY": "k", "WALK_ROUTE_CACHE_REDIS_URL": url})
    assert isinstance(p.cache.redis, FakeRedis) and made[0][0] == url and made[0][1]["socket_timeout"] <= 0.5
    status = json.dumps(routing_status({"ORS_API_KEY": "k", "WALK_ROUTE_CACHE_REDIS_URL": url}))
    assert "hunter2" not in status and '"redis": "on"' in status


def test_routing_status_reports_breakers_quota_counters_and_no_secrets():
    env = {"WALK_ROUTER_URL": "http://user:pw@osrm-foot:5000", "ORS_API_KEY": "secret-key", "WALK_ROUTER_PROBE_S": "0"}
    p = make_provider(env)
    osrm, ors = p.routers
    osrm.session = FakeSession(lambda m, u, q: Resp(400, {"code": "NoSegment"}))
    ors.session = FakeSession(lambda m, u, b: Resp(200, ors_payload(), headers={"x-ratelimit-remaining": "1999"}))
    assert {leg["quality"] for leg in p.route_legs(SEQ)} == {"estimate"}         # NoSegment: ORS not asked
    osrm.session.handler = lambda m, u, q: Resp(503, {})
    osrm.retries = 0
    assert {leg["provider"] for leg in make_provider(env).route_legs(SEQ)} == {"ors"}
    st = routing_status(env)
    assert st["chain"] == ["osrm", "ors", "estimate"] and st["streets_available"] is True
    assert [r["name"] for r in st["routers"]] == ["osrm", "ors"]
    assert st["routers"][0]["url"] == "http://osrm-foot:5000"                    # credentials stripped
    assert st["routers"][0]["breaker"]["consecutive_failures"] == 1
    assert st["routers"][1]["quota"]["remaining"] == 1999
    assert st["routers"][1]["limiter"]["remaining_minute"] == 34
    c = st["counters"]
    assert (c["osrm.no_segment"], c["osrm.http_5xx"], c["ors.ok"], c["haversine.estimate"]) == (1, 1, 1, 1)
    assert (c["legs.streets"], c["legs.estimate"], c["route_legs.streets"], c["route_legs.estimate"]) == (4, 4, 1, 1)
    text = json.dumps(st)
    assert "secret-key" not in text and "pw@" not in text
    osrm.breaker.failure(cooldown_s=60.0)
    ors.breaker.failure(cooldown_s=60.0)
    assert routing_status(env)["streets_available"] is False          # every street router is open


def test_make_provider_ignores_a_start_that_is_not_a_point():
    env = {"ORS_API_KEY": "k"}
    for start in ("city_center", None, (44.43,), object()):
        p = make_provider(env, start=start)
        assert isinstance(p, ChainProvider) and not p._ephemeral
    import numpy as np
    assert make_provider(env, start=np.array([44.43, 26.10]))._touches_start((44.43, 26.10), (1.0, 2.0))


# --------------------------------------------------------------------------- #
# ORSProvider: the dashboard's old class name
# --------------------------------------------------------------------------- #
def test_ors_provider_is_importable_from_every_old_place():
    assert walk_planner.ORSProvider is ORSProvider is core.ORSProvider
    from walk_planner.core import ORSProvider as from_core
    assert from_core is ORSProvider
    with pytest.raises(AttributeError):
        core.NoSuchName  # noqa: B018


def test_ors_provider_one_request_per_plan_on_the_new_host():
    s = FakeSession(lambda m, u, b: Resp(200, ors_payload()))
    p = ORSProvider(api_key="k", session=s)
    assert p.base_url == DEFAULT_ORS_BASE_URL and p.api_key == "k"
    legs = p.route_legs(SEQ)
    assert len(s.calls) == 1 and s.calls[0]["url"].startswith("https://api.heigit.org/openrouteservice/")
    assert {(leg["quality"], leg["provider"]) for leg in legs} == {("streets", "ors")}


def test_ors_provider_failure_is_one_request_then_all_legs_flagged_estimates():
    s = FakeSession(lambda m, u, b: Resp(403, {"error": "Quota exceeded"}))
    stops = [Candidate(place_id=f"p{i}", name=f"P{i}", lat=la, lon=lo, dwell_min=20.0)
             for i, (la, lo) in enumerate(SEQ[1:4])]
    kw = dict(candidates=stops, start=SEQ[0], shape="loop", time_budget_min=180.0)
    plan = plan_sequence(stops, WalkRequest(provider=ORSProvider(api_key="k", session=s), **kw))
    assert len(s.calls) == 1                                    # the old class made 1 + one per leg
    assert {(g.quality, g.provider) for g in plan.segments} == {("estimate", "haversine")}
    assert plan.routing_quality == "estimate"
    est = plan_sequence(stops, WalkRequest(**kw))
    assert [g.walk_min for g in plan.segments] == [g.walk_min for g in est.segments]
    assert [g.geometry for g in plan.segments] == [g.geometry for g in est.segments]


def test_ors_provider_without_key_makes_no_request():
    s = FakeSession(lambda m, u, b: Resp(200, ors_payload()))
    legs = ORSProvider(api_key="", session=s).route_legs(SEQ)
    assert not s.calls and {leg["quality"] for leg in legs} == {"estimate"}


def test_ors_provider_instances_share_the_process_breaker_and_limiter():
    shared = routing._shared_ors_router("k", DEFAULT_ORS_BASE_URL, routing.DEFAULT_ORS_MAX_PER_MIN,
                                        routing.DEFAULT_ORS_MAX_PER_DAY)
    shared.session = FakeSession(lambda m, u, b: Resp(403, {"error": "Quota exceeded"}))
    ORSProvider(api_key="k").route_legs(SEQ)                    # the dashboard builds a new one per plan
    legs = ORSProvider(api_key="k").route_legs(SEQ)
    assert len(shared.session.calls) == 1 and {leg["quality"] for leg in legs} == {"estimate"}


# --------------------------------------------------------------------------- #
# secrets never in status / repr; Redis without retries
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url, safe", [
    ("http://user:pw@osrm-foot:5000", "http://osrm-foot:5000"),
    ("https://api.heigit.org/openrouteservice?api_key=SECRET#frag", "https://api.heigit.org/openrouteservice"),
    ("redis://:hunter2@redis:6379/5", "redis://redis:6379/5"),
    ("http://u:p@ss@host:1/x?y=1", "http://host:1/x"),               # a password with "@"
    ("http://host/a@b", "http://host/a@b"),                           # "@" in the path is not user-info
    ("http://[::1]:5000/x?k=1", "http://[::1]:5000/x"),
    ("osrm-foot:5000", "osrm-foot:5000"),
    ("", ""),
])
def test_safe_url_drops_credentials_query_and_fragment(url, safe):
    assert routing._safe_url(url) == safe


def test_routing_config_repr_shows_no_secret():
    env = {"WALK_ROUTER_URL": "http://walker:pw-secret-1@osrm:5000", "ORS_API_KEY": "key-secret-2",
           "ORS_BASE_URL": "https://ors.example/ors?api_key=qs-secret-3", "WALK_ROUTE_CACHE_REDIS_URL":
           "redis://:redis-secret-4@redis:6379/5"}
    cfg = routing.RoutingConfig.from_env(env)
    text = repr(cfg) + str(cfg)
    for secret in ("pw-secret-1", "key-secret-2", "qs-secret-3", "redis-secret-4", "?api_key"):
        assert secret not in text, secret
    assert "http://osrm:5000" in text and "ors_api_key='<set>'" in text
    assert "ors_api_key=''" in repr(routing.RoutingConfig.from_env({}))
    assert cfg.ors_api_key == "key-secret-2" and cfg.router_url.endswith("@osrm:5000")    # the values themselves stay


def test_redis_client_never_retries_and_times_out_fast(monkeypatch):
    made = []

    class Retry:
        def __init__(self, backoff, retries):
            self.backoff, self.retries = backoff, retries

    class NoBackoff:
        pass

    fake = types.ModuleType("redis")
    fake.Redis = types.SimpleNamespace(from_url=lambda u, **kw: made.append(kw) or FakeRedis())
    retry_mod, backoff_mod = types.ModuleType("redis.retry"), types.ModuleType("redis.backoff")
    retry_mod.Retry, backoff_mod.NoBackoff = Retry, NoBackoff
    monkeypatch.setitem(sys.modules, "redis", fake)
    monkeypatch.setitem(sys.modules, "redis.retry", retry_mod)
    monkeypatch.setitem(sys.modules, "redis.backoff", backoff_mod)
    assert isinstance(routing._redis_client("redis://r:6379/5"), FakeRedis)
    kw = made[0]
    assert kw["retry"].retries == 0 and isinstance(kw["retry"].backoff, NoBackoff)
    assert kw["retry_on_timeout"] is False and kw["health_check_interval"] == 0
    assert kw["socket_timeout"] <= 0.25 and kw["socket_connect_timeout"] <= 0.25
