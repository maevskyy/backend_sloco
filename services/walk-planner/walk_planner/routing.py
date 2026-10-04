"""Production street routing for the Walk Planner: one request per plan, cached, never silent.

The optimizer never calls a router: it keeps the offline straight-line estimate
(``RoutingProvider.walk_minutes``), so plans (places and their order) never depend on the network. Only
the final assembly asks ``route_legs(coords)`` for the legs of the chosen route, and that goes through a
chain (``ChainProvider``), cheapest first:

  1. per-leg cache (``LegCache``): L1 in-process LRU + optional L2 Redis; every leg cached -> 0 HTTP;
  2. self-hosted OSRM, foot profile (``OSRMRouter``): ONE ``GET /route/v1/foot/...`` for all legs;
  3. ORS cloud on the heigit host (``ORSRouter``): one POST per <= 50 waypoints, behind a local rate
     limiter and a quota-aware circuit breaker;
  4. the straight-line estimate (core ``RoutingProvider.route``); cached street legs are kept.

Every leg says where it came from::

    {"geometry": [[lon, lat], ...], "duration_min": float, "distance_km": float,
     "quality": "streets" | "estimate", "provider": "osrm" | "ors" | "haversine"}

and the core turns that into ``Segment.quality`` / ``provider`` and ``WalkPlan.routing_quality``.
Routers RAISE ``RoutingError(kind)``; only the chain falls back, and it never fans out per leg.

Routers, their breakers, the ORS limiter and the leg cache are process-wide and thread-safe (the service
runs handlers in a threadpool). A ``ChainProvider`` is built per planning request (``make_provider``) and
carries that request's routing time budget (shared by all its variants), the user's start point (legs
touching it are cached for 1 h only) and the routers that already failed in it (not retried for the next
variant). ``routing_status()`` reports the chain for ``/v1/meta``; ``ORSProvider`` is the research
dashboard's old class name, kept as a one-router chain.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional, Sequence

from .core import DEFAULT_DETOUR, DEFAULT_WALK_KMH, RoutingProvider
from .version import ALGORITHM_VERSION

__all__ = [
    "DEFAULT_OSRM_PROFILE", "DEFAULT_ORS_BASE_URL", "DEFAULT_DEADLINE_S", "DEFAULT_ORS_MAX_PER_MIN",
    "DEFAULT_ORS_MAX_PER_DAY", "DEFAULT_PROBE_INTERVAL_S", "OSRM_RADIUS_M", "ORS_MAX_WAYPOINTS",
    "CONFIG_ERROR_COOLDOWN_S", "CACHE_PREFIX", "L1_MAX_ENTRIES", "TTL_OSRM_S", "TTL_ORS_S", "TTL_START_S",
    "DEFAULT_TTL_S", "MIN_ATTEMPT_S", "USER_AGENT",
    "polyline_encode", "polyline_decode", "polyline6_encode", "polyline6_decode",
    "INPUT_ERRORS", "RoutingError", "Breaker", "RateLimiter", "OSRMRouter", "ORSRouter", "LegCache",
    "ChainProvider", "ORSProvider", "RoutingConfig", "make_provider", "routing_status", "reset_routing_state",
]

log = logging.getLogger("walk_planner.routing")

DEFAULT_OSRM_PROFILE = "foot"
DEFAULT_ORS_BASE_URL = "https://api.heigit.org/openrouteservice"   # api.openrouteservice.org is being shut down
DEFAULT_DEADLINE_S = 6.0          # routing time budget of ONE planning request (all its variants together)
DEFAULT_ORS_MAX_PER_MIN = 35      # local guard below the ORS Standard plan (40/min sliding window)
DEFAULT_ORS_MAX_PER_DAY = 1800    # ... and its 2000 Directions/day
DEFAULT_PROBE_INTERVAL_S = 60.0   # OSRM data_version / health probe, at most this often
OSRM_RADIUS_M = 1000.0            # a point farther than this from any foot way -> NoSegment, not an absurd snap
ORS_MAX_WAYPOINTS = 50            # ORS public API limit per directions request
CONFIG_ERROR_COOLDOWN_S = 3600.0  # the URL answers, but not as the ORS API (wrong ORS_BASE_URL): retry hourly

CACHE_PREFIX = "wr:v1"
L1_MAX_ENTRIES = 20000
TTL_OSRM_S = 30 * 86400           # a new OSRM data_version starts a new namespace anyway
TTL_ORS_S = 86400                 # replaced by OSRM legs soon after OSRM recovers
TTL_START_S = 3600                # legs touching the user's start: privacy (estimates are never cached)
DEFAULT_TTL_S = {"osrm": TTL_OSRM_S, "ors": TTL_ORS_S}

MIN_ATTEMPT_S = 0.05              # less routing budget than this left -> don't start another HTTP attempt
USER_AGENT = f"sloco-walk-planner/{ALGORITHM_VERSION}"


# --------------------------------------------------------------------------- #
# Encoded polyline (Google format; precision 6 = what OSRM/Valhalla use)
# --------------------------------------------------------------------------- #
# The format stores (lat, lon) pairs; our geometry is GeoJSON [lon, lat]. In TypeScript,
# @mapbox/polyline ``toGeoJSON(str, 6)`` returns [lon, lat] and ``decode(str, 6)`` [lat, lon].
def _round_half_away(x: float) -> int:
    """Round half away from zero, like Google's reference encoder (not Python's banker's rounding)."""
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


def polyline_encode(lonlat: Iterable, precision: int = 6) -> str:
    """[[lon, lat], ...] -> encoded polyline (lat, lon order inside, as the format requires)."""
    f = 10 ** precision
    out: list[str] = []
    plat = plon = 0
    for p in lonlat:
        ilat, ilon = _round_half_away(float(p[1]) * f), _round_half_away(float(p[0]) * f)
        for d in (ilat - plat, ilon - plon):
            d = ~(d << 1) if d < 0 else (d << 1)
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def polyline_decode(s: str, precision: int = 6) -> list[list[float]]:
    """Encoded polyline -> [[lon, lat], ...] (GeoJSON order, what ``Segment.geometry`` uses).
    Raises ``ValueError`` on a malformed or truncated string."""
    f = float(10 ** precision)
    coords: list[list[float]] = []
    i, n, lat, lon = 0, len(s), 0, 0
    while i < n:
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                if i >= n:
                    raise ValueError("truncated polyline")
                b = ord(s[i]) - 63
                i += 1
                if not 0 <= b < 64:
                    raise ValueError("invalid polyline character")
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else result >> 1)
        lat += vals[0]
        lon += vals[1]
        coords.append([lon / f, lat / f])
    return coords


def polyline6_encode(lonlat: Iterable) -> str:
    """``[[lon, lat], ...]`` -> Google encoded polyline with 6 decimals (compact client format / cache value)."""
    return polyline_encode(lonlat, 6)


def polyline6_decode(s: str) -> list[list[float]]:
    """Google encoded polyline with 6 decimals -> ``[[lon, lat], ...]``."""
    return polyline_decode(s, 6)


# --------------------------------------------------------------------------- #
# Errors, circuit breaker, rate limiter
# --------------------------------------------------------------------------- #
# What a failure says about the router's health: "fail" counts toward its breaker (quota / rate errors
# open it at once for their own cooldown), "ok" = the server answered (the input was the problem), None =
# no information (the call never happened: no budget left, no key, local limiter, breaker already open).
_BREAKER_EFFECT = {
    "timeout": "fail", "connect": "fail", "http_5xx": "fail", "parse": "fail",
    "quota": "fail", "auth": "fail", "rate_limited": "fail", "config": "fail",
    "no_segment": "ok", "no_route": "ok", "bad_request": "ok", "mismatch": "ok",
}
# The input itself cannot be routed (a point far from any way, or disconnected): the next router would
# fail on the same points, and ORS would spend quota on it.
INPUT_ERRORS = frozenset({"no_segment", "no_route"})


