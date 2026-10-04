"""The walk-planner microservice: FastAPI over the ``walk_planner`` package (internal, no auth, ``/v1``).

Run: ``uvicorn walk_planner.service.app:create_app --factory --host 0.0.0.0 --port 8000`` (deploy/Dockerfile;
``python -m walk_planner serve`` locally). Settings come from the environment (``service/settings.py``).

  GET  /v1/health/live                process is up (always 200)
  GET  /v1/health/ready               200 once every bundle is loaded, verified and warm; 503 not_ready before
  GET  /v1/meta                       identity, versions, git sha, loaded bundles, routing state (no secrets)
  GET  /v1/walks/config               form data for one city: activities, styles, shapes, defaults, limits
  POST /v1/walks/plan                 plan 1..5 route variants          (pipeline.build_plan)
  POST /v1/walks/schedule             re-time an edited order exactly   (pipeline.schedule)
  POST /v1/walks/insert               add a place at its best position  (pipeline.insert_place)
  GET  /v1/walks/places/search        name search in a city's catalog    (CityCatalog.search)
  GET  /v1/walks/places/{place_id}    place screen                       (CityCatalog.place_detail)

Response options -- ``lang`` (ru | en), ``geometry`` (geojson | polyline6), ``debug`` (plan only) -- are query
parameters or body fields (both given and different -> 422); defaults WALK_DEFAULT_LANG / WALK_GEOMETRY_DEFAULT.

How a request is served. The POST handlers make exactly the package calls of the API facade
(``walk_planner.cli.api_plan`` / ``api_schedule`` / ``api_insert``, which produced the golden expected
outputs): ``normalize_params`` / ``from_request_echo`` on the request JSON AS RECEIVED (the pydantic models
check JSON types and formats first, the pipeline owns the domain rules and their error codes), one routing
provider per request (``provider_factory(start)``; default ``routing.make_provider``: OSRM -> ORS ->
straight-line estimate as configured), the city's taste artifacts for personalization (plans AND edits),
then ``present`` builds the JSON, returned as is. Handlers are sync ``def`` (CPU-bound; FastAPI runs them in
its thread pool; parallelism = uvicorn worker processes, ``WEB_CONCURRENCY``).

Start-up (lifespan): load every bundle of ``WALK_BUNDLE_DIR`` (``bundle.load_bundle``: manifest, sha256 of
every file, catalog / taste alignment), warm the catalog caches and the taste model (``interest.warmup``),
plan one smoke walk per city -- then ready. Any failure stops the process (uvicorn exits 3); in a multi-worker
uvicorn (``WEB_CONCURRENCY`` > 1) the failing worker also stops its supervisor, so the container exits and
Docker's restart back-off applies instead of a respawn loop every 0.5 s.

Load guard: plan / schedule / insert hold one of ``WALK_MAX_CONCURRENT_PLANS`` (default 2) slots of this
worker while they run; a request that finds none free is refused at once with 503 ``busy`` + ``Retry-After:
2`` (no queueing: a burst cannot pile up threads, memory and latency behind a few 24-hour plans; the
gateway retries or tells the user). Search, place, config, meta and health are never limited.

Errors: ``{"error": {"code", "message", "params"}}`` with the codes of ``walk_planner.messages.ERRORS``
(PlannerInputError -> its HTTP status) plus request-format ``validation_error`` (422, ``params.errors``),
``not_found`` 404, ``method_not_allowed`` 405, ``payload_too_large`` 413, ``bad_request`` 400, ``busy`` 503 and
``internal_error`` 500 (the request id in ``params``; the stack only in the log). A data problem is never a
500: unknown / closed / moved places and catalog changes are 4xx from the pipeline. Every response carries
``X-Request-Id`` (the caller's if it sent a sane one) and ``X-Process-Time-Ms``; one JSON access line per
request (``service/logging.py``: timings interest / candidates / solver / router / render, routing quality,
stop counts; favourites only as a count + salted digest, coordinates rounded to 2 decimals).
"""
import datetime as _dt
import json
import logging
import math
import multiprocessing
import os
import platform
import re
import signal
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Union

import anyio
from fastapi import APIRouter, Body, Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import interest as _interest
from ..bundle import BundleError, LoadedBundle, load_bundle, resolve_bundle_dirs
from ..core import RoutingProvider
from ..messages import ERRORS, PlannerInputError, render_error
from ..pipeline import build_plan, from_request_echo, insert_place, make_context, normalize_params, schedule
from ..present import config_view, edit_response, plan_response
from ..routing import _safe_url, make_provider, routing_status
from ..version import ALGORITHM_VERSION, API_VERSION, BUNDLE_SCHEMA_VERSION, INTEREST_VERSION
from . import schemas as S
from .logging import (
    ACCESS_LOGGER,
    SERVICE_LOGGER,
    bind_request,
    coarse_point,
    collect_stage_times,
    configure_logging,
    ids_digest,
    instrument_pipeline,
    log_event,
    request_fields,
    uninstrument_pipeline,
)
from .settings import Settings

__all__ = ["SERVICE_NAME", "ProviderFactory", "ServiceState", "StartupError", "WalkJSONResponse", "WorkGuard",
           "create_app"]

SERVICE_NAME = "walk-planner"
ProviderFactory = Callable[[Optional[tuple]], RoutingProvider]
log = logging.getLogger(SERVICE_LOGGER)
access_log = logging.getLogger(ACCESS_LOGGER)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
# Retry-After (seconds) of the 503 answers: still starting / every load-guard slot taken
_RETRY_AFTER_S = {"not_ready": "5", "busy": "2"}
_OPTION_KEYS = ("lang", "geometry", "debug")
# HTTP errors raised by the framework -> the service-level codes of messages.ERRORS
_HTTP_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}


class StartupError(RuntimeError):
    """The service cannot start (no / invalid bundles, two bundles of one city, ...)."""


# --------------------------------------------------------------------------- #
# JSON responses
# --------------------------------------------------------------------------- #
def _json_default(obj: Any) -> Any:
    item = getattr(obj, "item", None)                # numpy scalars
    if callable(item):
        return item()
    raise TypeError(f"{type(obj).__name__} is not JSON serializable")


class WalkJSONResponse(JSONResponse):
    """Compact UTF-8 JSON; NaN / Infinity are refused (strict JSON) rather than sent."""

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
                          default=_json_default).encode("utf-8")


def _service_error_body(code: str, lang: str, params: Optional[dict] = None) -> dict:
    """The error body of a service-level code (messages.ERRORS: bad_request, not_found, ... internal_error)."""
    return {"error": {"code": code, "message": render_error(code, params, lang), "params": dict(params or {})}}


