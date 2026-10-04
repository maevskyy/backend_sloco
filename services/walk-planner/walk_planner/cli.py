"""Developer CLI of the Walk Planner: ``python -m walk_planner <command>`` (console script ``walk-planner``).

Commands (``<command> --help`` lists the options):

  plan | schedule | insert     the API operations: a JSON request (``--request FILE`` or ``-`` for stdin, or
                              convenience flags) -> the API JSON response on stdout; ``--pretty`` prints the
                              dashboard's Russian summary instead (variant captions, metrics, warnings, stop cards)
  search | place | config     catalog lookups (the API's /places/search, /places/{id}, /config)
  interest                    inspect the taste model: favourites -> the interest re-ordering per theme
  route                       probe the routing chain (OSRM -> ORS -> estimate) on "lat,lon;lat,lon" points
  bundle build|validate|info  the per-city data bundles (walk_planner.bundle)
  golden run|update|list      the golden expected API outputs (straight-line routing, one fixed bundle);
                              ``golden run --url http://host:port`` replays them against a RUNNING service
                              (acceptance test of a deployment: run it with routing disabled -- WALK_ROUTER_URL=
                              and ORS_API_KEY= set EMPTY; under compose an unset WALK_ROUTER_URL means osrm-foot)
  serve                       run the FastAPI service: exec ``uvicorn walk_planner.service.app:create_app --factory``

Bundle: ``--bundle PATH`` (a bundle directory, "a,b", or a root of bundles -> the newest per city) or the
``WALK_BUNDLE_DIR`` environment variable; inside the research repo the default is
``recommendation_system/ai_location_recommender/data/walk_bundles``. ``--city`` picks one of several.
Routing: like the service, ``make_provider()`` reads ``WALK_ROUTER_URL`` / ``ORS_API_KEY`` / ... from the
environment (``--routing estimate`` forces the straight-line estimate); golden always uses the estimate.
Exit codes: 0 ok · 1 failure (an API error response, an invalid bundle, golden differences) · 2 usage / setup.

The module also holds the small API facade the CLI and the golden outputs go through
(``api_plan`` / ``api_schedule`` / ``api_insert``: request body -> (HTTP status, response JSON), exactly the
package calls the service makes) and the golden machinery (``golden_scenario``, ``compare_json``,
``run_golden``, ``run_golden_http``).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

__all__ = [
    "PLAN_PATH", "SCHEDULE_PATH", "INSERT_PATH", "GOLDEN_FORMAT", "GOLDEN_TOLERANCE", "GOLDEN_OPTIONS",
    "ApiCall", "api_plan", "api_schedule", "api_insert",
    "scenario_request", "golden_scenario", "compare_json", "golden_diffs", "golden_dumps", "run_golden",
    "http_transport", "run_golden_http", "main",
]

PLAN_PATH = "/v1/walks/plan"
SCHEDULE_PATH = "/v1/walks/schedule"
INSERT_PATH = "/v1/walks/insert"
GOLDEN_FORMAT = "walk-golden/v1"
GOLDEN_TOLERANCE = 1e-6
GOLDEN_OPTIONS = {"lang": "ru", "geometry": "geojson"}
LANGS = ("ru", "en")
GEOMETRIES = ("geojson", "polyline6")
_PKG_ROOT = Path(__file__).resolve().parents[1]                  # services/walk_planner (source checkout)
GOLDEN_HTTP_TIMEOUT_S = 120.0                                    # one replayed call (a 24-hour plan takes ~5 s)
# The research data layout ("bundle build --data-dir"): role -> path pattern under the data dir.
_RESEARCH_LAYOUT = {
    "catalog_csv": "locations_{slug}_all.csv",
    "photo_manifest_csv": "visual_photo_profiles/image_metadata/visual_photo_metadata_cid_{slug}_all.csv",
    "text_npy": "embedding_store/location_embeddings_{slug}_all.npy",
    "text_meta_csv": "embedding_store/location_embeddings_{slug}_all_metadata.csv",
    "image_npy": "direct_image_embeddings/place_embedding_store/direct_place_image_embeddings_openclip_vitb32_v1.npy",
    "image_meta": ("direct_image_embeddings/place_embedding_store/"
                   "direct_place_image_embeddings_openclip_vitb32_v1_metadata.parquet"),
    "photos_root": "visual_photo_profiles/photos_cid",
}


class CliError(Exception):
    """A usage / setup problem (exit code 2): bad arguments, no bundle, unreadable input."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _eprint(*args) -> None:
    print(*args, file=sys.stderr, flush=True)


def _dumps(obj: Any, compact: bool = False) -> str:
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=None if compact else 2,
                      separators=(",", ":") if compact else None)


def _read_json(spec: str) -> Any:
    """JSON from a file path, or from stdin for "-"."""
    try:
        if spec == "-":
            return json.load(sys.stdin)
        return json.loads(Path(spec).expanduser().read_text(encoding="utf-8"))
    except OSError as exc:
        raise CliError(f"cannot read {spec}: {exc}") from exc
    except ValueError as exc:
        raise CliError(f"{spec} is not valid JSON: {exc}") from exc


def _split_ids(values: Optional[Sequence[str]]) -> Optional[list[str]]:
    """Repeatable, comma-separated id options -> one list (None when the option was not given)."""
    if values is None:
        return None
    out: list[str] = []
    for v in values:
        out += [x.strip() for x in str(v).split(",") if x.strip()]
    return out


def _latlon(text: str, what: str = "coordinates") -> tuple[float, float]:
    try:
        lat, lon = (float(x) for x in str(text).replace(" ", "").split(","))
    except ValueError as exc:
        raise CliError(f"{what}: expected LAT,LON, got {text!r}") from exc
    return lat, lon


def _jsonable(obj: Any) -> Any:
    """A JSON round trip (tuples -> lists, keys -> str); refuses NaN / Infinity."""
    return json.loads(json.dumps(obj, ensure_ascii=False, allow_nan=False))


# --------------------------------------------------------------------------- #
# Bundles
# --------------------------------------------------------------------------- #
_LOADED: dict[tuple[str, bool], Any] = {}


def _research_bundles() -> Optional[Path]:
    """``recommendation_system/ai_location_recommender/data/walk_bundles`` of the research repo this source
    checkout sits in (walk_planner/cli.py -> services/walk_planner -> repo root = parents[3]). Computed on
    use, never at import: a vendored or installed copy may sit fewer than 3 levels below "/" (then None)."""
    up = Path(__file__).resolve().parents
    if len(up) < 4:
        return None
    return up[3] / "recommendation_system" / "ai_location_recommender" / "data" / "walk_bundles"


def default_bundle_spec() -> Optional[str]:
    """WALK_BUNDLE_DIR, else the research repo's data/walk_bundles when it exists, else None."""
    env = os.environ.get("WALK_BUNDLE_DIR", "").strip()
    if env:
        return env
    research = _research_bundles()
    if research is not None and research.is_dir():
        return str(research)
    return None


def _load(path: Path, verify: bool):
    from .bundle import load_bundle

    key = (str(Path(path).resolve()), bool(verify))
    if key not in _LOADED:
        _LOADED[key] = load_bundle(path, verify=verify)
    return _LOADED[key]


def open_bundles(spec: Optional[str], verify: bool = True) -> list:
    """Every bundle `spec` names (``bundle.resolve_bundle_dirs``), loaded (cached per process)."""
    from .bundle import resolve_bundle_dirs

    spec = spec or default_bundle_spec()
    if not spec:
        raise CliError("no bundle: pass --bundle PATH or set WALK_BUNDLE_DIR (build one with 'bundle build')")
    return [_load(p, verify) for p in resolve_bundle_dirs(spec)]


def open_bundle(spec: Optional[str], city: Optional[str] = None, verify: bool = True):
    """The one bundle to use: the only one, or the one of `city` (case-insensitive)."""
    bundles = open_bundles(spec, verify=verify)
    if city:
        match = [b for b in bundles if str(b.city).casefold() == str(city).casefold()]
        if not match:
            raise CliError(f"no bundle for city {city!r} (have: {', '.join(sorted(b.city for b in bundles))})")
        return match[0]
    if len(bundles) > 1:
        raise CliError(f"several cities ({', '.join(sorted(b.city for b in bundles))}): pass --city")
    return bundles[0]


# --------------------------------------------------------------------------- #
# API facade: request body -> (HTTP status, response JSON), the package calls the service makes
# --------------------------------------------------------------------------- #
@dataclass
class ApiCall:
    """Outcome of one API operation: HTTP status + response JSON (an error body for 4xx), plus the
    package objects behind a success (PlanResult for plan; context + VariantState for edits)."""

    status: int
    response: dict
    result: Any = None
    context: Any = None
    state: Any = None


def _estimate_factory(_start=None):
    from .core import RoutingProvider

    return RoutingProvider()


def _env_factory(start=None):
    from .routing import make_provider

    return make_provider(start=start)


def _routing_version(provider) -> Optional[str]:
    v = getattr(provider, "data_version", None)
    return str(v) if v else None


def _check_options(lang: str, geometry: str) -> None:
    from .messages import PlannerInputError

    if lang not in LANGS:
        raise PlannerInputError("validation_error", {"field": "lang", "reason": "expected one of " + ", ".join(LANGS)})
    if geometry not in GEOMETRIES:
        raise PlannerInputError("validation_error",
                                {"field": "geometry", "reason": "expected one of " + ", ".join(GEOMETRIES)})


def _error_call(err, lang: str) -> ApiCall:
    from .present import error_body

    body = error_body(err, lang if lang in LANGS else "ru")
    return ApiCall(status=int(err.http_status), response=json.loads(json.dumps(body, ensure_ascii=False, default=str)))


def api_plan(body: dict, catalog, taste=None, *, lang: str = "ru", geometry: str = "geojson", debug: bool = False,
             photo_base_url: Optional[str] = None,
             provider_factory: Optional[Callable[[Any], Any]] = None) -> ApiCall:
    """POST /v1/walks/plan: ``normalize_params`` -> ``build_plan`` (one routing provider per request,
    ``provider_factory(start)``; default ``routing.make_provider``) -> ``present.plan_response``.
    A PlannerInputError becomes its HTTP status + ``{"error": {...}}``."""
    from .messages import PlannerInputError
    from .pipeline import build_plan, make_context, normalize_params
    from .present import plan_response

    try:
        _check_options(lang, geometry)
        params = normalize_params(body, catalog)
        start = make_context(params, catalog).start_resolved
        provider = (provider_factory or _env_factory)(start)
        result = build_plan(params, catalog, provider=provider, taste=taste)
        resp = plan_response(result, catalog, lang=lang, photo_base_url=photo_base_url, geometry=geometry, debug=debug)
    except PlannerInputError as err:
        return _error_call(err, lang)
    return ApiCall(status=200, response=_jsonable(resp), result=result, context=result.context)