class RoutingError(Exception):
    """A router could not produce the legs. Only ``ChainProvider`` falls back; routers never draw lines.

    ``kind``: ``timeout | connect | http_5xx | parse`` (transient / server fault), ``quota`` (ORS 403),
    ``auth`` (ORS 401), ``rate_limited`` (ORS 429 or the local limiter), ``config`` (ORS answered a 4xx
    that is not an ORS error answer — e.g. 404 from a wrong ``ORS_BASE_URL`` or a proxy: a configuration
    error, the breaker opens for ``CONFIG_ERROR_COOLDOWN_S``), ``no_segment | no_route`` (unroutable
    input), ``bad_request | mismatch`` (rejected input / wrong leg count), ``disabled`` (no key),
    ``breaker_open``, ``deadline`` (routing budget used up). ``detail`` never carries the API key or
    coordinates (numbers are scrubbed)."""

    def __init__(self, kind: str, detail: str = "", *, provider: str = "", code: Optional[str] = None,
                 cooldown_s: Optional[float] = None, breaker_effect: Optional[str] = "auto"):
        super().__init__(f"{provider or 'router'}:{kind}" + (f": {detail}" if detail else ""))
        self.kind = kind
        self.detail = detail
        self.provider = provider
        self.code = code                  # the server's own error code (OSRM "NoSegment", ORS 2010, ...)
        self.cooldown_s = cooldown_s      # explicit breaker cooldown (ORS quota / rate limit)
        self.breaker_effect = _BREAKER_EFFECT.get(kind) if breaker_effect == "auto" else breaker_effect