def _planner_error_body(err: PlannerInputError, lang: str) -> dict:
    try:
        message = render_error(err.code, err.params, lang) if err.code in ERRORS else err.code
    except Exception:                               # a template that cannot render these params
        message = err.code
    return {"error": {"code": err.code, "message": message, "params": dict(err.params)}}


# --------------------------------------------------------------------------- #
# Load guard
# --------------------------------------------------------------------------- #
class WorkGuard:
    """At most `limit` CPU-heavy requests (plan / schedule / insert) of this worker process at once.

    ``hold()`` takes a slot without waiting or raises PlannerInputError ``busy`` (503, Retry-After 2): a sync
    handler runs in the thread pool (40 threads) and every running plan competes for the same GIL and adds
    its working set (~0.1-0.7 GB for a 24-hour plan), so a burst that waited would only get slower for
    everyone and could push the worker past the container's memory limit. Rejected callers retry (the
    gateway) -- cheap requests (search, place, config, meta, health) never take a slot. Per process: with
    WEB_CONCURRENCY workers the service runs up to workers x limit of them."""

    def __init__(self, limit: int):
        self.limit = max(1, int(limit))
        self._slots = threading.BoundedSemaphore(self.limit)
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.admitted = 0
        self.rejected = 0

    @contextmanager
    def hold(self) -> Iterator[None]:
        if not self._slots.acquire(blocking=False):
            with self._lock:
                self.rejected += 1
            raise PlannerInputError("busy", {"max_concurrent": self.limit,
                                             "retry_after_s": int(_RETRY_AFTER_S["busy"])})
        with self._lock:
            self.active += 1
            self.admitted += 1
            self.peak = max(self.peak, self.active)
        try:
            yield
        finally:
            with self._lock:
                self.active -= 1
            self._slots.release()

    def status(self) -> dict:
        """Counters of this worker for /v1/meta."""
        with self._lock:
            return {"max_concurrent": self.limit, "active": self.active, "peak": self.peak,
                    "admitted": self.admitted, "rejected_busy": self.rejected}


# --------------------------------------------------------------------------- #
# Service state
# --------------------------------------------------------------------------- #
@dataclass
class ServiceState:
    """Per-app state: settings, the routing provider factory, the loaded bundles (by casefolded city) and
    the worker's load guard."""

    settings: Settings
    provider_factory: ProviderFactory
    bundles: Dict[str, LoadedBundle] = field(default_factory=dict)
    guard: WorkGuard = field(default_factory=lambda: WorkGuard(2))
    phase: str = "starting"                   # starting | ready | failed | stopping
    started_at: float = field(default_factory=time.time)
    startup_ms: Optional[float] = None
    error: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self.phase == "ready"

    @property
    def cities(self) -> List[str]:
        return [b.city for b in self.bundles.values()]

    def bundle_for(self, city: Any) -> LoadedBundle:
        """The bundle of `city` (case-insensitive); the only one when `city` is omitted and there is one.
        422 unknown_city / validation_error otherwise."""
        if city is None or city == "":
            if len(self.bundles) == 1:
                return next(iter(self.bundles.values()))
            raise PlannerInputError("validation_error", {"field": "city", "available": self.cities,
                                                         "reason": "required: this service plans for several cities"})
        if not isinstance(city, str):
            raise PlannerInputError("validation_error", {"field": "city", "reason": "expected a city name"})
        bundle = self.bundles.get(city.casefold())
        if bundle is None:
            raise PlannerInputError("unknown_city", {"city": city, "available": self.cities})
        return bundle


def _default_provider_factory(start: Optional[tuple]) -> RoutingProvider:
    """One routing provider per request from the environment (``routing.make_provider``): the plain
    straight-line estimate when no router is configured -- no network."""
    return make_provider(start=start)


def _state(request: Request, ready: bool = True) -> ServiceState:
    state: ServiceState = request.app.state.walk
    if ready and not state.ready:
        raise PlannerInputError("not_ready", {"state": state.phase})
    return state


@dataclass
class _Options:
    lang: str
    geometry: str
    debug: bool


def _options(state: ServiceState, request: Request, body: Any = None, lang: Optional[str] = None,
             geometry: Optional[str] = None, debug: Optional[bool] = None) -> _Options:
    """Response options from the query (`lang`, `geometry`, `debug`) or the body; both given and different
    -> 422 validation_error."""
    settings = state.settings

    def pick(name: str, query_value: Any, default: Any) -> Any:
        body_value = getattr(body, name, None) if body is not None else None
        if query_value is not None and body_value is not None and query_value != body_value:
            raise PlannerInputError("validation_error", {
                "field": name, "reason": "given as a query parameter and in the body, with different values"})
        return query_value if query_value is not None else (body_value if body_value is not None else default)

    chosen_lang = pick("lang", lang, settings.walk_default_lang)
    request.state.lang = chosen_lang
    return _Options(lang=chosen_lang, geometry=pick("geometry", geometry, settings.walk_geometry_default),
                    debug=bool(pick("debug", debug, False)))


async def _raw_json(request: Request) -> Any:
    """The request body exactly as sent (parsed JSON; FastAPI has already read and cached it) -- what the
    pipeline gets, so its type rules and error codes apply unchanged. None when it is not JSON (then the
    body model has already failed)."""
    try:
        return await request.json()
    except Exception:
        return None


def _pipeline_body(raw: Any) -> Any:
    """The plan request without the response options (the pipeline ignores unknown keys anyway)."""
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items() if k not in _OPTION_KEYS}
    return raw


def _require_dict(raw: Any, what: str = "body") -> dict:
    if not isinstance(raw, dict):
        raise PlannerInputError("validation_error", {"field": what, "reason": "expected a JSON object"})
    return raw


# --------------------------------------------------------------------------- #
# Telemetry fields of the access line
# --------------------------------------------------------------------------- #
def _router_ms(provider: Any) -> float:
    """Wall time this request waited on street routers (ChainProvider); 0 for the straight-line estimate."""
    spent = getattr(provider, "spent_s", None)
    try:
        return round(float(spent) * 1000.0, 1) if spent is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# Routing step outcomes that are routine (cache hits, a partly cached route, a router answering, a router
# skipped after it already failed in this request): no step list in the access line for these alone.
_ROUTINE_OUTCOMES = frozenset({"ok", "hit", "partial", "skipped"})
_EVENT_LOG_MAX = 10
_EVENT_DETAIL_MAX = 200