def _edit_body(body: Any):
    """(request echo, sequence, variant_index) of a /schedule or /insert body (validation_error otherwise)."""
    from .messages import PlannerInputError

    if not isinstance(body, dict):
        raise PlannerInputError("validation_error", {"field": "body", "reason": "expected an object"})
    if not isinstance(body.get("request"), dict):
        raise PlannerInputError("validation_error", {"field": "request",
                                                     "reason": "expected the request object of the plan response"})
    if not isinstance(body.get("sequence"), list):
        raise PlannerInputError("validation_error", {"field": "sequence", "reason": "expected a list of stops"})
    idx = body.get("variant_index", 0)
    if idx is None:
        idx = 0
    if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < 100:
        raise PlannerInputError("validation_error", {"field": "variant_index", "reason": "expected an integer 0..99"})
    return body["request"], body["sequence"], idx


def api_schedule(body: dict, catalog, taste=None, *, lang: str = "ru", geometry: str = "geojson",
                 photo_base_url: Optional[str] = None,
                 provider_factory: Optional[Callable[[Any], Any]] = None) -> ApiCall:
    """POST /v1/walks/schedule — body ``{"request": <plan response request>, "sequence": [EditStop],
    "variant_index": 0}``: ``from_request_echo`` -> ``schedule`` (the exact order) -> ``edit_response``."""
    from .messages import PlannerInputError
    from .pipeline import from_request_echo, schedule
    from .present import edit_response

    try:
        _check_options(lang, geometry)
        echo, sequence, idx = _edit_body(body)
        ctx = from_request_echo(echo, catalog)
        provider = (provider_factory or _env_factory)(ctx.start_resolved)
        state = schedule(ctx, sequence, catalog, provider, index=idx, taste=taste)
        resp = edit_response(ctx, state, catalog, lang=lang, photo_base_url=photo_base_url, geometry=geometry,
                             routing_version=_routing_version(provider))
    except PlannerInputError as err:
        return _error_call(err, lang)
    return ApiCall(status=200, response=_jsonable(resp), context=ctx, state=state)


def api_insert(body: dict, catalog, taste=None, *, lang: str = "ru", geometry: str = "geojson",
               photo_base_url: Optional[str] = None,
               provider_factory: Optional[Callable[[Any], Any]] = None) -> ApiCall:
    """POST /v1/walks/insert — body ``{"request", "sequence", "place_id", "allow_temporarily_closed": false,
    "dwell_min": null, "variant_index": 0}``: ``insert_place`` (best position) -> ``edit_response`` +
    ``inserted_index``."""
    from .messages import PlannerInputError
    from .pipeline import from_request_echo, insert_place
    from .present import edit_response

    try:
        _check_options(lang, geometry)
        echo, sequence, idx = _edit_body(body)
        allow = body.get("allow_temporarily_closed", False)
        if allow is None:
            allow = False
        if not isinstance(allow, bool):
            raise PlannerInputError("validation_error", {"field": "allow_temporarily_closed",
                                                         "reason": "expected true or false"})
        ctx = from_request_echo(echo, catalog)
        provider = (provider_factory or _env_factory)(ctx.start_resolved)
        state, pos = insert_place(ctx, sequence, body.get("place_id"), catalog, provider,
                                  allow_temporarily_closed=allow, index=idx, dwell_min=body.get("dwell_min"),
                                  taste=taste)
        resp = edit_response(ctx, state, catalog, lang=lang, photo_base_url=photo_base_url, geometry=geometry,
                             inserted_index=pos, routing_version=_routing_version(provider))
    except PlannerInputError as err:
        return _error_call(err, lang)
    return ApiCall(status=200, response=_jsonable(resp), context=ctx, state=state)


# --------------------------------------------------------------------------- #
# Pretty (the dashboard's Russian text)
# --------------------------------------------------------------------------- #
_SEVERITY_ICON = {"info": "ℹ️", "warning": "⚠️", "error": "⛔"}


def _strip_md(text: Optional[str]) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")


def _variant_text_ru(ctx, states: list, sel: int, catalog, links: bool = False) -> list[str]:
    from .present import navigation_ru, render_variant_ru

    r = render_variant_ru(ctx, states, sel, catalog)
    lines: list[str] = []
    state = states[sel]
    head = f"── Вариант {sel + 1}" + (" (изменён)" if state.edited else "") + " ──"
    lines.append(head)
    if r["metric_stops"] is not None:
        lines.append(f"Остановок: {r['metric_stops']} · {r['metric_finish_label']}: {r['metric_finish']} "
                     f"({r['metric_delta']}) · Пешком: {r['metric_walk']} · Окно: {r['metric_window']}")
    for c in r["captions"]:
        lines.append(f"  {c}")
    for kind, texts in (("warning", r["warnings"]), ("info", r["infos"])):
        for text in texts:                   # some texts carry their own icon (⚠️ / ⛔), like on the page
            icon = "" if text[:1] in ("⚠", "⛔", "ℹ") else _SEVERITY_ICON[kind] + " "
            lines.append(f"  {icon}{text}")
    for card in r["stop_lines"]:
        lines.append(f"  {card[0]}")
        lines += [f"      {x}" for x in card[1:]]
    if state.plan.stops:
        nav = navigation_ru(ctx, state.plan, catalog)
        for j, url in enumerate(nav.get("google") or []):
            lines.append(f"  Google Maps{f' (часть {j + 1})' if len(nav['google']) > 1 else ''}: {url}")
        if links:
            for leg in nav.get("legs") or []:
                lines.append(f"    {leg.get('from')} → {leg.get('to')}: {leg.get('google')}")
    return lines


def pretty_plan(result, catalog, links: bool = False) -> str:
    """A plan as the dashboard shows it (Russian): header, request notes, per variant the caption,
    metrics, warnings and stop cards, plus the Google Maps link."""
    from .present import variants_caption_ru, window_caption_ru
    from .slots import SHAPES, STYLES

    ctx = result.context
    p = ctx.params
    start = ("без старта (по району)" if ctx.start_resolved is None else
             "центр города" if p.start == "city_center" else
             (f"место {p.start_place_id}" if p.start_place_id else "точка") +
             f" ({ctx.start_resolved[0]:.5f}, {ctx.start_resolved[1]:.5f})")
    lines = [f"{p.city} · {p.date.isoformat()} · {ctx.start_label}–{ctx.end_label} · {SHAPES[p.shape].label_ru} · "
             f"{STYLES[p.style].label_ru} · старт: {start}",
             window_caption_ru(ctx.budget_min, ctx.weekday, ctx.next_day)]
    it = result.interest
    if it.mode == "favourites":
        lines.append(f"Интерес: по избранному (мест {len(it.used)}, профилей {it.profiles}, сила {it.strength:g})"
                     + (f"; не учтены: {', '.join(it.ignored)}" if it.ignored else ""))
    else:
        lines.append("Интерес: популярность (без избранного)")
    for m in result.messages:
        lines.append(f"{_SEVERITY_ICON.get(m.severity, '')} {m.text('ru')}")
    if result.status != "ok":
        lines.append(f"Статус: {result.status}")
        return "\n".join(lines)
    cap = variants_caption_ru(ctx, result.variants, 0)
    if cap:
        lines.append(_strip_md(cap))
    for i in range(len(result.variants)):
        lines.append("")
        lines += _variant_text_ru(ctx, result.variants, i, catalog, links=links)
    return "\n".join(lines)


def pretty_edit(call: ApiCall, catalog, links: bool = False) -> str:
    """An edited variant (schedule / insert) as the dashboard shows it."""
    lines = []
    if call.response.get("inserted_index") is not None:
        lines.append(f"Место добавлено на позицию {call.response['inserted_index'] + 1}")
    lines += _variant_text_ru(call.context, [call.state], 0, catalog, links=links)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Commands: plan / schedule / insert
# --------------------------------------------------------------------------- #
def _provider_factory(args):
    return _estimate_factory if getattr(args, "routing", "auto") == "estimate" else _env_factory


def _photo_base(args) -> Optional[str]:
    v = getattr(args, "photo_base_url", None)
    return v if v is not None else (os.environ.get("PHOTO_BASE_URL") or None)


def _parse_slots(text: str) -> list:
    out = []
    for item in [x.strip() for x in text.split(",") if x.strip()]:
        code, _, dwell = item.partition(":")
        if dwell:
            try:
                out.append({"activity": code.strip(), "dwell_min": int(dwell)})
            except ValueError as exc:
                raise CliError(f"--slots: dwell of {code!r} must be whole minutes, got {dwell!r}") from exc
        else:
            out.append({"activity": code.strip(), "dwell_min": None})
    return out


def _today(tz: str) -> str:
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo

        return _dt.datetime.now(ZoneInfo(tz)).date().isoformat()
    except Exception:                  # no tz database: the machine's date
        return _dt.date.today().isoformat()


def _plan_body(args, bundle) -> dict:
    """The plan request: the --request JSON (if any) with every given flag overriding its field;
    city, date (today in the city) and start (the city centre, unless shape "free") filled in."""
    body = _read_json(args.request) if args.request else {}
    if not isinstance(body, dict):
        raise CliError("--request: expected a JSON object (a plan request)")
    body = dict(body)
    flags = {
        "date": args.date, "start_time": args.start, "end_time": args.end, "end_day_offset": args.end_day_offset,
        "shape": args.shape, "style": args.style, "variants": args.variants, "radius_km": args.radius,
        "top_k": args.top_k, "personalization_strength": args.strength,
    }
    for k, v in flags.items():
        if v is not None:
            body[k] = v
    if args.slots is not None:
        body["slots"] = _parse_slots(args.slots)
    for key, opt in (("must_visit_place_ids", args.must), ("favourite_place_ids", args.fav),
                     ("want_to_go_place_ids", args.wtg)):
        ids = _split_ids(opt)
        if ids is not None:
            body[key] = ids
    if args.no_fill:
        body["fill_window"] = False
    if args.known_hours_only:
        body["known_hours_only"] = True
    if args.start_latlon:
        lat, lon = _latlon(args.start_latlon, "--start-latlon")
        body["start"] = {"lat": lat, "lon": lon}
    elif args.start_place:
        body["start"] = {"place_id": args.start_place}
    body.setdefault("city", bundle.city)
    body.setdefault("date", _today(bundle.timezone))
    if "start" not in body and body.get("shape", "loop") != "free":
        body["start"] = "city_center"
    return body


def _print_call(call: ApiCall, args, pretty: Callable[[], str]) -> int:
    if call.status == 200 and args.pretty:
        print(pretty())
    else:
        print(_dumps(call.response, compact=args.compact))
    if call.status != 200:
        err = call.response.get("error", {})
        _eprint(f"HTTP {call.status} {err.get('code')}: {err.get('message')}")
        return 1
    return 0


