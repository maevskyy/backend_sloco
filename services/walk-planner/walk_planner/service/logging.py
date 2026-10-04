"""JSON logging and request telemetry of the walk-planner service.

* ``configure_logging(level)``: one JSON object per line on stdout for every logger (the service, the
  package -- e.g. ``walk_planner.routing`` fallbacks -- and uvicorn's own ``uvicorn`` / ``uvicorn.error``).
  uvicorn's access logger is muted: it would print client addresses and full query strings (a place search
  carries the user's coordinates); the service writes its own access line per request instead.
* Request context: ``REQUEST_ID`` (every line logged while a request is handled carries ``request_id``, also
  from the worker thread of a sync handler -- contextvars are copied into it) and ``request_fields()``, the
  dict a handler fills with what the access line should say (endpoint, city, timings, plan summary ...).
* Privacy helpers -- what the logs may say about a user: ``coarse_point`` (coordinates rounded to 2
  decimals, ~1 km) and ``ids_digest`` (favourite / want-to-go ids as a salted 12-hex digest: equal sets
  log equally within one process, the ids themselves never appear and cannot be guessed back).
* Stage timings: ``collect_stage_times()`` + ``instrument_pipeline()``. The pipeline has no timing hooks of
  its own, so the service wraps the stage functions ``walk_planner.pipeline`` calls by module-global name
  (``resolve_interest``; ``slot_candidates`` / ``extra_candidates`` / ``distances_from`` /
  ``place_candidate``; ``plan_variants`` / ``plan_sequence`` / ``best_insertion``) with timers that add
  to the CURRENT request's ``StageTimes`` (a context variable; a call outside a timed request passes
  straight through). Transparent (same arguments, same result, no extra work -- one wrapper call per
  stage, never inside the solver's loops), reference-counted (installed while at least one app is
  running, restored on shutdown) and OpenTelemetry-style. If the pipeline grows native timings, drop this.
"""
from __future__ import annotations

import contextvars
import datetime as _dt
import functools
import hashlib
import json
import logging
import math
import secrets
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Optional

__all__ = [
    "SERVICE_LOGGER", "ACCESS_LOGGER", "REQUEST_ID", "JsonFormatter", "configure_logging", "request_fields",
    "bind_request", "log_event", "coarse_point", "ids_digest", "StageTimes", "collect_stage_times",
    "current_stage_times", "PIPELINE_STAGES", "instrument_pipeline", "uninstrument_pipeline",
]

SERVICE_LOGGER = "walk_planner.service"
ACCESS_LOGGER = "walk_planner.service.access"
_HANDLER_MARK = "_walk_planner_json"

REQUEST_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("walk_request_id", default=None)
_FIELDS: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("walk_request_fields", default=None)


# --------------------------------------------------------------------------- #
# JSON formatting
# --------------------------------------------------------------------------- #
def _finite(obj: Any) -> Any:
    """`obj` with non-finite floats replaced by their text (strict JSON has no NaN / Infinity)."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def _default(obj: Any) -> Any:
    """JSON fallback for log values: numpy scalars -> Python, sets / tuples -> lists, the rest -> str."""
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    return str(obj)


def _dumps(obj: dict) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=_default, allow_nan=False)
    except ValueError:                              # a NaN / Infinity somewhere
        return json.dumps(_finite(obj), ensure_ascii=False, default=_default, allow_nan=False)


class JsonFormatter(logging.Formatter):
    """A log record as one JSON line: ``ts`` (UTC, ms), ``level``, ``logger``, ``msg``, ``pid``,
    ``request_id`` (when inside a request), the structured ``fields`` passed as ``extra={"fields": {...}}``
    (merged at top level), and ``exc_type`` / ``exc_message`` / ``stack`` for exceptions. Never raises."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            ts = _dt.datetime.fromtimestamp(record.created, tz=_dt.timezone.utc)
            out: dict[str, Any] = {
                "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "pid": record.process,
            }
            rid = getattr(record, "request_id", None) or REQUEST_ID.get()
            if rid:
                out["request_id"] = rid
            fields = getattr(record, "fields", None)
            if isinstance(fields, dict):
                for k, v in fields.items():
                    if k not in ("ts", "level", "logger", "msg", "pid"):
                        out[str(k)] = v
            if record.exc_info and record.exc_info[0] is not None:
                out["exc_type"] = record.exc_info[0].__name__
                out["exc_message"] = str(record.exc_info[1])[:2000]
                out["stack"] = self.formatException(record.exc_info)
            elif record.stack_info:
                out["stack"] = self.formatStack(record.stack_info)
            return _dumps(out)
        except Exception as exc:  # pragma: no cover  (a logging failure must never break a request)
            return json.dumps({"level": "ERROR", "logger": "walk_planner.service.logging",
                               "msg": f"unloggable record ({type(exc).__name__})"})