def _event_log(events: Any) -> Optional[list]:
    """The router steps for the access line -- only when a step failed or legs fell back to the estimate
    (timeouts, HTTP errors, breaker open, budget used up, ``haversine`` estimates); at most 10 steps, each
    ``detail`` cut to 200 characters. None for routine requests."""
    if not isinstance(events, list):
        return None
    steps = [e for e in events if isinstance(e, dict)]
    if not any(e.get("outcome") not in _ROUTINE_OUTCOMES for e in steps):
        return None
    out = []
    for e in steps[:_EVENT_LOG_MAX]:
        e = dict(e)
        if isinstance(e.get("detail"), str) and len(e["detail"]) > _EVENT_DETAIL_MAX:
            e["detail"] = e["detail"][:_EVENT_DETAIL_MAX] + "…"
        out.append(e)
    if len(steps) > _EVENT_LOG_MAX:
        out.append({"truncated": len(steps) - _EVENT_LOG_MAX})
    return out


def _routing_fields(provider: Any) -> dict:
    """The access line's routing block: provider type, chain, time / legs by quality / failed routers
    (``ChainProvider.summary()``), the router steps when something failed (``_event_log``), the OSRM
    data_version."""
    out: dict = {"provider": type(provider).__name__}
    summary = getattr(provider, "summary", None)
    if callable(summary):
        try:
            out.update(summary())
        except Exception:                          # telemetry must never fail a request
            pass
    else:
        out["chain"] = ["estimate"]
    steps = _event_log(getattr(provider, "events", None))
    if steps is not None:
        out["event_log"] = steps
    version = getattr(provider, "data_version", None)
    if version:
        out["data_version"] = str(version)
    return out


def _timings(times, provider: Any, total_stage: str) -> dict:
    router = _router_ms(provider)
    solver = max(0.0, round(times.ms("solver") - router, 1))
    return {"interest_ms": times.ms("interest"), "candidates_ms": times.ms("candidates"), "solver_ms": solver,
            "router_ms": router, "render_ms": times.ms("render"), f"{total_stage}_ms": times.ms(total_stage)}


def _start_kind(raw_start: Any) -> Optional[str]:
    if raw_start is None:
        return None
    if isinstance(raw_start, str):
        return raw_start
    if isinstance(raw_start, dict):
        return "point" if raw_start.get("lat") is not None else "place"
    return "other"


def _personal_fields(favs: Sequence, wtg: Sequence, interest: Any = None) -> dict:
    out = {"favourites": len(favs), "want_to_go": len(wtg), "ids_digest": ids_digest(list(favs) + list(wtg))}
    if interest is not None:
        out.update(mode=interest.mode, used=len(interest.used), ignored=len(interest.ignored),
                   profiles=interest.profiles, strength=interest.strength, error=interest.error)
    return out


def _variant_fields(states: Sequence) -> dict:
    return {"stops": [len(s.plan.stops) for s in states],
            "over_budget": [bool(s.plan.over_budget) for s in states],
            "dropped_slots": [len(s.dropped_slot_indices) for s in states],
            "routing_quality": [str(getattr(s.plan, "routing_quality", "none")) for s in states]}