class Breaker:
    """Circuit breaker: opens after ``threshold`` consecutive failures for ``cooldown_s`` (or at once for an
    explicit cooldown, e.g. ORS 403 -> 1 h), then lets ONE probe call through (half-open): its success closes
    the breaker, its failure re-opens it. A probe that never reports back frees the slot after
    ``probe_timeout_s``. Thread-safe; ``clock`` is injectable for tests."""

    def __init__(self, threshold: int = 3, cooldown_s: float = 30.0, probe_timeout_s: float = 30.0,
                 clock=time.monotonic):
        self.threshold, self.cooldown_s, self.probe_timeout_s = int(threshold), float(cooldown_s), float(probe_timeout_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._fails = 0
        self._opened = False                       # tripped, not closed again by a success yet
        self._open_until = 0.0
        self._probe_since: Optional[float] = None  # half-open: when the probe call was let through

    def allow(self) -> bool:
        """May a call go out now? (Closed: yes. Open: no. Half-open: only the first caller, the probe.)"""
        with self._lock:
            if not self._opened:
                return True
            now = self._clock()
            if now < self._open_until:
                return False
            if self._probe_since is not None and now - self._probe_since < self.probe_timeout_s:
                return False                       # the probe is still out
            self._probe_since = now
            return True

    def success(self) -> None:
        with self._lock:
            self._fails, self._opened, self._open_until, self._probe_since = 0, False, 0.0, None

    def failure(self, cooldown_s: Optional[float] = None) -> None:
        with self._lock:
            self._fails += 1
            if cooldown_s is not None or self._fails >= self.threshold or self._probe_since is not None:
                until = self._clock() + (float(cooldown_s) if cooldown_s is not None else self.cooldown_s)
                # never shorten a longer block already in force (a late 429 must not cut a 403 quota hour)
                self._open_until = max(self._open_until, until) if self._opened else until
                self._opened = True
                self._probe_since = None

    def release(self) -> None:
        """The call that ``allow()`` let through did not happen (no budget / no key): free the probe slot."""
        with self._lock:
            self._probe_since = None

    def snapshot(self) -> dict:
        with self._lock:
            now = self._clock()
            state = "closed" if not self._opened else "open" if now < self._open_until else "half_open"
            return {"state": state, "open_for_s": round(self._open_until - now, 1) if state == "open" else 0.0,
                    "consecutive_failures": self._fails}


class RateLimiter:
    """Client-side sliding windows (per minute and per day) kept below the ORS quota, so a burst of
    plans cannot burn the daily Directions allowance. Per process: with several workers, divide the
    limits by the worker count. Thread-safe; ``clock`` is injectable for tests."""

    def __init__(self, per_minute: int = DEFAULT_ORS_MAX_PER_MIN, per_day: int = DEFAULT_ORS_MAX_PER_DAY,
                 clock=time.monotonic):
        self.per_minute, self.per_day = int(per_minute), int(per_day)
        self._clock = clock
        self._minute: deque = deque()
        self._day: deque = deque()
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        for q, span in ((self._minute, 60.0), (self._day, 86400.0)):
            while q and now - q[0] >= span:
                q.popleft()

    def try_acquire(self) -> bool:
        """Take one request slot if both windows have room (False = don't send the request)."""
        with self._lock:
            now = self._clock()
            self._expire(now)
            if len(self._minute) >= self.per_minute or len(self._day) >= self.per_day:
                return False
            self._minute.append(now)
            self._day.append(now)
            return True

    def snapshot(self) -> dict:
        with self._lock:
            self._expire(self._clock())
            return {"per_minute": self.per_minute, "per_day": self.per_day,
                    "remaining_minute": max(0, self.per_minute - len(self._minute)),
                    "remaining_day": max(0, self.per_day - len(self._day))}


# --------------------------------------------------------------------------- #
# HTTP plumbing (``requests`` is imported lazily: the estimate path never needs it)
# --------------------------------------------------------------------------- #
class _ThreadLocalSession:
    """One ``requests.Session`` per thread (a Session is not guaranteed thread-safe), keep-alive reused."""

    def __init__(self):
        self._local = threading.local()

    def _session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            import requests
            s = requests.Session()
            s.headers["User-Agent"] = USER_AGENT
            self._local.session = s
        return s

    def get(self, url, **kwargs):
        return self._session().get(url, **kwargs)

    def post(self, url, **kwargs):
        return self._session().post(url, **kwargs)


_NUMBER = re.compile(r"-?\d+\.\d+")


def _scrub(text) -> str:
    """Server text for logs/events: decimals (coordinates of the user's start!) masked, length capped."""
    return _NUMBER.sub("<num>", str(text))[:200]


def _transport_error(exc: Exception, provider: str) -> RoutingError:
    """Map an exception raised while sending/receiving to ``timeout`` or ``connect`` (message dropped: it
    would carry the request URL, i.e. coordinates)."""
    kind = "connect"
    if isinstance(exc, TimeoutError):
        kind = "timeout"
    else:
        try:
            import requests
            if isinstance(exc, requests.exceptions.Timeout):
                kind = "timeout"
        except Exception:                          # requests missing (or replaced): keep "connect"
            pass
    return RoutingError(kind, type(exc).__name__, provider=provider)


def _ors_config_error(status: int, what: str, provider: str) -> RoutingError:
    """A non-200 answer below 500 that is not the ORS API's own error answer: the base URL points at
    something else (or a proxy answers) — a configuration error, not a bad input. It fails the router
    for ``CONFIG_ERROR_COOLDOWN_S`` instead of costing a request (and the plan's routing budget) on
    every plan."""
    return RoutingError("config", f"HTTP {status}: {what} (check ORS_BASE_URL)", provider=provider,
                        cooldown_s=CONFIG_ERROR_COOLDOWN_S)


def _attempt_timeout(base: tuple, deadline: Optional[float], provider: str) -> tuple:
    """(connect, read) timeouts for one HTTP attempt, capped by the time left before ``deadline``."""
    if deadline is None:
        return base
    left = deadline - time.monotonic()
    if left < MIN_ATTEMPT_S:
        raise RoutingError("deadline", "routing budget used up", provider=provider)
    return (min(base[0], left), min(base[1], left))


def _headers(resp) -> dict:
    h = getattr(resp, "headers", None) or {}
    try:
        return {str(k).lower(): v for k, v in h.items()}
    except Exception:
        return {}


def _float_or_none(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):     # nonsense header value: status must not crash
        return None


def _safe_url(url: str) -> str:
    """A URL for status output and logs: no ``user:password@``, no ``?query`` and no ``#fragment`` (a key
    or token may hide in either, e.g. ``ORS_BASE_URL=https://host/ors?api_key=...``). Scheme, host, port
    and path stay."""
    text = str(url or "").split("#", 1)[0].split("?", 1)[0]
    # credentials: everything up to the LAST "@" of the authority (a password may contain "@")
    return re.sub(r"^((?:[A-Za-z][A-Za-z0-9+.-]*:)?//)?[^/]*@", r"\1", text)


def _street_leg(geom, duration_s, distance_m, provider: str) -> dict:
    """A router leg in the contract shape (seconds -> minutes, metres -> km); raises ValueError on junk."""
    g = [[float(p[0]), float(p[1])] for p in geom]
    if not g:
        raise ValueError("empty leg geometry")
    if len(g) == 1:
        g.append(list(g[0]))                 # a zero-length leg is still a 2-point LineString
    d, m = float(duration_s), float(distance_m)
    if not (math.isfinite(d) and math.isfinite(m)) or d < 0 or m < 0:
        raise ValueError("bad leg duration/distance")
    return {"geometry": g, "duration_min": d / 60.0, "distance_km": m / 1000.0,
            "quality": "streets", "provider": provider}


def _chain_steps(steps: list) -> list:
    """One leg's geometry from its OSRM steps (servers without ``overview=by_legs``): depart/arrive steps
    are ``[p, p]`` and consecutive steps share their joint, so consecutive duplicates are dropped."""
    out: list = []
    for st in steps:
        for p in st["geometry"]["coordinates"]:
            if not out or out[-1] != p:
                out.append(p)
    return out


# --------------------------------------------------------------------------- #
# Routers
# --------------------------------------------------------------------------- #
class _Router:
    """Shared plumbing: the breaker gate and settling the breaker from the outcome of ``_legs``."""

    name = "router"
    breaker: Breaker

    def legs(self, coords: Sequence, budget_s: Optional[float] = None) -> list[dict]:
        """Street legs for ``coords`` = [(lat, lon), ...]: exactly ``len(coords) - 1`` of them, in order,
        or ``RoutingError``. ``budget_s`` caps the wall time of this call (timeouts and retries included)."""
        pts = [(float(c[0]), float(c[1])) for c in coords]
        if len(pts) < 2:
            return []
        if not self.breaker.allow():
            raise RoutingError("breaker_open", "circuit breaker open", provider=self.name)
        deadline = None if budget_s is None else time.monotonic() + max(0.0, float(budget_s))
        try:
            out = self._legs(pts, deadline)
        except RoutingError as e:
            e.provider = e.provider or self.name
            self._settle(e)
            raise
        except Exception as exc:          # a bug or an unexpected payload must not escape as a plan failure
            err = RoutingError("parse", type(exc).__name__, provider=self.name)
            self._settle(err)
            raise err from exc
        self.breaker.success()
        return out

    def _settle(self, e: RoutingError) -> None:
        if e.breaker_effect == "fail":
            self.breaker.failure(e.cooldown_s)
        elif e.breaker_effect == "ok":
            self.breaker.success()
        else:
            self.breaker.release()

    def _legs(self, coords: list, deadline: Optional[float]) -> list[dict]:   # pragma: no cover
        raise NotImplementedError


_OSRM_OLD_SERVER_CODES = frozenset({"InvalidQuery", "InvalidOptions", "InvalidValue"})


class OSRMRouter(_Router):
    """Self-hosted OSRM (``osrm-routed``, foot profile): ALL legs of a plan in ONE call,
    ``GET {base}/route/v1/{profile}/{lon,lat;...}?overview=by_legs&geometries=geojson&steps=false
    &generate_hints=false&radiuses=1000;...``.

    ``overview=by_legs`` (OSRM >= v26.4.0) puts each leg's [lon, lat] geometry on ``routes[0].legs[i]``;
    older servers answer 400 InvalidQuery, then the router re-asks with ``steps=true`` and chains the step
    geometries (same legs, bigger payload) and remembers that. ``radiuses`` turns a stray point into
    ``no_segment`` instead of an absurd snap. Timeouts (0.5 s connect, 2 s read), one retry with 100-200 ms
    jitter on connect/timeout/502-504 (OSRM drops idle keep-alive connections after 5 s); breaker: 3
    consecutive failures -> 30 s open, then one probe. ``data_version`` (the OSM extract the server was built
    from, ``osrm-extract --data_version``) is read from every answer and from ``probe()`` (``/nearest``); it
    namespaces the leg cache."""

    name = "osrm"

    def __init__(self, base_url: str, profile: str = DEFAULT_OSRM_PROFILE, connect_timeout: float = 0.5,
                 read_timeout: float = 2.0, radius_m: Optional[float] = OSRM_RADIUS_M, max_waypoints: int = 500,
                 retries: int = 1, retry_backoff_s: tuple = (0.1, 0.2), session=None,
                 breaker: Optional[Breaker] = None):
        self.base_url = base_url.rstrip("/")
        self.profile = profile or DEFAULT_OSRM_PROFILE
        self.timeout = (float(connect_timeout), float(read_timeout))
        self.radius_m = radius_m
        self.max_waypoints = int(max_waypoints)
        self.retries = max(0, int(retries))
        self.retry_backoff_s = (float(retry_backoff_s[0]), float(retry_backoff_s[1]))
        self.session = session if session is not None else _ThreadLocalSession()
        self.breaker = breaker or Breaker(threshold=3, cooldown_s=30.0)
        self.by_legs = True                       # False once the server turned out older than v26.4.0
        self.data_version: Optional[str] = None
        self.last_probe: Optional[dict] = None
        self._lock = threading.Lock()
        self._last_point: Optional[tuple] = None  # any point it routed: what the next probe asks about
        self._probe_started: Optional[float] = None
        self._probing = False

    def __repr__(self) -> str:
        return f"OSRMRouter({_safe_url(self.base_url)!r}, profile={self.profile!r})"

    @property
    def dataset(self) -> str:
        """Leg-cache namespace: the server's ``data_version`` ("unknown" until the first answer)."""
        return self.data_version or "unknown"

    def _legs(self, coords: list, deadline: Optional[float]) -> list[dict]:
        if len(coords) > self.max_waypoints:
            raise RoutingError("bad_request", f"{len(coords)} waypoints > {self.max_waypoints}", provider=self.name)
        by_legs = self.by_legs
        try:
            j = self._get_route(coords, by_legs, deadline)
        except RoutingError as e:
            if not (by_legs and e.code in _OSRM_OLD_SERVER_CODES):
                raise
            # older than v26.4.0: same legs from steps=true. Remembered only when that works, so a bad
            # input (InvalidValue) cannot switch a modern server to the slower mode.
            j = self._get_route(coords, False, deadline)
            by_legs = False
            if self.by_legs:
                self.by_legs = False
                log.info("OSRM %s rejects overview=by_legs (older than v26.4): using steps=true",
                         _safe_url(self.base_url))
        return self._parse_legs(j, coords, by_legs)

    def _route_url(self, coords: list, by_legs: bool) -> str:
        params = {"overview": "by_legs" if by_legs else "false", "geometries": "geojson",
                  "steps": "false" if by_legs else "true", "generate_hints": "false"}
        if self.radius_m:
            params["radiuses"] = ";".join([f"{float(self.radius_m):g}"] * len(coords))
        path = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)        # OSRM wants lon,lat
        # the query is built by hand so ";" in radiuses stays literal
        return f"{self.base_url}/route/v1/{self.profile}/{path}?" + "&".join(f"{k}={v}" for k, v in params.items())

    def _get_route(self, coords: list, by_legs: bool, deadline: Optional[float]) -> dict:
        url = self._route_url(coords, by_legs)
        for attempt in range(self.retries + 1):
            timeout = _attempt_timeout(self.timeout, deadline, self.name)
            last = attempt == self.retries
            try:
                r = self.session.get(url, timeout=timeout)
            except Exception as exc:
                err = _transport_error(exc, self.name)
                if last or not self._pause_before_retry(deadline):
                    raise err from exc
                continue
            if r.status_code in (502, 503, 504) and not last and self._pause_before_retry(deadline):
                continue
            return self._decode(r)
        raise RoutingError("connect", "no attempt left", provider=self.name)    # pragma: no cover

    def _pause_before_retry(self, deadline: Optional[float]) -> bool:
        lo, hi = self.retry_backoff_s
        pause = random.uniform(lo, hi) if hi > 0 else 0.0
        if deadline is not None and deadline - time.monotonic() < pause + MIN_ATTEMPT_S:
            return False
        if pause > 0:
            time.sleep(pause)
        return True

    def _decode(self, r) -> dict:
        status = r.status_code
        if status >= 500:
            raise RoutingError("http_5xx", f"HTTP {status}", provider=self.name)
        try:
            j = r.json()
        except ValueError as exc:
            raise RoutingError("parse", f"HTTP {status}, not JSON", provider=self.name) from exc
        code = j.get("code") if isinstance(j, dict) else None
        if not isinstance(code, str):
            raise RoutingError("parse", f"HTTP {status}, not an OSRM answer", provider=self.name)
        if status == 200 and code == "Ok":
            return j
        kind = {"NoSegment": "no_segment", "NoRoute": "no_route"}.get(code, "bad_request")
        raise RoutingError(kind, _scrub(f"{code}: {j.get('message', '')}"), provider=self.name, code=code)

    def _parse_legs(self, j: dict, coords: list, by_legs: bool) -> list[dict]:
        try:
            raw = j["routes"][0]["legs"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RoutingError("parse", "no routes[0].legs", provider=self.name) from exc
        if not isinstance(raw, list) or len(raw) != len(coords) - 1:
            n = len(raw) if isinstance(raw, list) else "?"
            raise RoutingError("mismatch", f"{n} legs for {len(coords)} points", provider=self.name)
        try:
            out = [_street_leg(leg["geometry"]["coordinates"] if by_legs else _chain_steps(leg["steps"]),
                               leg["duration"], leg["distance"], self.name) for leg in raw]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RoutingError("parse", f"bad leg ({type(exc).__name__})", provider=self.name) from exc
        dv = j.get("data_version")
        with self._lock:
            if dv:
                self.data_version = str(dv)
            self._last_point = coords[1]          # a stop rather than the user's start (coords[0] usually)
        return out

    def probe(self, timeout: tuple = (0.5, 1.0)) -> Optional[str]:
        """``GET /nearest`` once: health + ``data_version``. Never raises; returns the data_version (None on
        failure). Does not touch the breaker (only real route calls do)."""
        with self._lock:
            lat, lon = self._last_point or (0.0, 0.0)
        url = f"{self.base_url}/nearest/v1/{self.profile}/{lon:.6f},{lat:.6f}?number=1&generate_hints=false"
        t0 = time.monotonic()
        ok, dv, err = False, None, None
        try:
            r = self.session.get(url, timeout=timeout)
            j = r.json()
            ok = r.status_code == 200 and isinstance(j, dict) and j.get("code") == "Ok"
            dv = j.get("data_version") if ok else None
            if not ok:
                err = f"HTTP {r.status_code}"
        except Exception as exc:
            err = type(exc).__name__
        with self._lock:
            if dv:
                self.data_version = str(dv)
            self._probing = False
            self.last_probe = {"ok": ok, "at": _iso(time.time()), "ms": round((time.monotonic() - t0) * 1000.0, 1),
                               "error": err}
        if not ok:
            log.warning("OSRM probe failed (%s): %s", _safe_url(self.base_url), err)
        return str(dv) if dv else (self.data_version if ok else None)

    def maybe_probe_async(self, max_age_s: Optional[float] = DEFAULT_PROBE_INTERVAL_S) -> bool:
        """Start a background ``probe()`` when the last one started more than ``max_age_s`` ago and none is
        running (``max_age_s`` None/0 = never). Returns immediately; True if a probe was started."""
        if not max_age_s or max_age_s <= 0:
            return False
        now = time.monotonic()
        with self._lock:
            if self._probing or (self._probe_started is not None and now - self._probe_started < max_age_s):
                return False
            self._probing, self._probe_started = True, now
        try:
            threading.Thread(target=self.probe, name="walk-osrm-probe", daemon=True).start()
        except RuntimeError:                       # interpreter shutting down / no threads left
            with self._lock:
                self._probing = False
            return False
        return True

    def status(self) -> dict:
        with self._lock:
            return {"name": self.name, "url": _safe_url(self.base_url), "profile": self.profile,
                    "mode": "by_legs" if self.by_legs else "steps", "data_version": self.data_version,
                    "breaker": self.breaker.snapshot(), "last_probe": dict(self.last_probe) if self.last_probe else None}


class ORSRouter(_Router):
    """openrouteservice cloud (fallback): ``POST {base}/v2/directions/foot-walking/geojson`` with the key in
    ``Authorization``; legs are split from ONE answer by ``properties.way_points`` / ``segments``.

    Guardrails for a metered API: at most ``max_waypoints`` (50) per request (longer routes are chunked;
    consecutive chunks share their boundary point), the local ``RateLimiter`` before every request, no
    retries, timeouts (2 s, 8 s); breaker: 403 (daily quota) -> open until ``x-ratelimit-reset`` (1 h if
    absent), 401 (bad key) -> 1 h, 429 -> ``Retry-After`` (60 s), 5xx/timeouts -> 2 in a row open it for 5 min,
    any other non-200 answer below 500 (a 4xx, an unfollowed redirect) whose body is not an ORS error answer
    (``{"error": {"code": <int>, ...}}`` — e.g. a 404 page from a wrong ``ORS_BASE_URL``) -> ``config`` for
    ``CONFIG_ERROR_COOLDOWN_S`` (1 h).
    ORS error 2010 (point not found) / 2009 (route not found) -> ``no_segment`` / ``no_route``; other ORS
    error codes -> ``bad_request`` (the input, not the server)."""

    name = "ors"
    dataset = "ors"                                # cache namespace when ORS is the primary

    def __init__(self, api_key: str, base_url: str = DEFAULT_ORS_BASE_URL, connect_timeout: float = 2.0,
                 read_timeout: float = 8.0, max_waypoints: int = ORS_MAX_WAYPOINTS,
                 limiter: Optional[RateLimiter] = None, session=None, breaker: Optional[Breaker] = None,
                 profile: str = "foot-walking"):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_ORS_BASE_URL).rstrip("/")
        self.profile = profile
        self.timeout = (float(connect_timeout), float(read_timeout))
        self.max_waypoints = max(2, int(max_waypoints))
        self.limiter = limiter or RateLimiter()
        self.session = session if session is not None else _ThreadLocalSession()
        self.breaker = breaker or Breaker(threshold=2, cooldown_s=300.0)
        self._lock = threading.Lock()
        self.quota: dict = {"remaining": None, "limit": None, "reset_at": None}   # from x-ratelimit-* headers

    def __repr__(self) -> str:                     # never show the key
        return f"ORSRouter({_safe_url(self.base_url)!r})"

    def _legs(self, coords: list, deadline: Optional[float]) -> list[dict]:
        if not self.api_key:
            raise RoutingError("disabled", "no ORS_API_KEY", provider=self.name)
        out: list[dict] = []
        step = self.max_waypoints - 1
        for s in range(0, len(coords) - 1, step):          # chunks share their boundary point
            out += self._one(coords[s:s + self.max_waypoints], deadline)
        return out

    def _one(self, coords: list, deadline: Optional[float]) -> list[dict]:
        timeout = _attempt_timeout(self.timeout, deadline, self.name)
        if not self.limiter.try_acquire():
            raise RoutingError("rate_limited", "local limiter", provider=self.name, breaker_effect=None)
        try:
            r = self.session.post(f"{self.base_url}/v2/directions/{self.profile}/geojson",
                                  json={"coordinates": [[lon, lat] for lat, lon in coords]},
                                  headers={"Authorization": self.api_key, "User-Agent": USER_AGENT,
                                           "Accept": "application/geo+json, application/json"},
                                  timeout=timeout)
        except Exception as exc:
            raise _transport_error(exc, self.name) from exc
        status = r.status_code
        reset_at = self._note_quota(r)
        if status == 401:
            raise RoutingError("auth", "HTTP 401 (key rejected)", provider=self.name, cooldown_s=3600.0)
        if status == 403:                          # daily quota (or a key without Directions access)
            cooldown = 3600.0 if reset_at is None else min(86400.0, max(60.0, reset_at - time.time()))
            raise RoutingError("quota", _scrub(f"HTTP 403: {getattr(r, 'text', '')}"), provider=self.name,
                               cooldown_s=cooldown)
        if status == 429:                          # per-minute window
            retry_after = _float_or_none(_headers(r).get("retry-after"))
            raise RoutingError("rate_limited", "HTTP 429", provider=self.name,
                               cooldown_s=min(300.0, max(1.0, retry_after)) if retry_after else 60.0)
        if status >= 500:
            raise RoutingError("http_5xx", f"HTTP {status}", provider=self.name)
        try:
            j = r.json()
        except ValueError as exc:
            if status != 200:                      # an HTML / text error page: not the ORS API
                raise _ors_config_error(status, "not JSON", self.name) from exc
            raise RoutingError("parse", f"HTTP {status}, not JSON", provider=self.name) from exc
        if status != 200:
            err = j.get("error") if isinstance(j, dict) else None
            code = err.get("code") if isinstance(err, dict) else None
            if isinstance(code, bool) or not isinstance(code, int):
                # JSON, but not ORS's {"error": {"code": <int>, "message": ...}} (a gateway's
                # {"message": "no Route matched"}, {"error": "Not Found"} ...): a wrong URL, not a bad input
                raise _ors_config_error(status, "not an ORS error answer", self.name)
            msg = err.get("message", "")
            kind = {2010: "no_segment", 2009: "no_route"}.get(code, "bad_request")
            raise RoutingError(kind, _scrub(f"HTTP {status} {code}: {msg}"), provider=self.name, code=str(code))
        try:
            feat = j["features"][0]
            geom, props = feat["geometry"]["coordinates"], feat["properties"]
            wps, segs = props["way_points"], props["segments"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RoutingError("parse", "no features[0] geometry/way_points/segments", provider=self.name) from exc
        if len(segs) != len(coords) - 1 or len(wps) != len(coords):
            raise RoutingError("mismatch", f"{len(segs)} segments / {len(wps)} way_points for {len(coords)} points",
                               provider=self.name)
        try:
            return [_street_leg(geom[int(wps[i]):int(wps[i + 1]) + 1], segs[i].get("duration", 0.0),
                                segs[i].get("distance", 0.0), self.name) for i in range(len(segs))]
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise RoutingError("parse", f"bad segment ({type(exc).__name__})", provider=self.name) from exc

    def _note_quota(self, r) -> Optional[float]:
        """Remember the ``x-ratelimit-*`` headers of this answer (status / meta); returns its quota reset
        time (epoch seconds) when it has one."""
        h = _headers(r)
        remaining, limit, reset = (_float_or_none(h.get(k)) for k in
                                   ("x-ratelimit-remaining", "x-ratelimit-limit", "x-ratelimit-reset"))
        reset_at = None
        if reset is not None and reset >= 0:   # epoch seconds, or seconds from now on some deployments
            reset_at = reset if reset > 1e9 else time.time() + reset
            if reset_at > time.time() + 400 * 86400:
                reset_at = None                # not a plausible quota window: ignore
        with self._lock:
            if remaining is not None:
                self.quota["remaining"] = int(remaining)
            if limit is not None:
                self.quota["limit"] = int(limit)
            if reset_at is not None:
                self.quota["reset_at"] = reset_at
        return reset_at

    def status(self) -> dict:
        with self._lock:
            quota = dict(self.quota, reset_at=_iso(self.quota.get("reset_at")))
        return {"name": self.name, "url": _safe_url(self.base_url), "key_configured": bool(self.api_key),
                "breaker": self.breaker.snapshot(), "limiter": self.limiter.snapshot(), "quota": quota}


# --------------------------------------------------------------------------- #
# Per-leg cache
# --------------------------------------------------------------------------- #
class LegCache:
    """Street legs keyed per directed pair of points, so duplicate variants and edits that change 1-3 legs
    cost no router call. L1 = in-process LRU (``max_entries``, TTL-aware); L2 = Redis when ``redis_client``
    is given (shared by workers; e.g. ``redis.Redis.from_url(WALK_ROUTE_CACHE_REDIS_URL)``).

    Key ``wr:v1:{dataset}:{sha1("lat1,lon1>lat2,lon2" at 5 decimals)}`` (dataset = OSRM data_version, so a
    rebuilt map starts a fresh namespace). Value ``{"d": seconds, "m": metres, "g": polyline6, "p": provider,
    "x": expires_at}``. TTL: OSRM 30 d, ORS 24 h, legs touching the user's start 1 h; estimates are never
    cached. A cache error is logged and treated as a miss: it never fails a plan (Redis also sits behind
    its own breaker so an outage does not add latency to every request)."""

    def __init__(self, redis_client=None, max_entries: int = L1_MAX_ENTRIES, ttl_s: Optional[Mapping] = None,
                 start_ttl_s: float = TTL_START_S, prefix: str = CACHE_PREFIX, clock=time.time):
        self.redis = redis_client
        self.max_entries = max(1, int(max_entries))
        self.ttl_s = dict(DEFAULT_TTL_S if ttl_s is None else ttl_s)
        self.start_ttl_s = float(start_ttl_s)
        self.prefix = prefix
        self._clock = clock
        self._l1: OrderedDict = OrderedDict()      # key -> (expires_at, value)
        self._lock = threading.Lock()
        self._redis_breaker = Breaker(threshold=3, cooldown_s=30.0)
        self._stats = {"hits_l1": 0, "hits_l2": 0, "misses": 0, "writes": 0, "errors": 0}

    @staticmethod
    def point_key(p) -> str:
        """A point at cache precision (5 decimals, ~1 m)."""
        return f"{float(p[0]):.5f},{float(p[1]):.5f}"

    def key(self, a, b, dataset: str) -> str:
        raw = f"{self.point_key(a)}>{self.point_key(b)}"
        return f"{self.prefix}:{dataset}:{hashlib.sha1(raw.encode('ascii')).hexdigest()}"

    def __len__(self) -> int:
        with self._lock:
            return len(self._l1)

    def _count(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._stats[name] += n

    def _put_l1(self, key: str, value: dict, expires_at: float) -> None:
        """Caller holds the lock."""
        self._l1[key] = (expires_at, value)
        self._l1.move_to_end(key)
        while len(self._l1) > self.max_entries:
            self._l1.popitem(last=False)

    @staticmethod
    def _to_leg(v: dict) -> dict:
        geom = polyline6_decode(v["g"])
        if len(geom) < 2:
            raise ValueError("cached geometry has < 2 points")
        return {"geometry": geom, "duration_min": float(v["d"]) / 60.0, "distance_km": float(v["m"]) / 1000.0,
                "quality": "streets", "provider": str(v["p"]), "cached": True}

    def get_many(self, keys: Sequence[str]) -> list[Optional[dict]]:
        """Legs for ``keys`` (None = miss), L1 first, then one Redis ``MGET`` for the rest."""
        keys = list(keys)
        now = self._clock()
        vals: list = [None] * len(keys)
        with self._lock:
            for i, k in enumerate(keys):
                hit = self._l1.get(k)
                if hit is None:
                    continue
                if hit[0] <= now:
                    del self._l1[k]
                    continue
                self._l1.move_to_end(k)
                vals[i] = hit[1]
        missing = [i for i, v in enumerate(vals) if v is None]
        if missing and self.redis is not None and self._redis_breaker.allow():
            blobs: list = [None] * len(missing)
            try:
                blobs = list(self.redis.mget([keys[i] for i in missing]))
                self._redis_breaker.success()
            except Exception as exc:
                self._redis_breaker.failure()
                self._count("errors")
                log.warning("leg cache: Redis read failed (%s); in-process cache only", type(exc).__name__)
            for i, blob in zip(missing, blobs):
                if not blob:
                    continue
                try:
                    v = json.loads(blob)
                    expires_at = float(v.get("x", now + self.start_ttl_s))
                except Exception:
                    self._count("errors")
                    continue
                if expires_at <= now:
                    continue
                vals[i] = v
                with self._lock:
                    self._put_l1(keys[i], v, expires_at)
        out: list[Optional[dict]] = []
        for i, v in enumerate(vals):
            leg = None
            if v is not None:
                try:
                    leg = self._to_leg(v)
                except Exception:                  # corrupt entry: drop it, count a miss
                    self._count("errors")
                    with self._lock:
                        self._l1.pop(keys[i], None)
            out.append(leg)
        from_l2 = set(missing)
        n_l2 = sum(1 for i, leg in enumerate(out) if leg is not None and i in from_l2)
        n_hit = sum(leg is not None for leg in out)
        with self._lock:
            self._stats["hits_l1"] += n_hit - n_l2
            self._stats["hits_l2"] += n_l2
            self._stats["misses"] += len(out) - n_hit
        return out

    def ttl_for(self, leg: dict, touches_start: bool = False) -> float:
        """Seconds to keep ``leg`` (0 = not cacheable)."""
        if leg.get("quality") != "streets":
            return 0.0                             # estimates: never (we want streets as soon as a router is back)
        ttl = float(self.ttl_s.get(str(leg.get("provider")), TTL_ORS_S))
        return min(ttl, self.start_ttl_s) if touches_start else ttl

    def set_many(self, items: Iterable[tuple]) -> int:
        """Store ``(key, leg, touches_start)`` items; returns how many were written. Only street legs that
        did not come from this cache are stored."""
        now = self._clock()
        rows = []
        for key, leg, touches_start in items:
            if leg.get("cached"):
                continue
            ttl = self.ttl_for(leg, touches_start)
            if ttl <= 0:
                continue
            try:
                v = {"d": float(leg["duration_min"]) * 60.0, "m": float(leg["distance_km"]) * 1000.0,
                     "g": polyline6_encode(leg["geometry"]), "p": str(leg["provider"]), "x": now + ttl}
            except Exception:
                self._count("errors")
                continue
            rows.append((key, v, ttl))
        if not rows:
            return 0
        with self._lock:
            for key, v, _ttl in rows:
                self._put_l1(key, v, v["x"])
            self._stats["writes"] += len(rows)
        if self.redis is not None and self._redis_breaker.allow():
            try:
                pipe = self.redis.pipeline(transaction=False)
                for key, v, ttl in rows:
                    pipe.set(key, json.dumps(v, separators=(",", ":")), ex=max(1, int(ttl)))
                pipe.execute()
                self._redis_breaker.success()
            except Exception as exc:
                self._redis_breaker.failure()
                self._count("errors")
                log.warning("leg cache: Redis write failed (%s)", type(exc).__name__)
        return len(rows)

    def clear(self) -> None:
        """Empty L1 (Redis is left alone: other processes share it)."""
        with self._lock:
            self._l1.clear()

    def status(self) -> dict:
        with self._lock:
            stats = dict(self._stats)
            n = len(self._l1)
        redis = "off" if self.redis is None else ("on" if self._redis_breaker.snapshot()["state"] == "closed"
                                                  else "unavailable")
        return {"l1_entries": n, "l1_max": self.max_entries, "redis": redis, **stats}


# --------------------------------------------------------------------------- #
# Process-wide counters (for /v1/meta and alerting on the estimate share)
# --------------------------------------------------------------------------- #
class _Counters:
    def __init__(self):
        self._lock = threading.Lock()
        self._c: dict = {}

    def incr(self, name: str, n: int = 1) -> None:
        if n:
            with self._lock:
                self._c[name] = self._c.get(name, 0) + n

    def snapshot(self) -> dict:
        with self._lock:
            return dict(sorted(self._c.items()))

    def reset(self) -> None:
        with self._lock:
            self._c.clear()


_COUNTERS = _Counters()


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #
class ChainProvider(RoutingProvider):
    """Drop-in ``RoutingProvider`` for ONE planning request: ``walk_minutes`` (the optimizer) stays the
    straight-line estimate; ``route_legs`` goes cache -> routers in order -> estimate.

    * ``deadline_s`` is the routing time budget of the request, shared by all its variants: the wall time
      spent waiting on routers is summed, every HTTP attempt is capped by what is left, and once it is used
      up the remaining legs are estimated (planning CPU time between variants is not charged).
    * A router that failed in this request is skipped for its remaining variants (its timeout is paid
      once); ``no_segment`` / ``no_route`` stop the chain for that call instead (the next router would fail
      on the same points and ORS would spend quota) without marking the router failed.
    * On fallback, cached street legs are kept and only the rest are estimated (plan quality "mixed").
    * ``events`` lists what happened (one dict per step: provider, outcome, legs, ms, detail) for the
      request log; ``summary()`` condenses it. Routers, breakers, limiter and cache are shared singletons;
      only this object is per request."""

    def __init__(self, routers: Sequence = (), cache: Optional[LegCache] = None,
                 deadline_s: float = DEFAULT_DEADLINE_S, start=None, ephemeral_points: Iterable = (),
                 walk_kmh: float = DEFAULT_WALK_KMH, detour: float = DEFAULT_DETOUR, clock=time.monotonic):
        super().__init__(walk_kmh=walk_kmh, detour=detour)
        self.routers = list(routers)
        self.cache = cache
        self.deadline_s = max(0.0, float(deadline_s))
        points = list(ephemeral_points) + ([start] if start is not None else [])
        self._ephemeral = {LegCache.point_key(p) for p in points}    # legs touching these: short cache TTL
        self._clock = clock
        self._lock = threading.Lock()
        self._spent = 0.0
        self._failed: set = set()                 # id(router) of routers that failed in THIS request
        self._legs_by_quality = {"streets": 0, "estimate": 0}
        self.events: list[dict] = []

    def __deepcopy__(self, memo) -> "ChainProvider":
        # a handle on process-wide routers/cache plus this request's state: copies of a WalkRequest
        # (e.g. copy.deepcopy) must keep routing through the SAME request budget, not crash on its locks
        return self

    # -- introspection -------------------------------------------------------------------------
    @property
    def dataset(self) -> str:
        """Leg-cache namespace: the primary router's dataset (OSRM data_version; "ors" for ORS only)."""
        if not self.routers:
            return "none"
        return str(getattr(self.routers[0], "dataset", None) or "unknown")

    @property
    def data_version(self) -> Optional[str]:
        """The OSRM ``data_version`` once known (``versions.routing`` in API responses), else None."""
        for r in self.routers:
            dv = getattr(r, "data_version", None)
            if dv:
                return str(dv)
        return None

    @property
    def spent_s(self) -> float:
        with self._lock:
            return self._spent

    def summary(self) -> dict:
        """One line for the request log: routing time used, legs by quality, routers skipped as failed."""
        with self._lock:
            failed = sorted({getattr(r, "name", "?") for r in self.routers if id(r) in self._failed})
            return {"chain": [getattr(r, "name", "?") for r in self.routers], "spent_ms": round(self._spent * 1000.0, 1),
                    "legs": dict(self._legs_by_quality), "failed": failed, "events": len(self.events)}

    # -- RoutingProvider ---------------------------------------------------------------------------
    def route(self, coords: list[tuple[float, float]]) -> dict:
        """One path through ``coords`` (joined legs); quality "streets" only if every leg is."""
        legs = self.route_legs(coords)
        if not legs:
            return RoutingProvider.route(self, coords)
        geom = [list(p) for p in legs[0]["geometry"]]
        for leg in legs[1:]:
            g = leg["geometry"]
            geom += [list(p) for p in (g[1:] if g and geom and list(g[0]) == geom[-1] else g)]
        providers = {leg["provider"] for leg in legs}
        return {"geometry": geom, "duration_min": sum(leg["duration_min"] for leg in legs),
                "distance_km": sum(leg["distance_km"] for leg in legs),
                "quality": "streets" if all(leg["quality"] == "streets" for leg in legs) else "estimate",
                "provider": providers.pop() if len(providers) == 1 else "mixed"}

    def route_legs(self, coords: list[tuple[float, float]]) -> list[dict]:
        """Exactly ``len(coords) - 1`` legs for ``coords`` = [(lat, lon), ...]; never raises for routing
        problems (worst case: every leg is a flagged straight-line estimate)."""
        pts = [(float(c[0]), float(c[1])) for c in coords]
        pairs = list(zip(pts, pts[1:]))
        if not pairs:
            return []
        dataset, keys, cached = self._cache_get(pairs)
        n_hit = sum(c is not None for c in cached)
        if n_hit == len(pairs):
            self._event("cache", "hit", legs=n_hit)
            return self._done(cached)
        if n_hit:
            self._event("cache", "partial", legs=len(pairs), hits=n_hit)

        for router in self.routers:
            name = str(getattr(router, "name", type(router).__name__))
            with self._lock:
                failed_before = id(router) in self._failed
                left = self.deadline_s - self._spent
            if failed_before:
                self._event(name, "skipped", detail="failed earlier in this request")
                continue
            if left < MIN_ATTEMPT_S:
                self._event(name, "deadline", detail="routing budget used up")
                break
            t0 = self._clock()
            try:
                legs = router.legs(pts, budget_s=left)
            except RoutingError as e:
                ms = self._charge(t0)
                self._event(name, e.kind, ms=ms, detail=e.detail)
                if e.kind in INPUT_ERRORS:
                    break                          # the next router would fail on the same points
                self._mark_failed(router)
                continue
            except Exception as exc:               # a broken custom router must not fail the plan
                ms = self._charge(t0)
                log.exception("router %s crashed", name)
                self._event(name, "error", ms=ms, detail=type(exc).__name__)
                self._mark_failed(router)
                continue
            ms = self._charge(t0)
            if not isinstance(legs, list) or len(legs) != len(pairs):
                self._event(name, "mismatch", ms=ms, detail=f"{len(legs) if isinstance(legs, list) else '?'} legs "
                                                           f"for {len(pairs)} pairs")
                self._mark_failed(router)
                continue
            self._event(name, "ok", ms=ms, legs=len(legs))
            self._cache_put(dataset, keys, pairs, legs)
            return self._done(legs)

        n_est = len(pairs) - n_hit
        self._event("haversine", "estimate", legs=n_est)
        log.info("routing fell back to the straight-line estimate for %d of %d legs", n_est, len(pairs))
        return self._done([c if c is not None else self._estimate(a, b) for c, (a, b) in zip(cached, pairs)])

    # -- helpers -----------------------------------------------------------------------------------
    def _estimate(self, a, b) -> dict:
        leg = RoutingProvider.route(self, [a, b])
        leg["quality"], leg["provider"] = "estimate", "haversine"
        return leg

    def _touches_start(self, a, b) -> bool:
        return LegCache.point_key(a) in self._ephemeral or LegCache.point_key(b) in self._ephemeral

    def _charge(self, t0: float) -> float:
        dt = max(0.0, self._clock() - t0)
        with self._lock:
            self._spent += dt
        return round(dt * 1000.0, 1)

    def _mark_failed(self, router) -> None:
        with self._lock:
            self._failed.add(id(router))

    def _event(self, provider: str, outcome: str, **kw) -> None:
        ev = {"provider": provider, "outcome": outcome}
        ev.update({k: v for k, v in kw.items() if v not in (None, "")})
        with self._lock:
            self.events.append(ev)
        _COUNTERS.incr(f"{provider}.{outcome}")
        if provider not in ("cache", "haversine") and outcome not in ("ok", "skipped", "deadline", "breaker_open"):
            log.warning("router %s failed: %s %s", provider, outcome, kw.get("detail", ""))

    def _done(self, legs: list[dict]) -> list[dict]:
        n_streets = sum(leg.get("quality") == "streets" for leg in legs)
        with self._lock:
            self._legs_by_quality["streets"] += n_streets
            self._legs_by_quality["estimate"] += len(legs) - n_streets
        _COUNTERS.incr("legs.streets", n_streets)
        _COUNTERS.incr("legs.estimate", len(legs) - n_streets)
        _COUNTERS.incr("route_legs." + ("streets" if n_streets == len(legs) else "estimate" if not n_streets
                                        else "mixed"))
        return legs

    def _cache_get(self, pairs: list) -> tuple:
        if self.cache is None:
            return None, None, [None] * len(pairs)
        try:
            dataset = self.dataset
            keys = [self.cache.key(a, b, dataset) for a, b in pairs]
            cached = self.cache.get_many(keys)
            if len(cached) != len(pairs):
                raise ValueError("cache returned a wrong number of entries")
        except Exception as exc:
            log.warning("leg cache lookup failed (%s); routing without it", type(exc).__name__)
            return None, None, [None] * len(pairs)
        hits = sum(c is not None for c in cached)
        _COUNTERS.incr("cache.hit", hits)
        _COUNTERS.incr("cache.miss", len(pairs) - hits)
        return dataset, keys, cached

    def _cache_put(self, dataset: Optional[str], keys: Optional[list], pairs: list, legs: list) -> None:
        if self.cache is None or keys is None:
            return
        try:
            if self.dataset != dataset:          # the router just told us its data_version: use that namespace
                keys = [self.cache.key(a, b, self.dataset) for a, b in pairs]
            self.cache.set_many([(k, leg, self._touches_start(a, b)) for k, leg, (a, b) in zip(keys, legs, pairs)])
        except Exception as exc:
            log.warning("leg cache write failed (%s)", type(exc).__name__)


class ORSProvider(ChainProvider):
    """The research dashboard's ``ORSProvider(api_key=key)``, kept as a one-router chain: ORS cloud only
    (default host now ``https://api.heigit.org/openrouteservice``; api.openrouteservice.org is being shut
    down), ONE request per plan (chunked at 50 waypoints), the process-wide ORS limiter and breaker for that
    key, and on any failure ALL legs become straight-line estimates flagged ``quality="estimate"`` (the old
    class silently re-requested every leg on its own). No leg cache. ``session`` injects an HTTP session
    (tests); the router is then private to this instance."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_ORS_BASE_URL, walk_kmh: float = DEFAULT_WALK_KMH,
                 detour: float = DEFAULT_DETOUR, deadline_s: float = DEFAULT_DEADLINE_S, session=None):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_ORS_BASE_URL).rstrip("/")
        router = (ORSRouter(self.api_key, self.base_url, session=session) if session is not None
                  else _shared_ors_router(self.api_key, self.base_url, DEFAULT_ORS_MAX_PER_MIN, DEFAULT_ORS_MAX_PER_DAY))
        super().__init__([router], cache=None, deadline_s=deadline_s, walk_kmh=walk_kmh, detour=detour)


# --------------------------------------------------------------------------- #
# Configuration + process-wide singletons
# --------------------------------------------------------------------------- #
_WARNED: set = set()


def _warn_once(key: str, msg: str, *args) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        log.warning(msg, *args)


def _env_number(env: Mapping, name: str, default, cast):
    raw = str(env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = cast(float(raw))
    except (TypeError, ValueError, OverflowError):
        value = None
    if value is None or value < 0 or (isinstance(value, float) and not math.isfinite(value)):
        _warn_once(f"env:{name}:{raw}", "routing: ignoring %s=%r (not a non-negative number); using %s",
                   name, raw, default)
        return default
    return value


@dataclass(frozen=True)
class RoutingConfig:
    """Routing settings from the environment (``RoutingConfig.from_env``). Nothing configured
    (no ``WALK_ROUTER_URL``, no ``ORS_API_KEY``) = the straight-line estimate only.

    ``WALK_ROUTER_URL`` self-hosted OSRM base (e.g. ``http://osrm-foot:5000``) · ``WALK_ROUTER_PROFILE``
    (foot) · ``ORS_API_KEY`` (secret) · ``ORS_BASE_URL`` (heigit host) · ``ORS_MAX_PER_MIN`` / ``ORS_MAX_PER_DAY``
    (35 / 1800, per process) · ``WALK_ROUTE_CACHE_REDIS_URL`` (L2 leg cache; needs the ``redis`` package) ·
    ``WALK_ROUTING_DEADLINE_S`` (6) · ``WALK_ROUTER_PROBE_S`` (60; OSRM data_version probe interval, 0 = off)."""

    router_url: str = field(default="", repr=False)            # may embed user:password@
    router_profile: str = DEFAULT_OSRM_PROFILE
    ors_api_key: str = field(default="", repr=False)
    ors_base_url: str = field(default=DEFAULT_ORS_BASE_URL, repr=False)   # may carry ?api_key=
    ors_max_per_min: int = DEFAULT_ORS_MAX_PER_MIN
    ors_max_per_day: int = DEFAULT_ORS_MAX_PER_DAY
    cache_redis_url: str = field(default="", repr=False)      # may embed a password
    deadline_s: float = DEFAULT_DEADLINE_S
    probe_interval_s: float = DEFAULT_PROBE_INTERVAL_S

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "RoutingConfig":
        env = os.environ if env is None else env

        def text(name: str, default: str = "") -> str:
            return str(env.get(name) or "").strip() or default

        return cls(router_url=text("WALK_ROUTER_URL").rstrip("/"),
                   router_profile=text("WALK_ROUTER_PROFILE", DEFAULT_OSRM_PROFILE),
                   ors_api_key=text("ORS_API_KEY"),
                   ors_base_url=text("ORS_BASE_URL", DEFAULT_ORS_BASE_URL).rstrip("/"),
                   ors_max_per_min=_env_number(env, "ORS_MAX_PER_MIN", DEFAULT_ORS_MAX_PER_MIN, int),
                   ors_max_per_day=_env_number(env, "ORS_MAX_PER_DAY", DEFAULT_ORS_MAX_PER_DAY, int),
                   cache_redis_url=text("WALK_ROUTE_CACHE_REDIS_URL"),
                   deadline_s=_env_number(env, "WALK_ROUTING_DEADLINE_S", DEFAULT_DEADLINE_S, float),
                   probe_interval_s=_env_number(env, "WALK_ROUTER_PROBE_S", DEFAULT_PROBE_INTERVAL_S, float))

    def __repr__(self) -> str:
        # never the key, a password in a URL or a token in a query string (logs, tracebacks, debuggers)
        return (f"RoutingConfig(router_url={_safe_url(self.router_url)!r}, router_profile={self.router_profile!r}, "
                f"ors_api_key={'<set>' if self.ors_api_key else ''!r}, ors_base_url={_safe_url(self.ors_base_url)!r}, "
                f"ors_max_per_min={self.ors_max_per_min!r}, ors_max_per_day={self.ors_max_per_day!r}, "
                f"cache_redis_url={_safe_url(self.cache_redis_url)!r}, deadline_s={self.deadline_s!r}, "
                f"probe_interval_s={self.probe_interval_s!r})")

    @property
    def configured(self) -> bool:
        return bool(self.router_url or self.ors_api_key)

    @property
    def chain(self) -> list[str]:
        return (["osrm"] if self.router_url else []) + (["ors"] if self.ors_api_key else []) + ["estimate"]


_REGISTRY_LOCK = threading.Lock()
_OSRM_ROUTERS: dict = {}
_ORS_ROUTERS: dict = {}
_CACHES: dict = {}


def _shared_osrm_router(base_url: str, profile: str) -> OSRMRouter:
    key = (base_url.rstrip("/"), profile)
    with _REGISTRY_LOCK:
        r = _OSRM_ROUTERS.get(key)
        if r is None:
            if not re.match(r"https?://", base_url):
                _warn_once(f"url:{base_url}", "WALK_ROUTER_URL %r has no http(s):// scheme: every OSRM call will "
                                              "fail (and fall back)", _safe_url(base_url))
            r = _OSRM_ROUTERS[key] = OSRMRouter(base_url, profile=profile)
        return r


def _shared_ors_router(api_key: str, base_url: str, per_min: int, per_day: int) -> ORSRouter:
    key = (api_key, base_url.rstrip("/"), int(per_min), int(per_day))
    with _REGISTRY_LOCK:
        r = _ORS_ROUTERS.get(key)
        if r is None:
            r = _ORS_ROUTERS[key] = ORSRouter(api_key, base_url, limiter=RateLimiter(per_min, per_day))
        return r


def _redis_client(url: str):
    try:
        import redis
    except ImportError:
        _warn_once("redis:missing", "WALK_ROUTE_CACHE_REDIS_URL is set but the 'redis' package is not installed: "
                                    "leg cache is in-process only")
        return None
    # Fail fast, never retry: a leg-cache read must not add latency to a plan. 0.25 s socket timeouts; no
    # retries at all (redis-py >= 6 retries 3x with exponential back-off by default, older versions on
    # timeouts when asked); no health-check round trips. The cache's own breaker then skips Redis for a while.
    try:
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        no_retry: dict = {"retry": Retry(NoBackoff(), 0)}
    except Exception:                                  # a redis-py without these modules: its default is no retry
        no_retry = {}
    try:
        return redis.Redis.from_url(url, socket_timeout=0.25, socket_connect_timeout=0.25, retry_on_timeout=False,
                                    health_check_interval=0, **no_retry)
    except Exception as exc:
        _warn_once("redis:url", "leg cache: unusable WALK_ROUTE_CACHE_REDIS_URL (%s): in-process only",
                   type(exc).__name__)
        return None


def _shared_cache(redis_url: str) -> LegCache:
    with _REGISTRY_LOCK:
        c = _CACHES.get(redis_url)
        if c is None:
            c = _CACHES[redis_url] = LegCache(redis_client=_redis_client(redis_url) if redis_url else None)
        return c


def make_provider(env: Optional[Mapping[str, str]] = None, start=None) -> RoutingProvider:
    """The routing provider for ONE planning request (pass the same object to all its variants and edits).

    ``env`` defaults to ``os.environ`` (see ``RoutingConfig`` for the variables). With nothing configured it
    returns the plain ``RoutingProvider()`` (straight-line estimate, no network). Otherwise a ``ChainProvider``
    over the process-wide routers (OSRM first, then ORS), leg cache, breakers and limiter, with the request's
    routing budget (``WALK_ROUTING_DEADLINE_S``). ``start`` = the user's (lat, lon) start: legs touching it are
    cached for 1 h only. Never touches the network itself (an OSRM data_version probe may start in the
    background, at most every ``WALK_ROUTER_PROBE_S``)."""
    cfg = RoutingConfig.from_env(env)
    if not cfg.configured:
        return RoutingProvider()
    routers: list = []
    if cfg.router_url:
        osrm = _shared_osrm_router(cfg.router_url, cfg.router_profile)
        osrm.maybe_probe_async(cfg.probe_interval_s)
        routers.append(osrm)
    if cfg.ors_api_key:
        routers.append(_shared_ors_router(cfg.ors_api_key, cfg.ors_base_url, cfg.ors_max_per_min, cfg.ors_max_per_day))
    start_pt = None
    if start is not None and not isinstance(start, str):
        try:
            if len(start) == 2:
                start_pt = (float(start[0]), float(start[1]))
        except (TypeError, ValueError):
            start_pt = None
    return ChainProvider(routers, cache=_shared_cache(cfg.cache_redis_url), deadline_s=cfg.deadline_s,
                         start=start_pt)


def routing_status(env: Optional[Mapping[str, str]] = None) -> dict:
    """Routing block for ``GET /v1/meta`` (JSON-ready, no secrets, no network): the configured chain, each
    router's breaker state (+ OSRM mode/data_version/last probe, ORS local limiter and server-reported
    quota), the leg cache, and process-wide counters (``<provider>.<outcome>``, ``legs.streets|estimate``,
    ``route_legs.streets|mixed|estimate``, ``cache.hit|miss``)."""
    cfg = RoutingConfig.from_env(env)
    routers = []
    osrm = _shared_osrm_router(cfg.router_url, cfg.router_profile) if cfg.router_url else None
    if osrm is not None:
        routers.append(osrm.status())
    if cfg.ors_api_key:
        routers.append(_shared_ors_router(cfg.ors_api_key, cfg.ors_base_url, cfg.ors_max_per_min,
                                          cfg.ors_max_per_day).status())
    return {
        "chain": cfg.chain,
        "configured": cfg.configured,
        "streets_available": any(r["breaker"]["state"] != "open" for r in routers),
        "dataset": osrm.data_version if osrm is not None else None,
        "deadline_s": cfg.deadline_s,
        "routers": routers,
        "cache": _shared_cache(cfg.cache_redis_url).status() if cfg.configured else None,
        "counters": _COUNTERS.snapshot(),
    }


def reset_routing_state() -> None:
    """Forget the process-wide routers, caches and counters (tests; a config reload)."""
    with _REGISTRY_LOCK:
        _OSRM_ROUTERS.clear()
        _ORS_ROUTERS.clear()
        _CACHES.clear()
    _COUNTERS.reset()
    _WARNED.clear()