def cmd_plan(args) -> int:
    bundle = open_bundle(args.bundle, args.city, verify=not args.no_verify)
    body = _plan_body(args, bundle)
    t = time.perf_counter()
    call = api_plan(body, bundle.catalog, bundle.taste, lang=args.lang, geometry=args.geometry, debug=args.debug,
                    photo_base_url=_photo_base(args), provider_factory=_provider_factory(args))
    if args.timing:
        _eprint(f"plan: {time.perf_counter() - t:.3f} s")
    return _print_call(call, args, lambda: pretty_plan(call.result, bundle.catalog, links=args.links))


def _edit_source(args) -> dict:
    """The /schedule or /insert body: --request, or built from --from-plan (a plan / edit response)."""
    if args.request:
        body = _read_json(args.request)
        if not isinstance(body, dict):
            raise CliError("--request: expected a JSON object")
        return dict(body)
    if not args.from_plan:
        raise CliError("pass --request FILE (a schedule / insert body) or --from-plan FILE (a plan or edit response)")
    src = _read_json(args.from_plan)
    if not isinstance(src, dict):
        raise CliError("--from-plan: expected a JSON object")
    if "variants" in src:                                   # a plan response
        variants = src.get("variants") or []
        if not 0 <= args.variant < len(variants):
            raise CliError(f"--variant {args.variant}: the plan has {len(variants)} variant(s)")
        return {"request": src.get("request"), "sequence": list(variants[args.variant].get("sequence") or []),
                "variant_index": args.variant}
    if "variant" in src:                                    # an edit response
        v = src["variant"] or {}
        return {"request": src.get("request"), "sequence": list(v.get("sequence") or []),
                "variant_index": int(v.get("index") or 0)}
    if "request" in src and "sequence" in src:              # already a body
        return dict(src)
    raise CliError("--from-plan: not a plan response, an edit response or a schedule body")


def _apply_edits(seq: list, edits: Sequence[str]) -> list:
    """--edit move:FROM:TO | remove:I | dwell:I:MIN, applied in order (negative indices count from the end)."""
    if not isinstance(seq, list) or not all(isinstance(e, dict) for e in seq):
        raise CliError("--edit: the sequence must be a list of stop objects")
    seq = [dict(e) for e in seq]
    for spec in edits or ():
        parts = spec.split(":")
        try:
            if parts[0] == "move" and len(parts) == 3:
                n = len(seq)
                f, t = int(parts[1]), int(parts[2])
                f, t = f + n if f < 0 else f, t + n if t < 0 else t
                if not (0 <= f < n and 0 <= t < n):
                    raise CliError(f"--edit {spec}: index out of range (route has {n} stops)")
                seq.insert(t, seq.pop(f))
            elif parts[0] == "remove" and len(parts) == 2:
                i = int(parts[1])
                if not -len(seq) <= i < len(seq):
                    raise CliError(f"--edit {spec}: index out of range (route has {len(seq)} stops)")
                seq.pop(i)
            elif parts[0] == "dwell" and len(parts) == 3:
                i = int(parts[1])
                if not -len(seq) <= i < len(seq):
                    raise CliError(f"--edit {spec}: index out of range (route has {len(seq)} stops)")
                seq[i] = dict(seq[i], dwell_min=float(parts[2]), dwell_fixed=True)
            else:
                raise CliError(f"--edit {spec}: expected move:FROM:TO, remove:I or dwell:I:MIN")
        except ValueError as exc:
            raise CliError(f"--edit {spec}: indices / minutes must be numbers") from exc
    return seq


def _edit_bundle(args, body: dict):
    req = body.get("request") if isinstance(body.get("request"), dict) else {}
    return open_bundle(args.bundle, args.city or req.get("city"), verify=not args.no_verify)


def cmd_schedule(args) -> int:
    body = _edit_source(args)
    if args.edit:
        body["sequence"] = _apply_edits(body.get("sequence") or [], args.edit)
    bundle = _edit_bundle(args, body)
    call = api_schedule(body, bundle.catalog, bundle.taste, lang=args.lang, geometry=args.geometry,
                        photo_base_url=_photo_base(args), provider_factory=_provider_factory(args))
    return _print_call(call, args, lambda: pretty_edit(call, bundle.catalog, links=args.links))


def cmd_insert(args) -> int:
    body = _edit_source(args)
    if args.place:
        body["place_id"] = args.place
    if args.allow_temporarily_closed:
        body["allow_temporarily_closed"] = True
    if args.dwell is not None:
        body["dwell_min"] = args.dwell
    if not body.get("place_id"):
        raise CliError("insert needs --place PLACE_ID (or place_id in the --request body)")
    bundle = _edit_bundle(args, body)
    call = api_insert(body, bundle.catalog, bundle.taste, lang=args.lang, geometry=args.geometry,
                      photo_base_url=_photo_base(args), provider_factory=_provider_factory(args))
    return _print_call(call, args, lambda: pretty_edit(call, bundle.catalog, links=args.links))


# --------------------------------------------------------------------------- #
# Commands: search / place / config / interest / route
# --------------------------------------------------------------------------- #
def cmd_search(args) -> int:
    from .messages import PlannerInputError
    from .present import error_body

    bundle = open_bundle(args.bundle, args.city, verify=not args.no_verify)
    near = _latlon(args.near, "--near") if args.near else None
    try:
        hits = bundle.catalog.search(args.query, near=near, limit=args.limit,
                                     include_closed_forever=args.include_closed, photo_base_url=_photo_base(args))
    except PlannerInputError as err:
        print(_dumps(error_body(err, args.lang), compact=args.compact))
        return 1
    if args.pretty:
        for i, h in enumerate(hits, 1):
            dist = f" · {h['distance_m']} м" if "distance_m" in h else ""
            status = "" if h["business_status"] == "operational" else f" · {h['business_status']}"
            rating = f" · ★{h['rating']:.1f} ({h['rating_count']})" if h.get("rating") else ""
            print(f"{i:2d}. {h['name']} [{h['place_id']}] — {h.get('type_label') or ''}{rating}{dist}{status} "
                  f"({h['match']})")
        if not hits:
            print("ничего не найдено")
    else:
        print(_dumps({"query": args.query, "results": hits}, compact=args.compact))
    return 0


def cmd_place(args) -> int:
    from .messages import PlannerInputError
    from .present import error_body

    bundle = open_bundle(args.bundle, args.city, verify=not args.no_verify)
    if not bundle.catalog.has(args.place_id):
        err = PlannerInputError("unknown_place", {"place_id": args.place_id, "field": "place_id"})
        print(_dumps(error_body(err, args.lang), compact=args.compact))
        return 1
    print(_dumps(bundle.catalog.place_detail(args.place_id, photo_base_url=_photo_base(args)), compact=args.compact))
    return 0


def cmd_config(args) -> int:
    from .present import config_view

    bundle = open_bundle(args.bundle, args.city, verify=not args.no_verify)
    print(_dumps(_jsonable(config_view(bundle.catalog)), compact=args.compact))
    return 0


def cmd_interest(args) -> int:
    """Favourites -> the interest map (``interest.interest_map``, as a plan uses it); prints the top
    places by interest (optionally of one catalog theme / theme group) with their cold-start value and
    rank, taste percentile and most similar seed."""
    import math

    from .interest import interest_map

    if not (math.isfinite(args.strength) and 0.0 <= args.strength <= 1.0):
        raise CliError("--strength must be between 0 and 1")
    bundle = open_bundle(args.bundle, args.city, verify=not args.no_verify)
    cat = bundle.catalog
    rows = cat.rows
    favs, wtg = _split_ids(args.fav) or [], _split_ids(args.wtg) or []
    if (favs or wtg) and bundle.taste is None:
        raise CliError(f"bundle {bundle.bundle_id} has no taste artifacts (built without embeddings)")
    themes = [str(x) for x in (rows["theme"] if "theme" in rows.columns else
                               rows["theme_group"] if "theme_group" in rows.columns else [""] * len(rows))]
    groups = [str(x) for x in (rows["theme_group"] if "theme_group" in rows.columns else [""] * len(rows))]
    cold = cat.cold_interest()
    t = time.perf_counter()
    res = interest_map(cat.place_ids, themes, cold, bundle.taste, favourite_ids=favs, want_to_go_ids=wtg,
                       strength=args.strength)
    ms = (time.perf_counter() - t) * 1000.0
    pids = cat.place_ids
    keep = [i for i in range(len(pids)) if not args.theme or args.theme in (themes[i], groups[i])]
    if args.theme and not keep:
        raise CliError(f"--theme {args.theme!r}: no places (themes: {', '.join(sorted(set(themes)))})")
    cold_of = {i: float(cold.get(pids[i], 0.0)) for i in keep}
    new_of = {i: float(res.map.get(pids[i], 0.0)) for i in keep}
    by_cold = sorted(keep, key=lambda i: (-cold_of[i], pids[i]))
    cold_rank = {i: r for r, i in enumerate(by_cold, 1)}
    top = sorted(keep, key=lambda i: (-new_of[i], -cold_of[i], pids[i]))[: max(0, args.top)]
    taste_pct, similar, seeds = res.taste_pct or {}, res.similar_to or {}, set(res.used)
    items = []
    for r, i in enumerate(top, 1):
        pid = pids[i]
        sim = similar.get(pid)
        items.append({"rank": r, "cold_rank": cold_rank[i], "place_id": pid, "name": cat.name_of(pid),
                      "theme": themes[i], "cold": cold_of[i], "interest": new_of[i], "taste_pct": taste_pct.get(pid),
                      "similar_to": sim, "similar_to_name": cat.name_of(sim) if sim and cat.has(sim) else None,
                      "seed": pid in seeds})
    summary = {"bundle_id": bundle.bundle_id, "mode": res.mode, "strength": res.strength, "profiles": res.profiles,
               "used": res.used, "ignored": res.ignored, "elapsed_ms": round(ms, 1),
               "theme": args.theme, "places": len(keep), "top": items}
    if args.json:
        print(_dumps(_jsonable(summary), compact=args.compact))
        return 0
    print(f"{bundle.bundle_id} · mode {res.mode} · strength {res.strength:g} · profiles {res.profiles} · "
          f"{ms:.1f} ms · {len(keep)} places" + (f" of {args.theme}" if args.theme else ""))
    if res.used:
        print("seeds: " + "; ".join(f"{cat.name_of(p)} [{p}]" for p in res.used if cat.has(p)))
    if res.ignored:
        print("ignored: " + ", ".join(res.ignored))
    print(f"{'#':>3} {'cold#':>6} {'cold':>6} {'interest':>8} {'taste%':>6}  name  (theme) ~ similar to")
    for it in items:
        tpv = f"{it['taste_pct']:.2f}" if it["taste_pct"] is not None else "  -  "
        mark = " *seed*" if it["seed"] else ""
        simn = f" ~ {it['similar_to_name']}" if it["similar_to_name"] and not it["seed"] else ""
        print(f"{it['rank']:>3} {it['cold_rank']:>6} {it['cold']:>6.3f} {it['interest']:>8.3f} {tpv:>6}  "
              f"{it['name']}  ({it['theme']}){mark}{simn}")
    return 0