# --------------------------------------------------------------------------- #
# Middleware: request id, timing header, body limit, access line, last-resort 500
# --------------------------------------------------------------------------- #
class RequestContextMiddleware:
    """Pure ASGI middleware (contextvars-safe): binds the request id + the access-line fields, refuses
    bodies over the limit (413), adds ``X-Request-Id`` / ``X-Process-Time-Ms``, turns an unhandled
    exception into a 500 ``internal_error`` (stack logged, never sent) and writes one access line."""

    def __init__(self, app, max_body_bytes: int = 1_048_576, default_lang: str = "ru"):
        self.app = app
        self.max_body_bytes = int(max_body_bytes)
        self.default_lang = default_lang

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        t0 = time.perf_counter()
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        info = {"status": None, "started": False, "bytes": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                info["started"] = True
                info["status"] = message["status"]
                mh = MutableHeaders(scope=message)
                mh["X-Request-Id"] = request_id
                mh["X-Process-Time-Ms"] = f"{(time.perf_counter() - t0) * 1000.0:.1f}"
            elif message["type"] == "http.response.body":
                info["bytes"] += len(message.get("body", b""))
            await send(message)

        with bind_request(request_id) as fields:
            try:
                ok, receive = await self._guard_body(headers, receive)
                if not ok:
                    await self._send_error(scope, send_wrapper, "payload_too_large",
                                           {"max_bytes": self.max_body_bytes}, self._lang(scope))
                    fields["error"] = "payload_too_large"
                else:
                    await self.app(scope, receive, send_wrapper)
            except Exception as exc:
                log_event(log, "unhandled_exception", logging.ERROR, exc_info=exc,
                          method=scope.get("method"), path=scope.get("path"))
                if info["started"]:
                    raise                           # mid-response: nothing sane to send any more
                fields["error"] = "internal_error"
                await self._send_error(scope, send_wrapper, "internal_error", {"request_id": request_id},
                                       self._lang(scope))
            finally:
                self._access_line(scope, info, fields, t0, request_id)

    def _lang(self, scope) -> str:
        state = scope.get("state") or {}
        lang = state.get("lang") if isinstance(state, dict) else None
        if lang in ("ru", "en"):
            return lang
        query = scope.get("query_string", b"").decode("latin-1")
        m = re.search(r"(?:^|&)lang=(ru|en)(?:&|$)", query)
        return m.group(1) if m else self.default_lang

    async def _guard_body(self, headers: dict, receive):
        """(ok, receive): Content-Length above the limit -> not ok; a chunked body is read up to the limit
        and replayed to the app."""
        length = headers.get("content-length")
        if length is not None:
            try:
                return int(length) <= self.max_body_bytes, receive
            except ValueError:
                return True, receive
        if "chunked" not in headers.get("transfer-encoding", "").lower():
            return True, receive
        chunks: list = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":   # disconnect: let the app see it
                pending = [message]
                break
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_body_bytes:
                return False, receive
            chunks.append(body)
            if not message.get("more_body", False):
                pending = []
                break
        data = b"".join(chunks)
        replayed = {"done": False}

        async def replay():
            if not replayed["done"]:
                replayed["done"] = True
                return {"type": "http.request", "body": data, "more_body": False}
            if pending:
                return pending.pop(0)
            return await receive()

        return True, replay

    @staticmethod
    async def _send_error(scope, send, code: str, params: dict, lang: str) -> None:
        response = WalkJSONResponse(_service_error_body(code, lang, params), status_code=ERRORS[code].http_status)
        await response(scope, _never_receive, send)

    @staticmethod
    def _access_line(scope, info: dict, fields: dict, t0: float, request_id: str) -> None:
        status = info["status"]
        if status is None:                          # nothing was sent: the client went away (cancelled)
            status = 499
            fields.setdefault("error", "client_disconnected")
        path = scope.get("path", "")
        level = logging.WARNING if status >= 500 else (
            logging.DEBUG if path.startswith("/v1/health/") and status == 200 else logging.INFO)
        if not access_log.isEnabledFor(level):
            return
        line = {"event": "request", **fields, "request_id": request_id, "method": scope.get("method"), "path": path,
                "status": status, "duration_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                "bytes_out": info["bytes"]}
        access_log.log(level, "request", extra={"fields": line})


async def _never_receive():  # pragma: no cover  (a JSON response never reads the request)
    return {"type": "http.disconnect"}


# --------------------------------------------------------------------------- #
# Exception handlers
# --------------------------------------------------------------------------- #
async def _request_lang(request: Request) -> str:
    """Language for an error: the handler's choice, else ?lang=, else the body's "lang", else the default."""
    state: ServiceState = request.app.state.walk
    chosen = getattr(request.state, "lang", None)
    if chosen in ("ru", "en"):
        return chosen
    query = request.query_params.get("lang")
    if query in ("ru", "en"):
        return query
    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict) and body.get("lang") in ("ru", "en"):
                return body["lang"]
        except Exception:
            pass
    return state.settings.walk_default_lang


def _problem(err: dict) -> dict:
    """A pydantic error as a JSON-safe ValidationProblem: loc, type, msg, scalar input, scalar ctx."""
    out: dict = {"loc": [x if isinstance(x, int) else str(x) for x in err.get("loc", ())],
                 "type": str(err.get("type", "")), "msg": str(err.get("msg", ""))}
    value = err.get("input")
    if isinstance(value, bool) or isinstance(value, int):
        out["input"] = value
    elif isinstance(value, float):
        out["input"] = value if math.isfinite(value) else str(value)
    elif isinstance(value, str):
        out["input"] = value[:100]
    ctx = err.get("ctx")
    if isinstance(ctx, dict) and ctx:
        safe = {}
        for k, v in ctx.items():
            if v is None or isinstance(v, (bool, int, str)) or (isinstance(v, float) and math.isfinite(v)):
                safe[str(k)] = v
            else:
                safe[str(k)] = str(v)
        out["ctx"] = safe
    return out


async def _on_validation_error(request: Request, exc: RequestValidationError) -> WalkJSONResponse:
    lang = await _request_lang(request)
    problems = [_problem(e) for e in exc.errors()]
    first = problems[0] if problems else {"loc": [], "msg": ""}
    message = render_error("validation_error", {"field": ".".join(str(x) for x in first["loc"]) or None,
                                                "reason": first["msg"] or None}, lang)
    fields = request_fields()
    fields["error"] = "validation_error"
    fields["invalid"] = [".".join(str(x) for x in p["loc"]) for p in problems][:10]
    return WalkJSONResponse({"error": {"code": "validation_error", "message": message,
                                       "params": {"errors": problems}}}, status_code=422)


async def _on_planner_error(request: Request, exc: PlannerInputError) -> WalkJSONResponse:
    lang = await _request_lang(request)
    fields = request_fields()
    fields["error"] = exc.code
    retry = _RETRY_AFTER_S.get(exc.code) or (_RETRY_AFTER_S["not_ready"] if exc.http_status == 503 else None)
    headers = {"Retry-After": retry} if retry else None
    return WalkJSONResponse(_planner_error_body(exc, lang), status_code=exc.http_status, headers=headers)


async def _on_http_error(request: Request, exc: StarletteHTTPException) -> WalkJSONResponse:
    lang = await _request_lang(request)
    code = _HTTP_CODES.get(exc.status_code, "bad_request")
    request_fields()["error"] = code
    params = {"path": request.url.path} if code in ("not_found", "method_not_allowed") else {}
    return WalkJSONResponse(_service_error_body(code, lang, params), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
_RETRY_AFTER_HEADER = {"Retry-After": {"description": "seconds to wait before retrying (not_ready 5, busy 2)",
                                         "schema": {"type": "integer"}}}


def _errors(*statuses: int, busy: bool = False) -> dict:
    """OpenAPI ``responses`` for error statuses (all ErrorBody; examples: `_add_openapi_examples`). `busy`: the
    endpoint takes a load-guard slot, so its 503 may also be ``busy``."""
    text = {400: "Unreadable request (bad_request)", 404: "Unknown place / path",
            409: "Conflict (already in route, catalog changed)", 413: "Body too large (payload_too_large)",
            422: "Invalid request (format: validation_error; domain: its own code)",
            500: "Internal error (internal_error; the request id in params, the details only in the service log)",
            503: ("Not ready yet (not_ready, Retry-After 5) or busy: every load-guard slot of the worker is taken "
                  "(busy, Retry-After 2) -- retry" if busy else "Not ready yet (not_ready, Retry-After 5)")}
    out = {}
    for code in statuses:
        out[code] = {"model": S.ErrorBody, "description": text.get(code, "Error")}
        if code == 503:
            out[code]["headers"] = _RETRY_AFTER_HEADER
    return out


# the error statuses every endpoint group documents (500: any endpoint may fail unexpectedly)
_POST_EDIT_ERRORS = (400, 404, 409, 413, 422, 500, 503)
_POST_PLAN_ERRORS = (400, 404, 413, 422, 500, 503)   # 404: start.place_id not in the city catalog

# error examples per status (S.ERROR_EXAMPLES keys); 503 "busy" only where the endpoint takes a guard slot
_ERROR_EXAMPLE_KEYS = {400: ("bad_request",), 404: ("unknown_place",), 409: ("place_already_in_route",),
                       413: ("payload_too_large",),
                       422: ("validation_error", "duplicate_activity", "place_closed_forever"),
                       500: ("internal_error",), 503: ("not_ready",)}
_GUARDED_PATHS = ("/v1/walks/plan", "/v1/walks/schedule", "/v1/walks/insert")


def _add_openapi_examples(doc: dict) -> None:
    """Put the real examples (schemas.py) into a generated OpenAPI document. Done after generation because
    FastAPI drops every null from what it encodes -- examples included -- and nulls are part of the contract."""
    paths = doc.get("paths", {})
    bodies = {"/v1/walks/plan": S.PLAN_REQUEST_EXAMPLES, "/v1/walks/schedule": S.SCHEDULE_REQUEST_EXAMPLES,
              "/v1/walks/insert": S.INSERT_REQUEST_EXAMPLES}
    for path, examples in bodies.items():
        content = paths.get(path, {}).get("post", {}).get("requestBody", {}).get("content", {})
        if "application/json" in content:
            content["application/json"]["examples"] = json.loads(json.dumps(examples))
    ok = paths.get("/v1/walks/plan", {}).get("post", {}).get("responses", {}).get("200", {}).get("content", {})
    if "application/json" in ok:
        ok["application/json"]["example"] = json.loads(json.dumps(S.PLAN_RESPONSE_EXAMPLE))
    for path, ops in paths.items():
        for op in ops.values():
            for status, response in (op.get("responses") or {}).items():
                keys = _ERROR_EXAMPLE_KEYS.get(int(status)) if str(status).isdigit() else None
                if keys and str(status) == "503" and path in _GUARDED_PATHS:
                    keys = keys + ("busy",)
                content = (response.get("content") or {}).get("application/json")
                if keys and content is not None:
                    content["examples"] = {k: json.loads(json.dumps(S.ERROR_EXAMPLES[k])) for k in keys}


def _lang_q():
    return Query(None, description="text language of messages / errors (default WALK_DEFAULT_LANG)")


def _geometry_q():
    return Query(None, description="segment geometry: geojson | polyline6 (default WALK_GEOMETRY_DEFAULT)")


def _city_q():
    return Query(None, max_length=100, description="city (may be omitted when the service has one)")


router = APIRouter()


@router.get("/v1/health/live", operation_id="health_live", tags=["health"], response_model=S.HealthLive,
            summary="Liveness", responses=_errors(500))
def health_live() -> WalkJSONResponse:
    """The process answers (no dependency is checked)."""
    return WalkJSONResponse({"status": "alive"})


@router.get("/v1/health/ready", operation_id="health_ready", tags=["health"], response_model=S.HealthReady,
            summary="Readiness", responses=_errors(500, 503))
def health_ready(request: Request) -> WalkJSONResponse:
    """200 once every bundle is loaded, verified and warm; 503 ``not_ready`` (Retry-After) while starting or
    stopping. Street routing is not part of readiness (it falls back and says so)."""
    state = _state(request)
    return WalkJSONResponse({"status": "ready", "bundles": [b.bundle_id for b in state.bundles.values()]})


@router.get("/v1/meta", operation_id="get_meta", tags=["meta"], response_model=S.MetaResponse,
            summary="Service identity and state", responses=_errors(500))
def meta(request: Request) -> WalkJSONResponse:
    """Identity, versions, git sha, loaded bundles, routing chain / breakers / counters, this worker's load
    guard. Never secrets: every URL in it is shown without credentials, query string or fragment. Counters,
    the leg cache and the load guard are per worker process (another request may hit another worker)."""
    state = _state(request, ready=False)
    request_fields()["endpoint"] = "meta"
    try:
        routing = routing_status()
    except Exception as exc:                        # a status page must not fail on a routing problem
        routing = {"error": type(exc).__name__}
    started = _dt.datetime.fromtimestamp(state.started_at, tz=_dt.timezone.utc)
    return WalkJSONResponse(_scrub_urls({
        "service": SERVICE_NAME,
        "environment": state.settings.environment,
        "version": ALGORITHM_VERSION,
        "api_version": API_VERSION,
        "algorithms": [{"name": "walk_planner", "version": ALGORITHM_VERSION},
                       {"name": "walk_interest", "version": INTEREST_VERSION}],
        "versions": {"api": API_VERSION, "algorithm": ALGORITHM_VERSION, "interest": INTEREST_VERSION,
                     "bundle_schema": BUNDLE_SCHEMA_VERSION},
        "git_sha": state.settings.walk_git_sha,
        "ready": state.ready,
        "state": state.phase,
        "started_at": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "uptime_s": round(time.time() - state.started_at, 1),
        "startup_ms": state.startup_ms,
        "bundles": [_bundle_summary(b) for b in state.bundles.values()],
        "routing": routing,
        "load": state.guard.status(),
        "runtime": _runtime(),
        "settings": state.settings.public(),
    }))


def _scrub_urls(obj: Any) -> Any:
    """`obj` with every URL-looking string ("scheme://...") reduced to scheme://host[:port]/path: a password,
    token or API key in user-info, the query string or the fragment never reaches /v1/meta."""
    if isinstance(obj, str):
        return _safe_url(obj) if "://" in obj else obj
    if isinstance(obj, dict):
        return {k: _scrub_urls(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub_urls(v) for v in obj]
    return obj


def _bundle_summary(bundle: LoadedBundle) -> dict:
    m = bundle.manifest or {}
    interest = m.get("interest") or None
    return {
        "bundle_id": bundle.bundle_id,
        "city": bundle.city,
        "timezone": bundle.timezone,
        "built_at": m.get("built_at"),
        "schema_version": m.get("schema_version"),
        "rows": m.get("rows"),
        "places": len(bundle.catalog),
        "content_sha256": m.get("content_sha256"),
        "catalog_sha256": (m.get("catalog") or {}).get("catalog_sha256"),
        "coverage": m.get("coverage"),
        "interest": None if not interest else {
            "version": interest.get("version"), "fingerprint": interest.get("fingerprint"),
            "text_model": interest.get("text_model"), "image_model": interest.get("image_model"),
            "places_with_text": interest.get("places_with_text"),
            "places_with_image": interest.get("places_with_image"), "loaded": bundle.taste is not None},
    }


def _runtime() -> dict:
    out = {"python": platform.python_version(), "pid": str(os.getpid()),
           "workers": os.environ.get("WEB_CONCURRENCY")}
    for name in ("numpy", "pandas", "pyarrow", "sklearn", "fastapi", "pydantic", "uvicorn"):
        mod = sys.modules.get(name)
        out[name] = getattr(mod, "__version__", None) if mod is not None else None
    return out


@router.get("/v1/walks/config", operation_id="get_walks_config", tags=["walks"], response_model=S.ConfigResponse,
            summary="Form data of a city", responses=_errors(422, 500, 503))
def walks_config(request: Request, city: Optional[str] = _city_q(),
                 lang: Optional[S.Lang] = _lang_q()) -> WalkJSONResponse:
    """Activities, styles and shapes (codes + labels), visit-length choices, defaults, limits and versions."""
    state = _state(request)
    opts = _options(state, request, lang=lang)
    bundle = state.bundle_for(city)
    view = config_view(bundle.catalog)
    label_key = "label_en" if opts.lang == "en" else "label_ru"
    for group in ("activities", "styles", "shapes"):
        view[group] = [_with_label(item, item[label_key]) for item in view[group]]
    view["lang"] = opts.lang
    view["cities"] = state.cities
    request_fields().update(endpoint="config", city=bundle.city)
    return WalkJSONResponse(view)


def _with_label(item: dict, label: str) -> dict:
    out = {}
    for k, v in item.items():
        out[k] = v
        if k == "label_en":
            out["label"] = label
    if "label" not in out:
        out["label"] = label
    return out


@router.post("/v1/walks/plan", operation_id="plan_walk", tags=["walks"], response_model=S.PlanResponse,
             summary="Plan route variants", responses=_errors(*_POST_PLAN_ERRORS, busy=True))
def walks_plan(request: Request,
               body: S.PlanRequest = Body(),
               raw: Any = Depends(_raw_json),
               lang: Optional[S.Lang] = _lang_q(),
               geometry: Optional[S.Geometry] = _geometry_q(),
               debug: Optional[bool] = Query(None, description="add the 'debug' block (candidates, search area)"),
               ) -> WalkJSONResponse:
    """Plan up to ``variants`` different walks for a time window, activity slots and a style.

    A valid request that finds nothing is still 200: ``status`` no_candidates / no_route, no variants, and
    the request messages say why. Personalised by ``favourite_place_ids`` / ``want_to_go_place_ids``
    (``personalization`` says how). Keep ``request`` and each variant's ``sequence`` for /schedule and
    /insert."""
    state = _state(request)
    with state.guard.hold():                     # 503 busy when every slot is taken
        opts = _options(state, request, body, lang=lang, geometry=geometry, debug=debug)
        raw = _require_dict(raw)
        bundle = state.bundle_for(raw.get("city"))
        catalog = bundle.catalog
        fields = request_fields()
        fields.update(endpoint="plan", city=bundle.city, bundle_id=bundle.bundle_id, lang=opts.lang,
                      geometry=opts.geometry)
        with collect_stage_times() as times:
            params = normalize_params(_pipeline_body(raw), catalog)
            start = make_context(params, catalog).start_resolved
            provider = state.provider_factory(start)
            with times.stage("plan"):
                result = build_plan(params, catalog, provider=provider, taste=bundle.taste)
            with times.stage("render"):
                response = plan_response(result, catalog, lang=opts.lang, photo_base_url=state.settings.photo_base_url,
                                         geometry=opts.geometry, debug=opts.debug)
        fields.update(
            plan_status=result.status, shape=params.shape, style=params.style, window_min=params.window_min,
            slots=params.slot_codes, must_visit=len(params.must_visit_place_ids),
            variants_requested=params.variants, variants=len(result.variants),
            start_kind=_start_kind(raw.get("start")), start=coarse_point(start),
            personalization=_personal_fields(params.favourite_place_ids, params.want_to_go_place_ids, result.interest),
            messages=[m.code for m in result.messages], routing=_routing_fields(provider),
            timings=_timings(times, provider, "plan"), **_variant_fields(result.variants))
        return WalkJSONResponse(response)


def _edit_context(state: ServiceState, raw: dict):
    echo = raw.get("request")
    if not isinstance(echo, dict):
        raise PlannerInputError("validation_error", {"field": "request",
                                                     "reason": "expected the request object of the plan response"})
    bundle = state.bundle_for(echo.get("city"))
    return echo, bundle


@router.post("/v1/walks/schedule", operation_id="schedule_walk", tags=["walks"], response_model=S.EditResponse,
             summary="Re-time an edited route (exact order)", responses=_errors(*_POST_EDIT_ERRORS, busy=True))
def walks_schedule(request: Request,
                   body: S.ScheduleRequest = Body(),
                   raw: Any = Depends(_raw_json),
                   lang: Optional[S.Lang] = _lang_q(),
                   geometry: Optional[S.Geometry] = _geometry_q()) -> WalkJSONResponse:
    """Schedule the stops of ``sequence`` exactly in the given order: reorder, remove, restore, change minutes
    on the client, then send the edited sequence with the ``request`` echo. Nothing is dropped or swapped; a
    stop that may be closed at its new time is kept and flagged; ``over_budget`` when it overruns the window.
    Catalog facts (names, coordinates, hours, status) are re-read by place_id."""
    state = _state(request)
    with state.guard.hold():                     # 503 busy when every slot is taken
        opts = _options(state, request, body, lang=lang, geometry=geometry)
        raw = _require_dict(raw)
        echo, bundle = _edit_context(state, raw)
        catalog = bundle.catalog
        fields = request_fields()
        fields.update(endpoint="schedule", city=bundle.city, bundle_id=bundle.bundle_id, lang=opts.lang,
                      stops_in=len(raw.get("sequence") or []))
        with collect_stage_times() as times:
            ctx = from_request_echo(echo, catalog)
            provider = state.provider_factory(ctx.start_resolved)
            with times.stage("edit"):
                variant = schedule(ctx, raw.get("sequence"), catalog, provider, index=body.variant_index or 0,
                                   taste=bundle.taste)
            with times.stage("render"):
                response = edit_response(ctx, variant, catalog, lang=opts.lang,
                                         photo_base_url=state.settings.photo_base_url, geometry=opts.geometry,
                                         routing_version=_routing_version(provider))
        fields.update(personalization=_personal_fields(ctx.params.favourite_place_ids, ctx.params.want_to_go_place_ids),
                      routing=_routing_fields(provider), timings=_timings(times, provider, "edit"),
                      **_variant_fields([variant]))
        return WalkJSONResponse(response)


@router.post("/v1/walks/insert", operation_id="insert_place", tags=["walks"], response_model=S.InsertResponse,
             summary="Add a place at its best position", responses=_errors(*_POST_EDIT_ERRORS, busy=True))
def walks_insert(request: Request,
                 body: S.InsertRequest = Body(),
                 raw: Any = Depends(_raw_json),
                 lang: Optional[S.Lang] = _lang_q(),
                 geometry: Optional[S.Geometry] = _geometry_q()) -> WalkJSONResponse:
    """Insert ``place_id`` where it costs the least time (every stop open on arrival and no leg over 45 min
    if possible), then re-time. 404 unknown_place, 409 place_already_in_route / catalog_changed, 422
    place_closed_forever, and 422 place_temporarily_closed unless ``allow_temporarily_closed`` (ask the user,
    then resend with true: the stop is kept and flagged)."""
    state = _state(request)
    with state.guard.hold():                     # 503 busy when every slot is taken
        opts = _options(state, request, body, lang=lang, geometry=geometry)
        raw = _require_dict(raw)
        echo, bundle = _edit_context(state, raw)
        catalog = bundle.catalog
        fields = request_fields()
        fields.update(endpoint="insert", city=bundle.city, bundle_id=bundle.bundle_id, lang=opts.lang,
                      stops_in=len(raw.get("sequence") or []))
        with collect_stage_times() as times:
            ctx = from_request_echo(echo, catalog)
            provider = state.provider_factory(ctx.start_resolved)
            with times.stage("edit"):
                variant, position = insert_place(
                    ctx, raw.get("sequence"), raw.get("place_id"), catalog, provider,
                    allow_temporarily_closed=bool(body.allow_temporarily_closed), index=body.variant_index or 0,
                    dwell_min=raw.get("dwell_min"), taste=bundle.taste)
            with times.stage("render"):
                response = edit_response(ctx, variant, catalog, lang=opts.lang,
                                         photo_base_url=state.settings.photo_base_url, geometry=opts.geometry,
                                         inserted_index=position, routing_version=_routing_version(provider))
        fields.update(inserted_index=position,
                      personalization=_personal_fields(ctx.params.favourite_place_ids, ctx.params.want_to_go_place_ids),
                      routing=_routing_fields(provider), timings=_timings(times, provider, "edit"),
                      **_variant_fields([variant]))
        return WalkJSONResponse(response)


def _routing_version(provider: Any) -> Optional[str]:
    version = getattr(provider, "data_version", None)
    return str(version) if version else None


@router.get("/v1/walks/places/search", operation_id="search_places", tags=["places"], response_model=S.SearchResponse,
            summary="Search places by name", responses=_errors(422, 500, 503))
def places_search(request: Request,
                  q: str = Query(..., min_length=1, max_length=200, description="name (or address) text"),
                  city: Optional[str] = _city_q(),
                  lat: Optional[float] = Query(None, ge=-90, le=90, description="rank nearer places first (with lon)"),
                  lon: Optional[float] = Query(None, ge=-180, le=180),
                  limit: int = Query(20, ge=1, le=50),
                  include_closed: bool = Query(False, description="also permanently closed places"),
                  lang: Optional[S.Lang] = _lang_q()) -> WalkJSONResponse:
    """Places whose name matches ``q`` (diacritics- and case-insensitive): exact > prefix > substring > all
    words > address, then by popularity (minus distance when lat / lon are given). Permanently closed places
    only with ``include_closed``; temporarily closed ones are included and say so."""
    state = _state(request)
    _options(state, request, lang=lang)
    if (lat is None) != (lon is None):
        raise PlannerInputError("validation_error", {"field": "lon" if lon is None else "lat",
                                                     "reason": "lat and lon go together"})
    bundle = state.bundle_for(city)
    near = (lat, lon) if lat is not None else None
    results = bundle.catalog.search(q, near=near, limit=limit, include_closed_forever=include_closed,
                                    photo_base_url=state.settings.photo_base_url)
    request_fields().update(endpoint="search", city=bundle.city, q_len=len(q), near=near is not None, limit=limit,
                            include_closed=include_closed, results=len(results))
    return WalkJSONResponse({"city": bundle.city, "query": q, "count": len(results), "results": results,
                             "catalog_version": bundle.catalog.version})


@router.get("/v1/walks/places/{place_id}", operation_id="get_place", tags=["places"], response_model=S.PlaceDetail,
            summary="Place screen", responses=_errors(404, 422, 500, 503))
def place_detail(request: Request,
                 place_id: str = Path(..., pattern=S.PLACE_ID_PATTERN, description="Google CID (decimal string)"),
                 city: Optional[str] = _city_q(),
                 lang: Optional[S.Lang] = _lang_q()) -> WalkJSONResponse:
    """The place card with up to 10 photos, the catalog's text sections, tags and the week's opening hours.
    Without ``city`` every served city is searched."""
    state = _state(request)
    _options(state, request, lang=lang)
    if city is not None:
        candidates = [state.bundle_for(city)]
    else:
        candidates = list(state.bundles.values())
    bundle = next((b for b in candidates if b.catalog.has(place_id)), None)
    request_fields().update(endpoint="place", found=bundle is not None)
    if bundle is None:
        raise PlannerInputError("unknown_place", {"place_id": place_id, "field": "place_id"})
    return WalkJSONResponse(bundle.catalog.place_detail(place_id, photo_base_url=state.settings.photo_base_url))


# --------------------------------------------------------------------------- #
# Start-up
# --------------------------------------------------------------------------- #
def _load_bundles(state: ServiceState, injected: Optional[List[LoadedBundle]]) -> None:
    """Load (or take) the bundles, check one per city, warm caches + taste model, smoke-plan; then publish."""
    t_all = time.perf_counter()
    settings = state.settings
    if injected is None:
        if not settings.walk_bundle_dir:
            raise StartupError("WALK_BUNDLE_DIR is not set: the path of a data bundle, of several (a,b), or of a "
                               "directory of bundles")
        bundles = []
        for directory in resolve_bundle_dirs(settings.walk_bundle_dir):
            t = time.perf_counter()
            bundle = load_bundle(directory, verify=settings.walk_verify_bundle)
            log_event(log, "bundle_loaded", bundle_id=bundle.bundle_id, city=bundle.city, places=len(bundle.catalog),
                      taste=bundle.taste is not None, verified=settings.walk_verify_bundle,
                      ms=round((time.perf_counter() - t) * 1000.0, 1))
            bundles.append(bundle)
    else:
        bundles = list(injected)
    if not bundles:
        raise StartupError("no data bundle to serve")
    by_city: Dict[str, LoadedBundle] = {}
    for bundle in bundles:
        key = str(bundle.city).casefold()
        if key in by_city:
            raise StartupError(f"two bundles for {bundle.city}: {by_city[key].bundle_id} and {bundle.bundle_id}")
        by_city[key] = bundle
    for bundle in bundles:
        t = time.perf_counter()
        bundle.catalog.warm()
        _interest.warmup(bundle.taste)
        warm_ms = round((time.perf_counter() - t) * 1000.0, 1)
        smoke = _smoke_plan(bundle) if settings.walk_startup_smoke_plan else None
        log_event(log, "bundle_ready", bundle_id=bundle.bundle_id, city=bundle.city, warm_ms=warm_ms, smoke_plan=smoke)
    state.bundles = by_city
    state.startup_ms = round((time.perf_counter() - t_all) * 1000.0, 1)


def _smoke_plan(bundle: LoadedBundle) -> dict:
    """Plan (and render) one default walk from the city centre, straight-line routing, no network: a bundle
    the planner cannot plan with fails the start-up instead of the first request (an empty result does not)."""
    t = time.perf_counter()
    params = normalize_params({"city": bundle.city, "date": _dt.date.today().isoformat(), "start": "city_center"},
                              bundle.catalog)
    result = build_plan(params, bundle.catalog, provider=RoutingProvider(), taste=None)
    plan_response(result, bundle.catalog, lang="ru")
    out = {"status": result.status, "variants": len(result.variants),
           "ms": round((time.perf_counter() - t) * 1000.0, 1)}
    if result.status != "ok":
        log_event(log, "smoke_plan_empty", logging.WARNING, bundle_id=bundle.bundle_id, **out)
    return out


def _uvicorn_supervisor_pid() -> Optional[int]:
    """PID of the uvicorn multi-worker supervisor (``uvicorn.supervisors.multiprocess``) that spawned this
    process, or None (single process, --reload, gunicorn, tests ...): this process must be its spawned child
    and the supervisor's worker entry point must be on the current call stack."""
    try:
        parent = multiprocessing.parent_process()
    except Exception:  # pragma: no cover
        return None
    if parent is None or parent.pid is None or parent.pid != os.getppid():
        return None
    frame = sys._getframe()
    while frame is not None:
        if frame.f_code.co_name == "target" and frame.f_globals.get("__name__") == "uvicorn.supervisors.multiprocess":
            return parent.pid
        frame = frame.f_back
    return None


def _stop_supervisor() -> None:
    """After a fatal start-up error in a uvicorn worker: ask the supervisor to shut down (SIGTERM), so the
    process / container ends instead of re-spawning a doomed worker every 0.5 s."""
    pid = _uvicorn_supervisor_pid()
    if pid is None:
        return
    log_event(log, "stopping_supervisor", logging.CRITICAL, supervisor_pid=pid,
              reason="fatal start-up error in a worker")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:  # pragma: no cover
        pass


def _as_bundle_list(bundles: Union[None, LoadedBundle, Sequence[LoadedBundle]]) -> Optional[List[LoadedBundle]]:
    if bundles is None:
        return None
    if isinstance(bundles, LoadedBundle):
        return [bundles]
    return list(bundles)


# --------------------------------------------------------------------------- #
# The app
# --------------------------------------------------------------------------- #
_DESCRIPTION = """Walking-route planner of SLOCO (internal service, no auth; the app gateway calls it).

A walk = a city-local time window + ordered activity slots + a style + a start. `POST /v1/walks/plan` returns up
to 5 route variants; each carries its stops, timed segments, navigation links and an editable `sequence`.
Editing is stateless: send the plan's `request` echo and an edited `sequence` back to `/v1/walks/schedule`
(exact order) or `/v1/walks/insert` (one more place at its best position).

* Place ids are Google CIDs as decimal **strings** (they exceed 2^53: never parse them as numbers).
* Times are minutes from the departure plus the city-local clock (`request.timezone`).
* Messages and errors carry a stable `code` + `params`, and a `text` / `message` in `lang` (ru | en).
* Routing quality is always explicit: segments say `streets` or `estimate` (straight line), variants
  `summary.routing`; the planner's choices never depend on the router.
* Errors: `{"error": {"code", "message", "params"}}`; every response has `X-Request-Id` and `X-Process-Time-Ms`.
"""
_TAGS = [
    {"name": "walks", "description": "Plan and edit walks."},
    {"name": "places", "description": "Catalog lookups (search, place screen)."},
    {"name": "meta", "description": "Service identity and state."},
    {"name": "health", "description": "Liveness / readiness probes."},
]


def create_app(settings: Optional[Settings] = None,
               bundles: Union[None, LoadedBundle, Sequence[LoadedBundle]] = None,
               provider_factory: Optional[ProviderFactory] = None,
               *, setup_logging: bool = True) -> FastAPI:
    """The FastAPI app (``uvicorn walk_planner.service.app:create_app --factory``).

    `settings`: default ``Settings()`` from the environment. `bundles`: loaded bundles to serve (tests; default:
    load ``WALK_BUNDLE_DIR`` at start-up). `provider_factory(start)`: the routing provider of one request
    (default ``routing.make_provider``; tests pass a stub -- no network). `setup_logging`: install the JSON
    log handler (off in tests that capture logs themselves)."""
    try:
        settings = settings if settings is not None else Settings()
    except Exception as exc:
        configure_logging("INFO")
        log_event(log, "invalid_settings", logging.CRITICAL, error=str(exc)[:2000])
        _stop_supervisor()
        raise
    if setup_logging:
        configure_logging(settings.log_level)
    state = ServiceState(settings=settings, provider_factory=provider_factory or _default_provider_factory,
                         guard=WorkGuard(settings.walk_max_concurrent_plans))
    injected = _as_bundle_list(bundles)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.phase = "starting"
        state.started_at = time.time()
        instrument_pipeline()
        try:
            await anyio.to_thread.run_sync(_load_bundles, state, injected)
        except Exception as exc:
            state.phase = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
            expected = isinstance(exc, (StartupError, BundleError))
            log_event(log, "startup_failed", logging.CRITICAL, exc_info=None if expected else exc,
                      error=state.error[:2000])
            uninstrument_pipeline()
            _stop_supervisor()
            raise
        state.phase = "ready"
        log_event(log, "ready", bundles=[b.bundle_id for b in state.bundles.values()], cities=state.cities,
                  startup_ms=state.startup_ms, version=ALGORITHM_VERSION, git_sha=settings.walk_git_sha,
                  environment=settings.environment)
        try:
            yield
        finally:
            state.phase = "stopping"
            uninstrument_pipeline()
            log_event(log, "stopping")

    app = FastAPI(title="SLOCO Walk Planner", version=ALGORITHM_VERSION, description=_DESCRIPTION,
                  openapi_tags=_TAGS, lifespan=lifespan, redoc_url=None)
    app.state.walk = state
    app.add_middleware(RequestContextMiddleware, max_body_bytes=settings.walk_max_body_bytes,
                       default_lang=settings.walk_default_lang)
    app.add_exception_handler(RequestValidationError, _on_validation_error)
    app.add_exception_handler(PlannerInputError, _on_planner_error)
    app.add_exception_handler(StarletteHTTPException, _on_http_error)
    app.include_router(router)
    generate = app.openapi

    def openapi() -> dict:
        if app.openapi_schema is None:
            _add_openapi_examples(generate())     # FastAPI caches the generated dict in app.openapi_schema
        return app.openapi_schema

    app.openapi = openapi
    return app