def configure_logging(level: Any = "INFO", stream=None) -> logging.Handler:
    """Route every logger to ONE JSON handler on stdout (idempotent: replaces the handler an earlier call
    installed, leaves other handlers -- e.g. pytest's -- alone). uvicorn's ``uvicorn`` / ``uvicorn.error``
    loggers propagate to it; ``uvicorn.access`` is muted (see the module docstring). Returns the handler."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _HANDLER_MARK, False):
            root.removeHandler(h)
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            lg.removeHandler(h)
        lg.propagate = True
        lg.setLevel(logging.NOTSET)
    access = logging.getLogger("uvicorn.access")
    for h in list(access.handlers):
        access.removeHandler(h)
    access.propagate = False
    access.setLevel(logging.CRITICAL + 1)
    return handler


# --------------------------------------------------------------------------- #
# Request context
# --------------------------------------------------------------------------- #
@contextmanager
def bind_request(request_id: str, fields: Optional[dict] = None) -> Iterator[dict]:
    """Bind a request id and its access-line fields to the current context (the middleware does this)."""
    fields = {} if fields is None else fields
    t1 = REQUEST_ID.set(request_id)
    t2 = _FIELDS.set(fields)
    try:
        yield fields
    finally:
        _FIELDS.reset(t2)
        REQUEST_ID.reset(t1)


def request_fields() -> dict:
    """The current request's access-line fields (a throw-away dict outside a request). Handlers add to it;
    the middleware logs it with the response status and duration."""
    fields = _FIELDS.get()
    return fields if fields is not None else {}


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, exc_info: Any = None,
              **fields: Any) -> None:
    """Log `event` (the message) with structured `fields` (top-level keys of the JSON line)."""
    logger.log(level, event, exc_info=exc_info, extra={"fields": {"event": event, **fields}})


# --------------------------------------------------------------------------- #
# Privacy helpers
# --------------------------------------------------------------------------- #
_SALT = secrets.token_bytes(16)          # per process: digests cannot be reversed by trying place ids


def coarse_point(point: Any, digits: int = 2) -> Optional[list[float]]:
    """(lat, lon) rounded to `digits` decimals (2 = ~1 km) for logs; None for no / unparsable point."""
    if point is None or isinstance(point, str):
        return None
    try:
        lat, lon = float(point[0]), float(point[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    return [round(lat, digits), round(lon, digits)]


def ids_digest(ids: Optional[Iterable[Any]]) -> Optional[str]:
    """Salted 12-hex digest of a set of place ids (order and duplicates ignored); None when empty."""
    items = sorted({str(x) for x in (ids or ())})
    if not items:
        return None
    h = hashlib.sha256(_SALT)
    for x in items:
        h.update(x.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:12]


# --------------------------------------------------------------------------- #
# Stage timings
# --------------------------------------------------------------------------- #
class StageTimes:
    """Seconds spent per stage within one request (stages may repeat; times add up)."""

    __slots__ = ("seconds",)

    def __init__(self) -> None:
        self.seconds: dict[str, float] = {}

    def add(self, stage: str, seconds: float) -> None:
        self.seconds[stage] = self.seconds.get(stage, 0.0) + max(0.0, float(seconds))

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time a block as stage `name`."""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - t0)

    def ms(self, stage: str) -> float:
        return round(self.seconds.get(stage, 0.0) * 1000.0, 1)


_STAGES: contextvars.ContextVar[Optional[StageTimes]] = contextvars.ContextVar("walk_stage_times", default=None)


@contextmanager
def collect_stage_times() -> Iterator[StageTimes]:
    """Collect the instrumented pipeline stages of the code run inside the block (this context only)."""
    times = StageTimes()
    token = _STAGES.set(times)
    try:
        yield times
    finally:
        _STAGES.reset(token)


def current_stage_times() -> Optional[StageTimes]:
    return _STAGES.get()


# pipeline function (module-global name in walk_planner.pipeline) -> stage
PIPELINE_STAGES = {
    "resolve_interest": "interest",
    "slot_candidates": "candidates",
    "extra_candidates": "candidates",
    "distances_from": "candidates",
    "place_candidate": "candidates",
    "plan_variants": "solver",
    "plan_sequence": "solver",
    "best_insertion": "solver",
}
_STAGE_ATTR = "__walk_stage__"
_INSTRUMENT_LOCK = threading.Lock()
_INSTRUMENTED = {"count": 0}
_ORIGINALS: dict[str, Callable] = {}


def _timed(stage: str, fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        times = _STAGES.get()
        if times is None:
            return fn(*args, **kwargs)
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            times.add(stage, time.perf_counter() - t0)

    setattr(wrapper, _STAGE_ATTR, stage)
    return wrapper


def instrument_pipeline() -> None:
    """Install the stage timers on ``walk_planner.pipeline`` (reference-counted; see the module docstring)."""
    from .. import pipeline

    with _INSTRUMENT_LOCK:
        _INSTRUMENTED["count"] += 1
        if _INSTRUMENTED["count"] > 1:
            return
        for name, stage in PIPELINE_STAGES.items():
            fn = getattr(pipeline, name, None)
            if fn is None or getattr(fn, _STAGE_ATTR, None):
                continue
            _ORIGINALS[name] = fn
            setattr(pipeline, name, _timed(stage, fn))


def uninstrument_pipeline() -> None:
    """Undo one ``instrument_pipeline``; the last one restores the original functions (unless someone
    replaced a timer in the meantime -- then that replacement is left as it is)."""
    from .. import pipeline

    with _INSTRUMENT_LOCK:
        if _INSTRUMENTED["count"] == 0:
            return
        _INSTRUMENTED["count"] -= 1
        if _INSTRUMENTED["count"]:
            return
        for name, fn in _ORIGINALS.items():
            current = getattr(pipeline, name, None)
            if getattr(current, _STAGE_ATTR, None) and getattr(current, "__wrapped__", None) is fn:
                setattr(pipeline, name, fn)
        _ORIGINALS.clear()