def _parse_coords(text: str) -> list[tuple[float, float]]:
    pts = [_latlon(p, "--coords") for p in str(text).split(";") if p.strip()]
    if len(pts) < 2:
        raise CliError("--coords needs at least two points: \"lat,lon;lat,lon\"")
    return pts


def cmd_route(args) -> int:
    """Route legs through the configured chain (or the estimate) + quality + chain events + status."""
    from .routing import polyline6_encode, routing_status

    coords = _parse_coords(args.coords)
    provider = _provider_factory(args)(None)
    t = time.perf_counter()
    legs = provider.route_legs(coords)
    ms = (time.perf_counter() - t) * 1000.0
    quality = ({leg.get("quality", "estimate") for leg in legs})
    overall = quality.pop() if len(quality) == 1 else "mixed"
    out = {
        "provider": type(provider).__name__, "elapsed_ms": round(ms, 1), "quality": overall,
        "data_version": _routing_version(provider),
        "legs": [{"index": i, "from": list(coords[i]), "to": list(coords[i + 1]),
                  "duration_min": float(leg["duration_min"]), "distance_km": float(leg["distance_km"]),
                  "quality": leg.get("quality", "estimate"), "provider": leg.get("provider", "haversine"),
                  "points": len(leg.get("geometry") or []),
                  **({"geometry_polyline6": polyline6_encode(leg.get("geometry") or [])} if args.geometry == "polyline6"
                     else {"geometry": leg.get("geometry")})} for i, leg in enumerate(legs)],
        "events": list(getattr(provider, "events", []) or []),
        "summary": provider.summary() if hasattr(provider, "summary") else None,
        "status": routing_status() if args.routing != "estimate" else None,
    }
    if args.json:
        print(_dumps(_jsonable(out), compact=args.compact))
        return 0
    print(f"provider {out['provider']} · quality {overall} · {ms:.0f} ms"
          + (f" · data_version {out['data_version']}" if out["data_version"] else ""))
    for leg in out["legs"]:
        print(f"  leg {leg['index']}: {leg['duration_min']:.1f} min · {leg['distance_km'] * 1000:.0f} m · "
              f"{leg['quality']}/{leg['provider']} · {leg['points']} points")
    for e in out["events"]:
        print(f"  event: {json.dumps(e, ensure_ascii=False, default=str)}")
    if out["status"] is not None:
        st = out["status"]
        print(f"chain: {' -> '.join(st.get('chain') or [])} · streets available: {st.get('streets_available')}")
        for r in st.get("routers") or []:
            print(f"  {r.get('name', '?')}: breaker {((r.get('breaker') or {}).get('state'))}")
    return 0


# --------------------------------------------------------------------------- #
# Commands: bundle build / validate / info
# --------------------------------------------------------------------------- #
def _shell_join(argv: Sequence[str]) -> str:
    import shlex

    return " ".join(shlex.quote(a) for a in argv)


def cmd_bundle_build(args) -> int:
    from .bundle import build_bundle, slugify, validate_bundle

    paths: dict[str, Optional[str]] = {k: getattr(args, k) for k in _RESEARCH_LAYOUT}
    out_root = args.out_root
    if args.data_dir:
        data = Path(args.data_dir).expanduser()
        slug = args.city_slug or (slugify(args.city) if args.city else None)
        if not slug:
            raise CliError("--data-dir needs --city-slug (or --city) to find locations_<slug>_all.csv")
        for role, pattern in _RESEARCH_LAYOUT.items():
            if paths[role] is None:
                p = data / pattern.format(slug=slug)
                if p.exists():
                    paths[role] = str(p)
        out_root = out_root or str(data / "walk_bundles")
    if not paths["catalog_csv"]:
        raise CliError("bundle build needs --catalog-csv (or --data-dir with the research layout)")
    if not out_root:
        raise CliError("bundle build needs --out-root (or --data-dir: <data-dir>/walk_bundles)")
    if args.no_photos_check:
        paths["photos_root"] = None
    if args.no_interest:
        for role in ("text_npy", "text_meta_csv", "image_npy", "image_meta"):
            paths[role] = None
    for role in ("catalog_csv", "photo_manifest_csv", "text_npy", "text_meta_csv", "image_npy", "image_meta",
                 "photos_root"):
        print(f"  {role:20s} {paths[role] or '-'}")
    t = time.perf_counter()
    path = build_bundle(out_root, city_slug=args.city_slug, catalog_csv=paths["catalog_csv"],
                        photo_manifest_csv=paths["photo_manifest_csv"], text_npy=paths["text_npy"],
                        text_meta_csv=paths["text_meta_csv"], image_npy=paths["image_npy"],
                        image_meta=paths["image_meta"], city=args.city, timezone=args.timezone,
                        photos_root=paths["photos_root"], max_photos=args.max_photos,
                        builder_cmd=_shell_join(args.argv), progress=lambda m: print(f"  {m}", flush=True))
    build_s = time.perf_counter() - t
    rc = 0
    if not args.no_validate:
        t = time.perf_counter()
        report = validate_bundle(path, deep=True, photos_root=paths["photos_root"])
        _print_report(report)
        print(f"  validate: {time.perf_counter() - t:.2f} s")
        rc = 0 if report["ok"] else 1
    print(f"built in {build_s:.1f} s: {path}")
    return rc


def _print_report(report: dict) -> None:
    status = "OK" if report["ok"] else "INVALID"
    print(f"{status}: {report.get('bundle_id')} ({report.get('city')}) · {'deep' if report['deep'] else 'shallow'} · "
          f"{report['elapsed_s']:.2f} s · {report['bundle_dir']}")
    for name, c in report["checks"].items():
        details = {k: v for k, v in c.items() if k not in ("ok", "errors", "warnings")}
        info = ", ".join(f"{k}={v}" for k, v in details.items() if not isinstance(v, (dict, list)) or k == "city_area")
        print(f"  [{'ok' if c['ok'] else 'FAIL'}] {name}" + (f" — {info}" if info else ""))
        for e in c["errors"]:
            print(f"      error: {e}")
        for w in c["warnings"]:
            print(f"      warning: {w}")


def cmd_bundle_validate(args) -> int:
    from .bundle import resolve_bundle_dirs, validate_bundle

    spec = args.path or args.bundle or default_bundle_spec()
    if not spec:
        raise CliError("bundle validate needs a PATH (or --bundle / WALK_BUNDLE_DIR)")
    reports = [validate_bundle(p, deep=not args.shallow, photos_root=args.photos_root)
               for p in resolve_bundle_dirs(spec)]
    if args.json:
        print(_dumps(_jsonable(reports if len(reports) > 1 else reports[0]), compact=args.compact))
    else:
        for r in reports:
            _print_report(r)
    return 0 if all(r["ok"] for r in reports) else 1


def cmd_bundle_info(args) -> int:
    from .bundle import bundle_info, resolve_bundle_dirs

    spec = args.path or args.bundle or default_bundle_spec()
    if not spec:
        raise CliError("bundle info needs a PATH (or --bundle / WALK_BUNDLE_DIR)")
    infos = [bundle_info(p) for p in resolve_bundle_dirs(spec)]
    if args.json:
        print(_dumps(_jsonable(infos if len(infos) > 1 else infos[0]), compact=args.compact))
        return 0
    for i in infos:
        cov = i.get("coverage") or {}
        print(f"{i['bundle_id']} · {i['city']} ({i['timezone']}) · built {i['built_at']} · {i['rows']} places · "
              f"{i['total_bytes'] / 1e6:.1f} MB")
        print(f"  dir: {i['bundle_dir']}")
        print(f"  by theme group: {i.get('rows_by_theme_group')}")
        print(f"  coverage: hours {cov.get('opening_hours')}, photos {cov.get('photos')}, closed {cov.get('closed')}")
        it = i.get("interest")
        print("  interest: " + ("none" if not it else
                                f"{it['version']} · text {it['text_model']} ({it['text_dim']}d, "
                                f"{it['places_with_text']}) · image {it['image_model']} ({it['image_dim']}d, "
                                f"{it['places_with_image']}) · fingerprint {str(it['fingerprint'])[:12]}"))
        print(f"  content_sha256 {i['content_sha256']}")
        for rel, size in (i.get("files") or {}).items():
            print(f"    {rel:32s} {size / 1e6:9.2f} MB")
    return 0


# --------------------------------------------------------------------------- #
# Golden expected outputs
# --------------------------------------------------------------------------- #
_SCENARIO_REQUEST_KEYS = (
    "city", "date", "start_time", "end_time", "end_day_offset", "shape", "start", "style", "slots",
    "must_visit_place_ids", "favourite_place_ids", "want_to_go_place_ids", "personalization_strength",
    "variants", "radius_km", "fill_window", "known_hours_only", "top_k",
)


def default_golden_dir() -> Optional[Path]:
    d = _PKG_ROOT / "golden"
    return d if d.is_dir() else None


def scenario_request(sc: dict) -> dict:
    """A golden scenario as the API plan request body (its request fields; ids, edits and notes dropped)."""
    return _jsonable({k: sc[k] for k in _SCENARIO_REQUEST_KEYS if k in sc})


def _call_record(method: str, path: str, options: dict, body: dict, call: ApiCall) -> dict:
    return {"call": f"{method} {path}", "options": dict(options), "body": body, "status": call.status,
            "response": call.response}


def _golden_chain(ops: list, plan_call: ApiCall, bundle, options: dict, factory) -> list:
    """An edit chain as the API calls a client makes (the dashboard editor's ops, as replayed by
    golden/tools/parity_check.run_chain): the client keeps variant 0's ``sequence`` and the removed
    stops ("Не заходить"); move / remove / restore / set_dwell send the edited sequence to /schedule,
    add sends the place to /insert. set_dwell is the dashboard's minutes editor: every stop's shown
    minutes (int(round)) with the edited one changed, all stops dwell_fixed. One step per op:
    ``{op, resolved, applied, call}`` (call None when nothing was sent)."""
    catalog, taste = bundle.catalog, bundle.taste
    resp = plan_call.response
    if plan_call.status != 200 or not resp.get("variants"):
        return [{"op": op, "resolved": dict(op), "applied": False, "call": None} for op in ops]
    request = resp["request"]
    seq = [dict(e) for e in resp["variants"][0]["sequence"]]
    removed: list[dict] = []
    steps = []
    kw = dict(lang=options["lang"], geometry=options["geometry"], provider_factory=factory)
    for op in ops:
        kind = op.get("op")
        done = dict(op)
        new_seq = None
        call = None
        applied = False
        if kind == "move":
            n = len(seq)
            f = op["from"] + n if op["from"] < 0 else op["from"]
            t = op["to"] + n if op["to"] < 0 else op["to"]
            done.update({"from": f, "to": t})
            if 0 <= f < n and 0 <= t < n:
                done["place_id"] = seq[f]["place_id"]
                ns = list(seq)
                ns.insert(t, ns.pop(f))
                if [e["place_id"] for e in ns] != [e["place_id"] for e in seq]:
                    new_seq = ns
        elif kind == "remove":
            i = op["index"]
            done["place_id"] = seq[i]["place_id"] if 0 <= i < len(seq) else None
            if 0 <= i < len(seq):
                removed = removed + [seq[i]]
                new_seq = seq[:i] + seq[i + 1:]
        elif kind == "restore":
            ri, t = op["removed_index"], op["to"]
            if 0 <= ri < len(removed) and 0 <= t <= len(seq):
                item = removed[ri]
                done["place_id"] = item["place_id"]
                new_seq = seq[:t] + [item] + seq[t:]
                removed = removed[:ri] + removed[ri + 1:]
        elif kind == "add":
            pid = str(op["place_id"])
            done["name"] = catalog.name_of(pid) if catalog.has(pid) else None
            body = {"request": request, "sequence": seq, "place_id": pid, "allow_temporarily_closed": False,
                    "variant_index": 0}
            c = api_insert(body, catalog, taste, **kw)
            call = _call_record("POST", INSERT_PATH, options, _jsonable(body), c)
            if c.status == 200:
                applied = True
                done["inserted_index"] = c.response.get("inserted_index")
                request, seq = c.response["request"], [dict(e) for e in c.response["variant"]["sequence"]]
                removed = [e for e in removed if e["place_id"] != pid]
            else:
                done["skipped"] = c.response.get("error", {}).get("code")
        elif kind == "set_dwell":
            i, minutes = op["index"], op["dwell_min"]
            done["place_id"] = seq[i]["place_id"]
            shown = [int(round(float(e["dwell_min"]))) for e in seq]
            edited = list(shown)
            edited[i] = minutes
            new_min = [int(x) for x in edited]
            if new_min != shown:
                new_seq = [dict(e, dwell_min=float(m), dwell_fixed=True) for e, m in zip(seq, new_min)]
        else:
            raise ValueError(f"unknown edit op {kind!r}")
        if new_seq is not None:
            body = {"request": request, "sequence": new_seq, "variant_index": 0}
            c = api_schedule(body, catalog, taste, **kw)
            call = _call_record("POST", SCHEDULE_PATH, options, _jsonable(body), c)
            applied = c.status == 200
            if applied:
                request, seq = c.response["request"], [dict(e) for e in c.response["variant"]["sequence"]]
            else:
                done["skipped"] = c.response.get("error", {}).get("code")
        steps.append({"op": dict(op), "resolved": done, "applied": applied, "call": call})
    return steps


def golden_scenario(sc: dict, bundle, *, options: Optional[dict] = None) -> dict:
    """The golden expected output of one scenario: the API response of its /plan call and, per edit
    chain, the /schedule and /insert calls of its ops — straight-line routing (``core.RoutingProvider``),
    no photo base URL, `options` (default lang "ru", geometry "geojson")."""
    options = dict(GOLDEN_OPTIONS if options is None else options)
    factory = _estimate_factory
    body = scenario_request(sc)
    call = api_plan(body, bundle.catalog, bundle.taste, lang=options["lang"], geometry=options["geometry"],
                    provider_factory=factory)
    out = {"format": GOLDEN_FORMAT, "scenario": _jsonable(sc),
           "plan": _call_record("POST", PLAN_PATH, options, body, call), "edits": {}}
    for chain in sc.get("edits") or []:
        out["edits"][str(chain["chain"])] = _golden_chain(list(chain.get("ops") or []), call, bundle, options, factory)
    return _jsonable(out)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def compare_json(expected: Any, actual: Any, path: str = "", *, tol: float = GOLDEN_TOLERANCE,
                 ignore: Optional[Callable[[str], bool]] = None) -> list[tuple[str, Any, Any]]:
    """Differences between two JSON values as (path, expected, actual): dict keys must match, lists
    are compared position by position (order matters; a length difference is one entry plus the
    common prefix), numbers within `tol` (absolute), strings / booleans / null exactly. `ignore(path)`
    skips a subtree."""
    out: list[tuple[str, Any, Any]] = []

    def walk(e: Any, a: Any, p: str) -> None:
        if ignore is not None and ignore(p):
            return
        if isinstance(e, dict) and isinstance(a, dict):
            for k in e:
                if k not in a:
                    out.append((f"{p}.{k}", e[k], "<missing>"))
                else:
                    walk(e[k], a[k], f"{p}.{k}")
            for k in a:
                if k not in e:
                    out.append((f"{p}.{k}", "<missing>", a[k]))
        elif isinstance(e, (list, tuple)) and isinstance(a, (list, tuple)):
            if len(e) != len(a):
                out.append((f"{p}.<len>", len(e), len(a)))
            for i, (x, y) in enumerate(zip(e, a)):
                walk(x, y, f"{p}[{i}]")
        elif _is_number(e) and _is_number(a):
            if not abs(float(e) - float(a)) <= tol:
                out.append((p, e, a))
        elif type(e) is not type(a) or e != a:
            out.append((p, e, a))

    walk(expected, actual, path)
    return out


# Identity fields: they embed the bundle id (the build date) — compared only when the expected set is
# the loaded bundle's own; an expected set of ANOTHER bundle with the same content compares without them.
_IDENTITY_PATH = re.compile(r"(?:\.plan_id|\.versions\.catalog|\.catalog_version)$")
_PLAN_ID_PATH = ".plan.response.plan_id"


def _plan_id_problems(expected: dict, actual: dict, identity: bool) -> list[tuple[str, Any, Any]]:
    """``plan_id`` is a sha1 of the EXACT request echo + versions, so last-bit float noise of the echo
    (the city centre is a pandas mean: numpy 1.26 and 2.x differ by ~1e-14) changes it while every
    value still matches within the tolerance. It is therefore compared exactly only when the echo and
    the versions are bit-identical (and the identity fields count); it must always be the sha1 of
    the actual response's own request + versions."""
    from .present import plan_id_of

    a = (actual.get("plan") or {}).get("response") or {}
    e = ((expected or {}).get("plan") or {}).get("response") or {}
    if "plan_id" not in a:
        return []
    if a["plan_id"] != plan_id_of(a.get("request"), a.get("versions")):
        return [(_PLAN_ID_PATH, "sha1 of the response's request + versions", a["plan_id"])]
    if (identity and e.get("plan_id") != a["plan_id"] and e.get("request") == a.get("request")
            and e.get("versions") == a.get("versions")):
        return [(_PLAN_ID_PATH, e.get("plan_id"), a["plan_id"])]
    return []


def _flat_list(v: Any) -> bool:
    """A list of scalars or of scalar lists (ids, coordinates): always written on one line."""
    return isinstance(v, list) and all(
        not isinstance(x, (dict, list)) or (isinstance(x, list) and all(not isinstance(y, (dict, list)) for y in x))
        for x in v)


def golden_dumps(obj: Any, indent: int = 0, width: int = 120) -> str:
    """Diff-friendly JSON (what the golden files are written with): an object or list goes on one line
    when that line fits `width` (or, for lists, when it only holds scalars / scalar lists such as
    coordinates), else one member per line, indented by one space per level."""
    one = json.dumps(obj, ensure_ascii=False, allow_nan=False)
    if not isinstance(obj, (dict, list)) or not obj:
        return one
    if indent > 0 and (len(one) + indent <= width or _flat_list(obj)):
        return one
    pad = " " * (indent + 1)
    if isinstance(obj, dict):
        body = ",\n".join(f"{pad}{json.dumps(k, ensure_ascii=False)}: {golden_dumps(v, indent + 1, width)}"
                          for k, v in obj.items())
        return "{\n" + body + "\n" + " " * indent + "}"
    body = ",\n".join(f"{pad}{golden_dumps(v, indent + 1, width)}" for v in obj)
    return "[\n" + body + "\n" + " " * indent + "]"


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _environment() -> dict:
    import platform

    import numpy
    import pandas

    env = {"python": platform.python_version(), "numpy": numpy.__version__, "pandas": pandas.__version__}
    try:
        import pyarrow
        env["pyarrow"] = pyarrow.__version__
    except ImportError:  # pragma: no cover
        pass
    try:
        import sklearn
        env["scikit_learn"] = sklearn.__version__
    except ImportError:
        env["scikit_learn"] = None
    return env


def _load_scenarios(path: Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CliError(f"cannot read the scenarios {path}: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(s, dict) and "id" in s for s in data):
        raise CliError(f"{path}: expected a JSON list of scenarios with an 'id'")
    ids = [s["id"] for s in data]
    if len(set(ids)) != len(ids):
        raise CliError(f"{path}: duplicate scenario ids")
    return data


def _expected_sets(root: Path) -> dict[str, dict]:
    """{expected dir name: its index.json} under the expected root."""
    out = {}
    if root.is_dir():
        for d in sorted(root.iterdir()):
            idx = d / "index.json"
            if d.is_dir() and idx.is_file():
                try:
                    out[d.name] = json.loads(idx.read_text(encoding="utf-8"))
                except ValueError:
                    out[d.name] = {}
    return out


def _expected_dir_for(bundle, root: Path) -> tuple[Optional[Path], bool, str]:
    """(expected dir, identity fields comparable, note) for a bundle: its own set, else a set built
    from the same content (another bundle id) without the identity fields."""
    own = root / bundle.bundle_id
    if own.is_dir():
        return own, True, ""
    for name, index in _expected_sets(root).items():
        if index.get("content_sha256") == bundle.content_sha256:
            return root / name, False, (f"expected set {name} has the same content as {bundle.bundle_id}: "
                                        f"identity fields (plan_id, versions.catalog, catalog_version) not compared")
    return None, True, ""


def run_golden(bundles: list, scenarios: list[dict], expected_root: Path, *, only: Optional[set] = None,
               update: bool = False, verbose: bool = False, scenarios_file: Optional[Path] = None,
               out: Callable[[str], None] = print) -> int:
    """Compute every scenario (of `only`) on the bundle of its city and compare with (run) or write
    (update) ``<expected_root>/<bundle_id>/<scenario>.json``; print a table. Returns the exit code:
    run -> 0 when everything matched, 1 on any difference / missing expected output / error /
    scenario without a bundle; update -> 0 (1 when a scenario failed to compute)."""
    by_city = {str(b.city).casefold(): b for b in bundles}
    selected = [s for s in scenarios if not only or s["id"] in only]
    if only:
        unknown = sorted(set(only) - {s["id"] for s in scenarios})
        if unknown:
            raise CliError(f"unknown scenario ids: {', '.join(unknown)}")
    if not selected:
        raise CliError("no scenarios selected")
    targets: dict[str, tuple[Optional[Path], bool]] = {}      # bundle id -> (expected dir, identity compared)
    for b in bundles:
        if update:
            targets[b.bundle_id] = (expected_root / b.bundle_id, True)
        else:
            d, identity, note = _expected_dir_for(b, expected_root)
            targets[b.bundle_id] = (d, identity)
            if note:
                out(f"note: {note}")
        out(f"golden {'update' if update else 'run'}: bundle {b.bundle_id} ({b.city}) · expected "
            f"{targets[b.bundle_id][0] or '(none)'} · routing: straight-line estimate · {GOLDEN_OPTIONS}")
    out(f"{'scenario':26s} {'status':8s} {'plan':13s} {'var':>3s} {'calls':>5s} {'diffs':>6s} {'time':>6s}")
    counts: dict[str, int] = {}
    written: dict[str, dict[str, dict]] = {}                    # bundle id -> {scenario id: index entry}
    details: list[str] = []
    t_all = time.perf_counter()
    for sc in selected:
        t = time.perf_counter()
        bundle = by_city.get(str(sc.get("city", "")).casefold())
        if bundle is None:
            row = {"status": "SKIP", "details": [f"{sc['id']}: no bundle for city {sc.get('city')!r}"]}
        else:
            exp_dir, identity = targets[bundle.bundle_id]
            row = _golden_one(sc, bundle, exp_dir, identity, update, verbose)
            if row.get("written"):
                written.setdefault(bundle.bundle_id, {})[sc["id"]] = row["written"]
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        details += row.get("details", [])
        out(f"{sc['id']:26s} {row['status']:8s} {row.get('plan', '-'):13s} {row.get('variants', 0):>3d} "
            f"{row.get('calls', 0):>5d} {row.get('diffs', 0):>6d} {time.perf_counter() - t:5.2f}s")
    for line in details:
        out("  " + line)
    for b in bundles:
        if written.get(b.bundle_id):
            _write_index(targets[b.bundle_id][0], b, written[b.bundle_id], scenarios_file)
    summary = ", ".join(f"{v} {k.lower()}" for k, v in sorted(counts.items()))
    out(f"{len(selected)} scenarios: {summary} ({time.perf_counter() - t_all:.1f} s)")
    if update:
        return 1 if counts.get("ERROR") or counts.get("SKIP") else 0
    return 0 if counts.get("PASS", 0) == len(selected) else 1


def golden_diffs(expected: dict, actual: dict, identity: bool = True) -> list[tuple[str, Any, Any]]:
    """Differences between an expected golden output and a fresh one: ``compare_json`` with the golden
    tolerance, ``plan_id`` per ``_plan_id_problems``, minus TimePoint clock flips at a half minute
    (``_is_half_minute_flip``); `identity` False skips the fields that embed the bundle id."""
    if identity:
        def ignore(p: str) -> bool:
            return p == _PLAN_ID_PATH
    else:
        def ignore(p: str) -> bool:
            return p == _PLAN_ID_PATH or bool(_IDENTITY_PATH.search(p))
    diffs = [d for d in compare_json(expected, actual, ignore=ignore)
             if not _is_half_minute_flip(d, expected, actual)]
    return diffs + _plan_id_problems(expected, actual, identity)


_PATH_TOKEN = re.compile(r"\.([^.\[]+)|\[(\d+)\]")


def _at(obj: Any, path: str) -> Any:
    """The value at a ``compare_json`` path (".a.b[2].c"), or None."""
    for key, idx in _PATH_TOKEN.findall(path):
        try:
            obj = obj[int(idx)] if idx else obj[key]
        except (KeyError, IndexError, TypeError):
            return None
    return obj


def _is_half_minute_flip(diff: tuple, expected: Any, actual: Any) -> bool:
    """A TimePoint ``{"offset_min", "local"}`` whose clock differs by exactly one minute while its offset
    is within the tolerance of a half minute: ``local`` rounds the offset (Python ``round``), so last-bit
    float noise of another numpy / BLAS build may round it either way (e.g. 217.49999999999974 vs
    217.50000000000003). Both clocks are then correct."""
    import datetime as _dt

    path, e, a = diff
    if not (path.endswith(".local") and isinstance(e, str) and isinstance(a, str)):
        return False
    parent = path[: -len(".local")]
    pe, pa = _at(expected, parent), _at(actual, parent)
    if not (isinstance(pe, dict) and isinstance(pa, dict)):
        return False
    offsets = [x.get("offset_min") for x in (pe, pa)]
    if not all(_is_number(o) for o in offsets) or abs(offsets[0] - offsets[1]) > GOLDEN_TOLERANCE:
        return False
    try:
        delta = abs(_dt.datetime.strptime(e, "%Y-%m-%dT%H:%M") - _dt.datetime.strptime(a, "%Y-%m-%dT%H:%M"))
    except ValueError:
        return False
    return delta == _dt.timedelta(minutes=1) and abs(offsets[1] % 1.0 - 0.5) <= GOLDEN_TOLERANCE


def _golden_one(sc: dict, bundle, exp_dir: Optional[Path], identity: bool, update: bool, verbose: bool) -> dict:
    """Run (compare) or update (write) one scenario: {"status", "plan", "variants", "calls", "diffs",
    "details"[, "written": index entry]}."""
    sid = sc["id"]
    try:
        actual = golden_scenario(sc, bundle)
    except Exception as exc:                  # report and continue: one broken scenario must not hide the rest
        return {"status": "ERROR", "details": [f"{sid}: {type(exc).__name__}: {exc}"]}
    plan = actual["plan"]
    outcome = plan["response"].get("status") or plan["response"].get("error", {}).get("code")
    row: dict[str, Any] = {"plan": f"{plan['status']} {outcome}",
                           "variants": len(plan["response"].get("variants") or []),
                           "calls": sum(1 for steps in actual["edits"].values() for st in steps if st["call"]),
                           "details": []}
    path = exp_dir / f"{sid}.json" if exp_dir is not None else None
    expected = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.is_file() else None
    if update:
        text = golden_dumps(actual) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        row["written"] = {"file": path.name, "sha256": _sha256_text(text), "plan_status": plan["status"],
                          "variants": row["variants"], "edit_calls": row["calls"]}
        diffs = golden_diffs(expected, actual) if expected is not None else []
        row.update(status="NEW" if expected is None else ("CHANGED" if diffs else "SAME"), diffs=len(diffs))
        return row
    if expected is None:
        row.update(status="MISSING",
                   details=[f"{sid}: no expected output {path}" if path else f"{sid}: no expected set"])
        return row
    diffs = golden_diffs(expected, actual, identity)
    row.update(status="DIFF" if diffs else "PASS", diffs=len(diffs))
    for p, e, a in diffs[: (None if verbose else 8)]:
        row["details"].append(f"{sid}{p}: expected {json.dumps(e, ensure_ascii=False)[:150]} "
                              f"got {json.dumps(a, ensure_ascii=False)[:150]}")
    if not verbose and len(diffs) > 8:
        row["details"].append(f"{sid}: ... {len(diffs) - 8} more (-v shows all)")
    return row


def _write_index(exp_dir: Path, bundle, written: dict, scenarios_file: Optional[Path]) -> None:
    """index.json of an expected set: bundle identity + fingerprints, versions, how the outputs were
    made, and one entry per scenario file (merged with the existing index for partial updates)."""
    import datetime as _dt

    from .version import ALGORITHM_VERSION, API_VERSION, BUNDLE_SCHEMA_VERSION, INTEREST_VERSION

    path = exp_dir / "index.json"
    old = {}
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
    entries = dict(old.get("scenarios") or {})
    entries.update(written)
    index = {
        "format": GOLDEN_FORMAT,
        "bundle_id": bundle.bundle_id,
        "city": bundle.city,
        "content_sha256": bundle.content_sha256,
        "catalog_sha256": bundle.catalog_sha256,
        "interest_fingerprint": bundle.interest_fingerprint,
        "versions": {"api": API_VERSION, "algorithm": ALGORITHM_VERSION, "interest": INTEREST_VERSION,
                     "bundle_schema": BUNDLE_SCHEMA_VERSION},
        "routing": "estimate: straight-line core.RoutingProvider (forced; no router, no network)",
        "options": dict(GOLDEN_OPTIONS),
        "photo_base_url": None,
        "tolerance": {"numbers_abs": GOLDEN_TOLERANCE, "strings": "exact", "lists": "ordered"},
        "scenarios_file": (str(scenarios_file.name) if scenarios_file else None),
        "scenarios_sha256": (_sha256_text(Path(scenarios_file).read_text(encoding="utf-8"))
                             if scenarios_file and Path(scenarios_file).is_file() else None),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": _environment(),
        "scenarios": {k: entries[k] for k in sorted(entries)},
    }
    path.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Golden acceptance against a RUNNING service (golden run --url)
# --------------------------------------------------------------------------- #
# (method, path, query, body, request_id) -> (HTTP status, response JSON)
Transport = Callable[[str, str, dict, Optional[dict], Optional[str]], tuple]


def http_transport(base_url: str, timeout: float = GOLDEN_HTTP_TIMEOUT_S,
                   headers: Optional[dict] = None) -> Transport:
    """Calls to the service at `base_url` with the standard library (no proxy: an internal URL). A call
    returns ``(HTTP status, parsed JSON)`` -- also for 4xx / 5xx answers; a non-JSON body becomes
    ``{"_raw": <text>}``. Network failures raise ``OSError`` (``urllib.error.URLError``)."""
    import urllib.error
    import urllib.parse
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = base_url.rstrip("/")

    def call(method: str, path: str, query: dict, body: Optional[dict], request_id: Optional[str] = None):
        url = base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs = {"Accept": "application/json", **(headers or {})}
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        if request_id:
            hdrs["X-Request-Id"] = request_id
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with opener.open(req, timeout=timeout) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            return status, json.loads(raw.decode("utf-8"))
        except ValueError:
            return status, {"_raw": raw.decode("utf-8", "replace")[:500]}

    return call


def _service_meta(call: Transport, base_url: str, wait_s: float, out: Callable[[str], None]) -> dict:
    """GET /v1/meta of the service once it is ready (polled every second for up to `wait_s`)."""
    deadline = time.monotonic() + max(0.0, float(wait_s))
    last = "no answer"
    said = False
    while True:
        try:
            status, meta = call("GET", "/v1/meta", {}, None, None)
            if status == 200 and isinstance(meta, dict) and meta.get("service") == "walk-planner":
                if meta.get("ready"):
                    return meta
                last = f"not ready (state {meta.get('state')!r})"
            else:
                last = f"GET /v1/meta answered {status}: not the walk-planner service?"
        except OSError as exc:
            last = f"cannot connect ({getattr(exc, 'reason', exc)})"
        if time.monotonic() >= deadline:
            raise CliError(f"{base_url}: {last}")
        if not said:
            out(f"waiting up to {wait_s:g} s for {base_url} ({last}) ...")
            said = True
        time.sleep(1.0)


def _http_expected_dir(bundle: dict, root: Path) -> tuple[Optional[Path], bool, str]:
    """`_expected_dir_for` for a bundle as /v1/meta describes it (bundle_id, content_sha256)."""
    own = root / str(bundle.get("bundle_id"))
    if own.is_dir():
        return own, True, ""
    for name, index in _expected_sets(root).items():
        if index.get("content_sha256") and index.get("content_sha256") == bundle.get("content_sha256"):
            return root / name, False, (f"expected set {name} has the same content as {bundle.get('bundle_id')}: "
                                        f"identity fields (plan_id, versions.catalog, catalog_version) not compared")
    return None, True, ""


def _replay(call: Transport, record: dict, request_id: str) -> tuple:
    method, path = record["call"].split(" ", 1)
    return call(method, path, dict(record.get("options") or {}), record.get("body"), request_id)


def run_golden_http(base_url: str, scenarios: list[dict], expected_root: Path, *, only: Optional[set] = None,
                    verbose: bool = False, out: Callable[[str], None] = print, wait_s: float = 60.0,
                    timeout: float = GOLDEN_HTTP_TIMEOUT_S, transport: Optional[Transport] = None) -> int:
    """Acceptance test of a RUNNING service: replay every recorded API call of ``<expected_root>/<bundle_id>/``
    (the plan call and the /schedule and /insert calls of every edit chain, with their recorded options and
    bodies) against `base_url` and compare status + response with the golden rules (``golden_diffs``:
    numbers within 1e-6, strings exact, plan_id by its own request). The bundles come from ``GET /v1/meta``;
    a bundle without its own expected set uses one with the same content (identity fields not compared).

    The golden outputs use the straight-line estimate and no photo base URL: run the service with
    ``WALK_ROUTER_URL``, ``ORS_API_KEY`` and ``PHOTO_BASE_URL`` set EMPTY (under compose an unset
    ``WALK_ROUTER_URL`` defaults to osrm-foot) -- a warning says so when /v1/meta shows
    street routing or photo URLs (the comparison then reports the differences). Returns 0 when every
    selected scenario passed, 1 otherwise (a difference, a missing expected file, a city the service does
    not serve). `transport` replaces the HTTP client (tests)."""
    call = transport or http_transport(base_url, timeout)
    meta = _service_meta(call, base_url, wait_s, out)
    routing = meta.get("routing") or {}
    chain = [str(c) for c in routing.get("chain") or []]
    if routing.get("configured") or any(c != "estimate" for c in chain):
        out(f"WARNING: {base_url} routes through streets (chain {' -> '.join(chain) or '?'}): the golden outputs "
            "were made with the straight-line estimate, so segment times, distances and geometry WILL differ. "
            "For the acceptance run restart the service with WALK_ROUTER_URL= and ORS_API_KEY= set EMPTY "
            "(under compose an unset WALK_ROUTER_URL means osrm-foot).")
    photo = (meta.get("settings") or {}).get("photo_base_url")
    if photo:
        out(f"WARNING: PHOTO_BASE_URL is set on {base_url} ({photo}): photo urls will differ from the golden "
            "outputs (null). Set it empty for the acceptance run.")
    selected = [s for s in scenarios if not only or s["id"] in only]
    if only:
        unknown = sorted(set(only) - {s["id"] for s in scenarios})
        if unknown:
            raise CliError(f"unknown scenario ids: {', '.join(unknown)}")
    if not selected:
        raise CliError("no scenarios selected")
    targets: dict[str, tuple] = {}
    for b in meta.get("bundles") or []:
        exp_dir, identity, note = _http_expected_dir(b, expected_root)
        targets[str(b.get("city", "")).casefold()] = (b, exp_dir, identity)
        if note:
            out(f"note: {note}")
        out(f"golden run --url {base_url}: bundle {b.get('bundle_id')} ({b.get('city')}) · expected "
            f"{exp_dir or '(none)'} · service {meta.get('version')} (git {meta.get('git_sha') or '?'}) · "
            f"routing {' -> '.join(chain) or '?'}")
    out(f"{'scenario':26s} {'status':8s} {'plan':13s} {'var':>3s} {'calls':>5s} {'diffs':>6s} {'time':>6s}")
    counts: dict[str, int] = {}
    details: list[str] = []
    t_all = time.perf_counter()
    for sc in selected:
        t = time.perf_counter()
        sid = sc["id"]
        row: dict[str, Any] = {"details": []}
        target = targets.get(str(sc.get("city", "")).casefold())
        path = target[1] / f"{sid}.json" if target is not None and target[1] is not None else None
        if target is None:
            row.update(status="SKIP", details=[f"{sid}: the service has no bundle for city {sc.get('city')!r}"])
        elif path is None or not path.is_file():
            row.update(status="MISSING", details=[f"{sid}: no expected output " + (str(path) if path else "set")])
        else:
            expected = json.loads(path.read_text(encoding="utf-8"))
            actual = copy.deepcopy(expected)
            try:
                actual["plan"]["status"], actual["plan"]["response"] = _replay(call, expected["plan"], f"golden-{sid}-0")
                n = 0
                for chain_id, steps in expected["edits"].items():
                    for i, step in enumerate(steps):
                        if step.get("call") is not None:
                            n += 1
                            st, body = _replay(call, step["call"], f"golden-{sid}-{chain_id}{i}")
                            actual["edits"][chain_id][i]["call"]["status"] = st
                            actual["edits"][chain_id][i]["call"]["response"] = body
            except OSError as exc:
                row.update(status="ERROR", details=[f"{sid}: {type(exc).__name__}: {exc}"])
            else:
                resp = actual["plan"]["response"] if isinstance(actual["plan"]["response"], dict) else {}
                outcome = resp.get("status") or (resp.get("error") or {}).get("code")
                diffs = golden_diffs(expected, actual, target[2])
                row.update(status="DIFF" if diffs else "PASS", diffs=len(diffs), calls=n,
                           plan=f"{actual['plan']['status']} {outcome}", variants=len(resp.get("variants") or []))
                for p_, e, a in diffs[: (None if verbose else 8)]:
                    row["details"].append(f"{sid}{p_}: expected {json.dumps(e, ensure_ascii=False)[:150]} "
                                          f"got {json.dumps(a, ensure_ascii=False)[:150]}")
                if not verbose and len(diffs) > 8:
                    row["details"].append(f"{sid}: ... {len(diffs) - 8} more (-v shows all)")
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        details += row["details"]
        out(f"{sid:26s} {row['status']:8s} {row.get('plan', '-'):13s} {row.get('variants', 0):>3d} "
            f"{row.get('calls', 0):>5d} {row.get('diffs', 0):>6d} {time.perf_counter() - t:5.2f}s")
    for line in details:
        out("  " + line)
    summary = ", ".join(f"{v} {k.lower()}" for k, v in sorted(counts.items()))
    out(f"{len(selected)} scenarios: {summary} ({time.perf_counter() - t_all:.1f} s) against {base_url}")
    return 0 if counts.get("PASS", 0) == len(selected) else 1


def _golden_paths(args) -> tuple[Path, Path]:
    gdir = default_golden_dir()
    scen = Path(args.scenarios).expanduser() if args.scenarios else (gdir / "scenarios.json" if gdir else None)
    root = Path(args.expected_root).expanduser() if args.expected_root else (gdir / "expected" if gdir else None)
    if scen is None or root is None:
        raise CliError("no golden directory next to the package: pass --scenarios and --expected-root")
    return scen, root


def cmd_golden_run(args, update: bool = False) -> int:
    scen_path, root = _golden_paths(args)
    scenarios = _load_scenarios(scen_path)
    url = getattr(args, "url", None)
    if url:
        if args.bundle or args.city or args.no_verify:
            raise CliError("--url replays against a running service: --bundle / --city / --no-verify do not apply")
        if not re.match(r"^https?://", url):
            raise CliError(f"--url: expected http(s)://host:port, got {url!r}")
        only = {s for s in (args.only or "").split(",") if s.strip()} or None
        return run_golden_http(url, scenarios, root, only=only, verbose=args.verbose, wait_s=args.wait,
                               timeout=args.timeout)
    bundles = open_bundles(args.bundle, verify=not args.no_verify)
    if args.city:
        bundles = [b for b in bundles if str(b.city).casefold() == args.city.casefold()]
        if not bundles:
            raise CliError(f"no bundle for city {args.city!r}")
    only = {s for s in (args.only or "").split(",") if s.strip()} or None
    return run_golden(bundles, scenarios, root, only=only, update=update, verbose=args.verbose,
                      scenarios_file=scen_path)


def cmd_golden_list(args) -> int:
    scen_path, root = _golden_paths(args)
    scenarios = _load_scenarios(scen_path)
    base_dir = scen_path.parent / "baseline_v0"
    print(f"scenarios: {scen_path} ({len(scenarios)})")
    print(f"{'id':26s} {'shape':8s} {'style':7s} {'window':13s} {'slots':42s} {'must':>4s} {'fav':>4s} "
          f"{'chains':6s} baseline_v0")
    for s in scenarios:
        slots = ",".join(x["activity"] if isinstance(x, dict) else str(x) for x in s.get("slots") or [])
        window = f"{s.get('start_time', '')}-{s.get('end_time', '')}" + ("+1" if s.get("end_day_offset") else "")
        chains = ",".join(c["chain"] for c in s.get("edits") or []) or "-"
        base = "yes" if (base_dir / f"{s['id']}.json").is_file() else "no"
        print(f"{s['id']:26s} {s.get('shape', ''):8s} {s.get('style', ''):7s} {window:13s} {slots[:42]:42s} "
              f"{len(s.get('must_visit_place_ids') or []):>4d} {len(s.get('favourite_place_ids') or []):>4d} "
              f"{chains:6s} {base}")
    sets = _expected_sets(root)
    print(f"expected sets under {root}: {len(sets)}")
    for name, idx in sets.items():
        files = len([p for p in (root / name).glob("*.json") if p.name != "index.json"])
        print(f"  {name}: algorithm {(idx.get('versions') or {}).get('algorithm')} · {files} files · "
              f"generated {idx.get('generated_at')} · content {str(idx.get('content_sha256'))[:12]}")
    return 0


# --------------------------------------------------------------------------- #
# serve
# --------------------------------------------------------------------------- #
def cmd_serve(args) -> int:
    """exec uvicorn on the service factory (replaces this process)."""
    import importlib.util

    if importlib.util.find_spec("uvicorn") is None:
        raise CliError("uvicorn is not installed: pip install 'sloco-walk-planner[service]'")
    try:
        found = importlib.util.find_spec("walk_planner.service.app") is not None
    except ModuleNotFoundError:
        found = False
    if not found:
        raise CliError("walk_planner.service.app is not available in this installation")
    env = dict(os.environ)
    spec = args.bundle or default_bundle_spec()          # --bundle, $WALK_BUNDLE_DIR, the research data's bundles
    if spec:
        env["WALK_BUNDLE_DIR"] = spec
    root = str(_PKG_ROOT)
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    argv = [sys.executable, "-m", "uvicorn", "walk_planner.service.app:create_app", "--factory",
            "--host", args.host, "--port", str(args.port)]
    if args.reload:
        argv.append("--reload")
    if args.workers:
        argv += ["--workers", str(args.workers)]
    if args.log_level:
        argv += ["--log-level", args.log_level]
    sys.stdout.flush()
    os.execvpe(sys.executable, argv, env)
    return 0  # pragma: no cover  (exec does not return)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def _common(bundle: bool = True, output: bool = True) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    if bundle:
        p.add_argument("--bundle", default=None,
                       help="bundle dir, 'a,b', or a root of bundles (default: $WALK_BUNDLE_DIR / research data)")
        p.add_argument("--city", default=None, help="the city to use when several bundles are loaded")
        p.add_argument("--no-verify", action="store_true", help="skip the bundle's sha256 verification")
    if output:
        p.add_argument("--compact", action="store_true", help="compact JSON output")
    return p


def _api_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--lang", choices=LANGS, default="ru", help="message language (default ru)")
    p.add_argument("--geometry", choices=GEOMETRIES, default="geojson", help="segment geometry format")
    p.add_argument("--pretty", action="store_true", help="the dashboard's Russian summary instead of JSON")
    p.add_argument("--links", action="store_true", help="--pretty: also the per-leg navigation links")
    p.add_argument("--routing", choices=("auto", "estimate"), default="auto",
                   help="auto: the service's chain from the environment; estimate: straight lines only")
    p.add_argument("--photo-base-url", default=None, help="PHOTO_BASE_URL for photo urls (default: env)")


def _edit_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--request", default=None, help="the request body JSON file ('-' = stdin)")
    p.add_argument("--from-plan", default=None, help="a saved plan (or edit) response: its request + variant sequence")
    p.add_argument("--variant", type=int, default=0, help="--from-plan: the variant index (default 0)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="walk-planner", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Run '<command> --help' for the options of a command.")
    from .version import ALGORITHM_VERSION

    ap.add_argument("--version", action="version", version=f"walk-planner {ALGORITHM_VERSION}")
    sub = ap.add_subparsers(dest="command", metavar="command")
    sub.required = True

    p = sub.add_parser("plan", parents=[_common()], help="plan walk variants (POST /v1/walks/plan)")
    _api_options(p)
    p.add_argument("--request", default=None, help="a plan request JSON file ('-' = stdin); flags override its fields")
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: today in the city)")
    p.add_argument("--start", default=None, help="departure HH:MM (default 10:00)")
    p.add_argument("--end", default=None, help="end HH:MM (default: start + 4 h)")
    p.add_argument("--end-day-offset", type=int, choices=(0, 1), default=None, help="1 = the end is on the next day")
    p.add_argument("--slots", default=None, help="activity codes in order, e.g. sight,coffee:15,park,food:90")
    p.add_argument("--shape", default=None, help="loop | one_way | free")
    p.add_argument("--style", default=None, help="max | chill | scenic")
    p.add_argument("--start-latlon", default=None, help="start point LAT,LON (default: the city centre)")
    p.add_argument("--start-place", default=None, help="start at a catalog place (place_id)")
    p.add_argument("--must", action="append", default=None, help="must-visit place ids (comma list, repeatable)")
    p.add_argument("--fav", action="append", default=None, help="favourite place ids (comma list, repeatable)")
    p.add_argument("--wtg", action="append", default=None, help="want-to-go place ids (comma list, repeatable)")
    p.add_argument("--strength", type=float, default=None, help="personalization strength 0..1 (default 0.5)")
    p.add_argument("--variants", type=int, default=None, help="1..5 (default 3)")
    p.add_argument("--radius", type=float, default=None, help="search radius km (default 2.5)")
    p.add_argument("--top-k", type=int, default=None, help="candidates per slot (bench; default 8)")
    p.add_argument("--no-fill", action="store_true", help="do not add on-the-way stops")
    p.add_argument("--known-hours-only", action="store_true", help="only places with known opening hours")
    p.add_argument("--debug", action="store_true", help="include the debug block (search area, candidates)")
    p.add_argument("--timing", action="store_true", help="print the planning time to stderr")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("schedule", parents=[_common()], help="re-time an edited order (POST /v1/walks/schedule)")
    _api_options(p)
    _edit_options(p)
    p.add_argument("--edit", action="append", default=None,
                   help="edit the sequence first: move:FROM:TO | remove:I | dwell:I:MIN (repeatable, in order)")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("insert", parents=[_common()], help="add a place at its best position (POST /v1/walks/insert)")
    _api_options(p)
    _edit_options(p)
    p.add_argument("--place", default=None, help="the place_id to add")
    p.add_argument("--allow-temporarily-closed", action="store_true", help="accept a temporarily closed place")
    p.add_argument("--dwell", type=float, default=None, help="visit length in minutes (default: estimated)")
    p.set_defaults(func=cmd_insert)

    p = sub.add_parser("search", parents=[_common()], help="search places by name (GET /v1/walks/places/search)")
    p.add_argument("query")
    p.add_argument("--near", default=None, help="LAT,LON: prefer places near this point")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--include-closed", action="store_true", help="include closed_forever places")
    p.add_argument("--pretty", action="store_true", help="one line per place instead of JSON")
    p.add_argument("--lang", choices=LANGS, default="ru")
    p.add_argument("--photo-base-url", default=None)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("place", parents=[_common()], help="a place's detail view (GET /v1/walks/places/{id})")
    p.add_argument("place_id")
    p.add_argument("--lang", choices=LANGS, default="ru")
    p.add_argument("--photo-base-url", default=None)
    p.set_defaults(func=cmd_place)

    p = sub.add_parser("config", parents=[_common()], help="the form configuration (GET /v1/walks/config)")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("interest", parents=[_common()], help="inspect the taste model (favourites -> interest)")
    p.add_argument("--fav", action="append", default=None, help="favourite place ids (comma list, repeatable)")
    p.add_argument("--wtg", action="append", default=None, help="want-to-go place ids (comma list, repeatable)")
    p.add_argument("--strength", type=float, default=0.5, help="blend strength 0..1 (default 0.5)")
    p.add_argument("--top", type=int, default=20, help="places to show (default 20)")
    p.add_argument("--theme", default=None, help="only this catalog theme or theme_group (e.g. culture_sights)")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_interest)

    p = sub.add_parser("route", parents=[_common(bundle=False)], help="probe the routing chain on coordinates")
    p.add_argument("--coords", required=True, help='"lat,lon;lat,lon[;...]"')
    p.add_argument("--routing", choices=("auto", "estimate"), default="auto")
    p.add_argument("--geometry", choices=GEOMETRIES, default="geojson")
    p.add_argument("--json", action="store_true", help="JSON output (legs with geometry, events, status)")
    p.set_defaults(func=cmd_route)

    pb = sub.add_parser("bundle", help="build / validate / inspect data bundles")
    bsub = pb.add_subparsers(dest="bundle_command", metavar="bundle_command")
    bsub.required = True
    p = bsub.add_parser("build", help="build a city bundle (atomic; never overwrites)")
    p.add_argument("--out-root", default=None, help="directory the bundle directory is created in")
    p.add_argument("--data-dir", default=None,
                   help="research data dir: fills every input from its layout (locations_<slug>_all.csv, ...); "
                        "default --out-root <data-dir>/walk_bundles")
    p.add_argument("--city-slug", default=None, help="bundle id prefix (default: from the city name)")
    p.add_argument("--city", default=None, help="the city (default: the catalog's only city)")
    p.add_argument("--timezone", default=None, help="IANA timezone (default: known per city)")
    p.add_argument("--catalog-csv", default=None)
    p.add_argument("--photo-manifest-csv", default=None)
    p.add_argument("--text-npy", default=None)
    p.add_argument("--text-meta-csv", default=None)
    p.add_argument("--image-npy", default=None)
    p.add_argument("--image-meta", default=None)
    p.add_argument("--photos-root", default=None, help="the photos_cid directory (drops manifest rows without a file)")
    p.add_argument("--max-photos", type=int, default=10)
    p.add_argument("--no-photos-check", action="store_true", help="do not check photo files (keep every manifest row)")
    p.add_argument("--no-interest", action="store_true", help="no taste artifacts (no personalization)")
    p.add_argument("--no-validate", action="store_true", help="skip the deep validation after the build")
    p.set_defaults(func=cmd_bundle_build)
    p = bsub.add_parser("validate", parents=[_common(bundle=True)], help="validate bundle(s); exit 1 if invalid")
    p.add_argument("path", nargs="?", default=None, help="bundle dir / 'a,b' / root (default --bundle)")
    p.add_argument("--shallow", action="store_true", help="manifest, hashes, schema, alignment only (no row checks)")
    p.add_argument("--photos-root", default=None, help="also count existing photo files under this photos_cid dir")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_bundle_validate)
    p = bsub.add_parser("info", parents=[_common(bundle=True)], help="summarise bundle manifest(s)")
    p.add_argument("path", nargs="?", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_bundle_info)

    pg = sub.add_parser("golden", help="golden expected API outputs")
    gsub = pg.add_subparsers(dest="golden_command", metavar="golden_command")
    gsub.required = True
    for name, helptext in (("run", "compare with the expected outputs (exit 1 on any difference)"),
                           ("update", "regenerate the expected outputs of the loaded bundle")):
        p = gsub.add_parser(name, parents=[_common(output=False)], help=helptext)
        p.add_argument("--scenarios", default=None, help="scenarios JSON (default golden/scenarios.json)")
        p.add_argument("--expected-root", default=None, help="expected root (default golden/expected)")
        p.add_argument("--only", default=None, help="comma-separated scenario ids")
        p.add_argument("-v", "--verbose", action="store_true", help="print every difference")
        if name == "run":
            p.add_argument("--url", default=None,
                           help="replay the expected API calls against a RUNNING service at this base URL "
                                "(http://host:port) instead of computing them; run the service with WALK_ROUTER_URL, "
                                "ORS_API_KEY and PHOTO_BASE_URL set EMPTY")
            p.add_argument("--wait", type=float, default=60.0,
                           help="--url: seconds to wait for the service to be ready (default 60)")
            p.add_argument("--timeout", type=float, default=GOLDEN_HTTP_TIMEOUT_S,
                           help="--url: seconds per call (default 120)")
        p.set_defaults(func=cmd_golden_run if name == "run" else (lambda a: cmd_golden_run(a, update=True)))
    p = gsub.add_parser("list", help="list the scenarios and the expected sets")
    p.add_argument("--scenarios", default=None)
    p.add_argument("--expected-root", default=None)
    p.set_defaults(func=cmd_golden_list)

    p = sub.add_parser("serve", help="run the service (uvicorn walk_planner.service.app:create_app --factory)")
    p.add_argument("--bundle", default=None, help="sets WALK_BUNDLE_DIR for the service")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--log-level", default=None)
    p.set_defaults(func=cmd_serve)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point (console script ``walk-planner``); returns the exit code."""
    from .bundle import BundleError

    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    args.argv = ["walk-planner"] + argv
    try:
        return int(args.func(args) or 0)
    except (CliError, BundleError) as exc:
        _eprint(f"error: {exc}")
        return 2
    except BrokenPipeError:                     # e.g. piped into head
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
