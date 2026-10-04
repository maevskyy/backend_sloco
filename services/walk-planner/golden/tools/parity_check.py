#!/usr/bin/env python3
"""Golden PARITY CHECK of the Walk Planner package against the pre-refactor baseline (baseline_v0).

Runs the NEW, Streamlit-free pipeline — ``CityCatalog`` -> ``normalize_params`` -> ``build_plan``
(cold start, straight-line ``RoutingProvider``) and the stateless editing operations ``schedule`` /
``insert_place`` — over ``../scenarios.json``, builds digests in EXACTLY the format of
``capture_baseline.py`` (render strings from ``present``'s Russian helpers, navigation from
``present.navigation_ru``) and compares them with ``../baseline_v0/<id>.json``:

  * floats: absolute tolerance 2e-6 (the baseline rounds to 6 decimals); strings, ints, bools: exact;
  * ``candidates.slot`` / ``candidates.extra``: positional, else as multisets (tied-interest rows may
    be ordered differently by an unstable sort — see baseline_v0/README.md);
  * allowed differences (reported, not failures):
      (a) bug 1 — segment ``from_order`` / ``to_order`` of shape "free" plans: the baseline's
          off-by-one ``(i-1, i)`` or the fixed ``(i, i+1)`` (whichever the core currently gives);
      (b) bug 2 — scenario S19_must_closed: the closed_forever must-visit is dropped (with a
          must_visit_closed_forever note) and the temporarily_closed one stays pinned with a
          place_temporarily_closed warning; the new behaviour is printed. GUARDED: only the notes,
          the must-visit candidates (+ ``request.n_must_visit``) and the variants' plans / renders /
          navigation may differ, and only as bug 2 explains — the must list is the baseline's minus
          the catalog's closed_forever places, the notes are the baseline's plus exactly the
          must_visit_closed_forever note naming them, no variant routes a closed_forever place and
          every kept must-visit is pinned in every variant. Slot / extra candidates, the request,
          the inputs, the variant count and dropped slots must match like everywhere else;
      (env) the ``inputs_resolved.*_exact`` repr strings of the city centre when they differ by
          < 1e-9 (a pandas mean: its last bits depend on the numpy build — numpy 1.26 vs 2.4 differ
          by ~1e-14); the outputs that depend on it are compared as usual.
    Everything else must match.

Scenarios marked ``"baseline_v0": false`` in scenarios.json (P01 / P02: personalization is new in v1, the
pre-refactor dashboard cannot produce them) are skipped and listed as SKIP: ``golden/expected`` alone pins them.

The edit chains replay the dashboard's editor on the client side of the stateless API: the variant's
``sequence`` (EditStops) and the «Не заходить» bin are kept here, every applied op re-schedules
through ``schedule`` (exact order) or ``insert_place`` (add), and the minutes editor's rounding of
all stops is replicated (as in capture_baseline.run_chain).

Hermetic when run as a script: every socket connect / DNS lookup raises; no bytecode is written;
nothing is modified except files under ``--write DIR`` (optional digests of the new code, same
format as the baseline). Importing the module (tests) has no such side effects.

Usage (from the repo root):
    PYTHONDONTWRITEBYTECODE=1 ../../venv/bin/python services/walk_planner/golden/tools/parity_check.py \\
        [--only S01_default,S19_must_closed] [--source csv|bundle] [--write DIR] [-v]
Exit code: 0 = only allowed differences, 1 = unexpected differences, 2 = setup error.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import socket
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional

sys.dont_write_bytecode = True


def _network_disabled(*_args, **_kwargs):
    raise RuntimeError("network access is disabled during the Walk Planner parity check")


def _hermetic() -> None:
    """No network for the rest of the process (the check must never route through a real router)."""
    socket.socket.connect = _network_disabled
    socket.socket.connect_ex = _network_disabled
    socket.create_connection = _network_disabled
    socket.getaddrinfo = _network_disabled


HERE = Path(__file__).resolve()
GOLDEN_DIR = HERE.parents[1]                         # services/walk_planner/golden
PKG_ROOT = HERE.parents[2]                           # services/walk_planner
# sloco_recommendation_system (None when this copy is vendored without the research repo around it)
REPO = HERE.parents[4] if len(HERE.parents) > 4 else None
CATALOG_CSV = (REPO / "recommendation_system" / "ai_location_recommender" / "data" / "locations_bucharest_all.csv"
               if REPO is not None else None)
DEFAULT_SCENARIOS = GOLDEN_DIR / "scenarios.json"
DEFAULT_BASELINE = GOLDEN_DIR / "baseline_v0"
TOL = 2e-6
BUG2_SCENARIOS = {"S19_must_closed"}

sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402

from walk_planner.catalog import CityCatalog, to_bundle_frame  # noqa: E402
from walk_planner.core import RoutingProvider  # noqa: E402
from walk_planner.messages import Message  # noqa: E402
from walk_planner.pipeline import (  # noqa: E402
    PlannerInputError,
    build_plan,
    edit_stops,
    insert_place,
    normalize_params,
    schedule,
)
from walk_planner.present import hours_label_ru, navigation_ru, render_variant_ru  # noqa: E402
from walk_planner.slots import LABEL_RU_BY_ACTIVITY, STYLES  # noqa: E402


# --------------------------------------------------------------------------------------------- #
# Digests (the format of capture_baseline.py)
# --------------------------------------------------------------------------------------------- #
def scenario_request(sc: dict) -> dict:
    """A golden scenario as an API plan request (codes; start "city_center" | {lat, lon} | {place_id})."""
    keys = ("city", "date", "start_time", "end_time", "end_day_offset", "shape", "start", "style", "slots",
            "must_visit_place_ids", "variants", "radius_km", "fill_window", "known_hours_only", "top_k")
    return {k: sc[k] for k in keys if k in sc}


def plan_digest(plan, ctx, catalog) -> dict:
    def hours(pid):
        return catalog.hours_of(pid) if ctx.has_hours else None
    return {
        "stops": [{
            "order": s.order, "place_id": s.place_id, "name": s.name, "slot": s.slot, "theme": s.theme,
            "extra": s.extra, "pinned": s.pinned, "interest": s.interest, "dwell_min": s.dwell_min,
            "arrival_min": s.arrival_min, "wait_min": s.wait_min, "depart_min": s.depart_min, "hours_ok": s.hours_ok,
            "hours_label_ru": hours_label_ru(hours(s.place_id), ctx.t0 + s.arrival_min + s.wait_min),
        } for s in plan.stops],
        "segments": [{"from_order": g.from_order, "to_order": g.to_order, "walk_min": g.walk_min,
                      "distance_km": g.distance_km, "n_geom": len(g.geometry)} for g in plan.segments],
        "total_time_min": plan.total_time_min, "total_walk_min": plan.total_walk_min,
        "total_dwell_min": plan.total_dwell_min, "total_wait_min": plan.total_wait_min,
        "total_distance_km": plan.total_distance_km, "over_budget": plan.over_budget,
        "dropped_slots": list(plan.dropped_slots), "note": plan.note,
    }


def nav_digest(ctx, plan, catalog):
    return navigation_ru(ctx, plan, catalog) if plan.stops else None


def cand_row(c) -> list:
    return [c.place_id, c.slot, c.interest, c.dwell_min, c.dwell_fixed, c.theme, c.subtype]


def request_digest(req, n) -> dict:
    p = req.provider
    return {
        "mode": req.mode, "n_slots": req.n_slots, "start": list(req.start) if req.start is not None else None,
        "shape": req.shape, "style": req.style, "time_budget_min": req.time_budget_min,
        "fit_budget": req.fit_budget, "start_week_min": req.start_week_min, "max_wait_min": req.max_wait_min,
        "fill_window": req.fill_window, "walk_weight": req.walk_weight, "max_leg_min": req.max_leg_min,
        "interest_weight": req.interest_weight, "max_stops": req.max_stops,
        # the public package path (the class moved from walk_planner.py to walk_planner/core.py)
        "provider": f"walk_planner.{type(p).__name__}(walk_kmh={p.walk_kmh}, detour={p.detour})",
        "n_candidates": len(req.candidates), "n_must_visit": len(req.must_visit), "n_variants_requested": n,
    }


def run_chain(ctx, states, ops, catalog, provider) -> dict:
    """The dashboard editor's ops on variant 0, through the stateless API (see the module docstring)."""
    cur = states[0]
    seq = edit_stops(ctx, cur)
    removed: list = []
    manual = False
    resolved = []
    for op in ops:
        kind = op["op"]
        seq_ids = [e.place_id for e in seq]
        done = dict(op)
        new_seq = None
        if kind == "move":                       # drag within «Маршрут»
            n = len(seq)
            f = op["from"] + n if op["from"] < 0 else op["from"]
            t = op["to"] + n if op["to"] < 0 else op["to"]
            done.update({"from": f, "to": t})
            applied = 0 <= f < n and 0 <= t < n
            if applied:
                done["place_id"] = seq[f].place_id
                ns = list(seq)
                ns.insert(t, ns.pop(f))
                applied = [e.place_id for e in ns] != seq_ids
                if applied:
                    new_seq = ns
        elif kind == "remove":                   # «➖ Убрать место» -> the bin
            i = op["index"]
            done["place_id"] = seq[i].place_id if 0 <= i < len(seq) else None
            applied = 0 <= i < len(seq)
            if applied:
                removed = removed + [seq[i]]
                new_seq = seq[:i] + seq[i + 1:]
        elif kind == "restore":                  # drag back from «Не заходить» to position `to`
            ri, t = op["removed_index"], op["to"]
            applied = 0 <= ri < len(removed) and 0 <= t <= len(seq)
            if applied:
                item = removed[ri]
                done["place_id"] = item.place_id
                new_seq = seq[:t] + [item] + seq[t:]
                removed = removed[:ri] + removed[ri + 1:]
        elif kind == "add":                      # «➕ Добавить место из базы» -> /insert
            pid = str(op["place_id"])
            in_route = {e.place_id for e in seq}
            done["name"] = str(catalog.row(pid)["name"]) if catalog.has(pid) else None
            applied = False
            if not catalog.has(pid) or pid in in_route:
                done["skipped"] = "already in route" if pid in in_route else "not in city catalog"
            else:
                try:
                    cur, _pos = insert_place(ctx, seq, pid, catalog, provider)
                except PlannerInputError as err:
                    done["skipped"] = err.code
                else:
                    applied = True
                    seq = edit_stops(ctx, cur)
                    removed = [e for e in removed if e.place_id != pid]
        elif kind == "set_dwell":                # «⏱ Сколько побыть»: every stop rounded + fixed
            i, minutes = op["index"], op["dwell_min"]
            done["place_id"] = seq[i].place_id
            shown = [int(round(e.dwell_min)) for e in seq]
            edited = list(shown)
            edited[i] = minutes
            new_min = [int(x) for x in edited]
            applied = new_min != shown
            if applied:
                new_seq = [replace(e, dwell_min=float(m), dwell_fixed=True) for e, m in zip(seq, new_min)]
        else:
            raise ValueError(f"unknown edit op {kind!r}")
        done["applied"] = bool(applied)
        resolved.append(done)
        if applied:
            manual = True
            if new_seq is not None:
                cur = schedule(ctx, new_seq, catalog, provider)
                seq = edit_stops(ctx, cur)
    cur = replace(cur, edited=manual)
    return {"ops": resolved, "manual": manual, "removed": [e.place_id for e in removed],
            "plan": plan_digest(cur.plan, ctx, catalog), "render": render_variant_ru(ctx, [cur] + states[1:], 0, catalog),
            "navigation": nav_digest(ctx, cur.plan, catalog)}


def capture(sc: dict, catalog: CityCatalog) -> tuple[dict, object]:
    """The new pipeline's digest of one scenario (+ the PlanResult, for the report)."""
    provider = RoutingProvider()
    params = normalize_params(scenario_request(sc), catalog)
    result = build_plan(params, catalog, provider=provider)
    ctx = result.context
    ok = result.status == "ok"
    errors = [m.text("ru") for m in result.messages if m.severity == "error"]
    req = result.request
    start = ctx.start_resolved
    digest = {
        "scenario": sc,
        "inputs_resolved": {
            "start": list(start) if start is not None else None, "center": list(ctx.center),
            "start_exact": [repr(float(v)) for v in start] if start is not None else None,
            "center_exact": [repr(float(v)) for v in ctx.center],
            "t_start": ctx.start_label, "t0": ctx.t0, "budget_min": ctx.budget_min, "end_abs": ctx.end_abs,
            "next_day": ctx.next_day, "end_label_ru": ctx.end_label,
            "slot_labels": [LABEL_RU_BY_ACTIVITY[c] for c in ctx.slot_codes],
            "style_label": STYLES[params.style].label_ru,
            "dwell_by_slot": {LABEL_RU_BY_ACTIVITY[s.activity]: s.dwell_min for s in params.slots
                              if s.dwell_min is not None},
            "must_ids": list(params.must_visit_place_ids), "seed_ids": [],
        },
        "error": errors[0] if errors else None,
        "notes": [{"kind": m.severity, "text": m.text("ru")} for m in result.messages if m.severity != "error"]
                 if ok else [],
        "search_km": ctx.search_km if (ok or params.slots) else None,
        "area": list(ctx.area) if (ok or params.slots) else None,
        "request": request_digest(req, params.variants) if req is not None else None,
        "candidates": {
            "slot": [cand_row(c) for c in result.slot_candidates],
            "extra": [[c.place_id, c.slot, c.interest, c.dwell_min, LABEL_RU_BY_ACTIVITY[a]]
                      for c, a in zip(result.extra_candidates, result.extra_candidate_activities)],
            "must": [cand_row(c) + [c.pinned] for c in req.must_visit] if req is not None else [],
        },
        "variants": [],
        "edits": {},
    }
    if not ok:
        return digest, result
    states = result.variants
    for i, st in enumerate(states):
        digest["variants"].append({
            "dropped": [LABEL_RU_BY_ACTIVITY[ctx.slot_codes[j]] for j in st.dropped_slot_indices],
            "plan": plan_digest(st.plan, ctx, catalog),
            "render": render_variant_ru(ctx, states, i, catalog),
            "navigation": nav_digest(ctx, st.plan, catalog),
        })
    for chain in sc.get("edits", []):
        digest["edits"][chain["chain"]] = run_chain(ctx, states, chain["ops"], catalog, provider)
    return digest, result


# --------------------------------------------------------------------------------------------- #
# Normalisation + comparison
# --------------------------------------------------------------------------------------------- #
def norm(x):
    """capture_baseline.norm: floats rounded to 6 decimals (-0.0 -> 0.0), tuples -> lists."""
    if x is None or isinstance(x, (bool, str)):
        return x
    if hasattr(x, "item") and not isinstance(x, (list, dict)):       # numpy scalars
        x = x.item()
    if isinstance(x, bool):
        return x
    if isinstance(x, int):
        return int(x)
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            raise ValueError("non-finite float in digest")
        r = round(x, 6)
        return 0.0 if r == 0 else r
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    raise TypeError(f"cannot serialise {type(x)}")


def _flat(v) -> bool:
    return not isinstance(v, (dict, list)) or (isinstance(v, list) and all(not isinstance(i, (dict, list)) for i in v))


def dumps(obj, indent: int = 0) -> str:
    """capture_baseline.dumps (one line per stop / segment / candidate)."""
    pad = " " * (indent + 1)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        if all(_flat(v) for v in obj.values()) and indent > 0:
            return json.dumps(obj, ensure_ascii=False)
        body = ",\n".join(f"{pad}{json.dumps(k, ensure_ascii=False)}: {dumps(v, indent + 1)}" for k, v in obj.items())
        return "{\n" + body + "\n" + " " * indent + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if all(not isinstance(i, dict) and _flat(i) for i in obj):
            if all(not isinstance(i, list) for i in obj):
                return json.dumps(obj, ensure_ascii=False)
        body = ",\n".join(f"{pad}{dumps(v, indent + 1)}" for v in obj)
        return "[\n" + body + "\n" + " " * indent + "]"
    return json.dumps(obj, ensure_ascii=False)


def _same_scalar(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or (isinstance(a, bool) and isinstance(b, bool) and a == b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= TOL
    return a == b


def compare(base, new, path: str, out: list) -> None:
    """Append (path, base_value, new_value) for every difference."""
    if isinstance(base, dict) and isinstance(new, dict):
        for k in list(base) + [k for k in new if k not in base]:
            if k not in new:
                out.append((f"{path}.{k}", base[k], "<missing>"))
            elif k not in base:
                out.append((f"{path}.{k}", "<missing>", new[k]))
            else:
                compare(base[k], new[k], f"{path}.{k}", out)
    elif isinstance(base, list) and isinstance(new, list):
        if len(base) != len(new):
            out.append((f"{path}.<len>", len(base), len(new)))
        for i, (x, y) in enumerate(zip(base, new)):
            compare(x, y, f"{path}[{i}]", out)
    elif not _same_scalar(base, new):
        out.append((path, base, new))


def compare_candidate_rows(base: list, new: list, path: str, out: list) -> str:
    """Positional compare; else as multisets (tied-interest order). Returns "exact" | "as_set" | "diff"."""
    pos: list = []
    compare(base, new, path, pos)
    if not pos:
        return "exact"
    key = lambda r: json.dumps(norm(r), ensure_ascii=False, sort_keys=True)  # noqa: E731
    if len(base) == len(new) and sorted(map(key, base)) == sorted(map(key, new)):
        return "as_set"
    out.extend(pos)
    return "diff"


def _is_free_segment_shift(path: str, base, new, free: bool) -> bool:
    """Bug 1: in a shape-"free" plan the fixed core labels segment i as (i, i+1) instead of (i-1, i)."""
    return (free and ".segments[" in path and (path.endswith(".from_order") or path.endswith(".to_order"))
            and isinstance(base, int) and isinstance(new, int) and new == base + 1)


def _is_env_float_repr(path: str, base, new) -> bool:
    """`inputs_resolved.*_exact` are repr strings of the start / city centre. The centre is a pandas
    mean, whose last bits depend on the numpy build (e.g. numpy 1.26 vs 2.4 differ by ~1e-14); every
    output is compared separately, so a repr that differs by < 1e-9 is an environment difference."""
    if not (path.startswith(".inputs_resolved.") and "_exact[" in path and isinstance(base, str) and isinstance(new, str)):
        return False
    try:
        return abs(float(base) - float(new)) <= 1e-9
    except ValueError:
        return False


# Where the bug-2 fix may change a BUG2 scenario's digest: the notes, the must-visit candidates and
# their count, and the plans / renders / navigation of the variants (and of edit chains: ".edits.<name>"
# in the scenario compare, "edits.<name>" in the per-chain one). Anything else that differs there is
# unexpected, like in every other scenario.
_BUG2_PATH = re.compile(r"^(?:\.notes|\.candidates\.must|\.request\.n_must_visit)(?:$|[.\[])"
                        r"|^\.variants\[\d+\]\.(?:plan|render|navigation)(?:$|[.\[])"
                        r"|^\.?edits\.[^.\[]+\.(?:plan|render|navigation)(?:$|[.\[])")


def _is_bug2_path(path: str) -> bool:
    return _BUG2_PATH.match(path) is not None


def closed_forever_of(sc: dict, catalog: CityCatalog) -> dict:
    """{place_id: name} of the scenario's must-visits the catalog says are closed_forever (request order)."""
    return {str(pid): catalog.name_of(pid) for pid in sc.get("must_visit_place_ids") or []
            if catalog.has(pid) and catalog.status_of(pid) == "closed_forever"}


def bug2_problems(sc: dict, base: dict, new: dict, closed_forever: Optional[dict] = None) -> list:
    """Checks that a BUG2 scenario's allowed differences are exactly what bug 2 explains; returns the
    violations as (path, expected, got) diffs. `closed_forever` = {place_id: name} from the catalog
    (``closed_forever_of``); without it the dropped must-visits are taken from the digests and the
    note's text is not checked (only that exactly one warning note was added)."""
    out: list = []
    base_must, new_must = base["candidates"]["must"], new["candidates"]["must"]
    if closed_forever is None:
        kept = {r[0] for r in new_must}
        closed_ids = [r[0] for r in base_must if r[0] not in kept]
    else:
        closed_ids = list(closed_forever)
    if not closed_ids:
        out.append(("<bug2>.closed_forever_must_visits", "at least one", []))
    expected_must = [r for r in base_must if r[0] not in set(closed_ids)]
    compare(expected_must, new_must, "<bug2>.candidates.must", out)
    if new["request"] is not None and new["request"].get("n_must_visit") != len(expected_must):
        out.append(("<bug2>.request.n_must_visit", len(expected_must), new["request"].get("n_must_visit")))
    # notes: exactly one must_visit_closed_forever note added; without it, the baseline's notes in order
    notes = list(new["notes"])
    if closed_forever is not None:
        note = {"kind": "warning", "text": Message("must_visit_closed_forever", {
            "place_ids": closed_ids, "names": [closed_forever[p] for p in closed_ids]}).text("ru")}
        found = [note] if notes.count(note) == 1 else []
    else:
        found = [n for n in notes if n.get("kind") == "warning" and n not in base["notes"]]
        note = found[0] if len(found) == 1 else {"kind": "warning", "text": "<one must_visit_closed_forever note>"}
    if len(found) != 1:
        out.append(("<bug2>.notes.closed_forever_note", note, notes))
    else:
        notes.remove(found[0])
    if notes != base["notes"]:
        out.append(("<bug2>.notes.others", base["notes"], notes))
    # every variant: no closed_forever place, every kept must-visit pinned
    kept_ids = [r[0] for r in expected_must]
    for i, v in enumerate(new["variants"]):
        stops = {s["place_id"]: s for s in v["plan"]["stops"]}
        for pid in closed_ids:
            if pid in stops:
                out.append((f"<bug2>.variants[{i}].plan.closed_forever_routed", None, pid))
        for pid in kept_ids:
            if pid not in stops or not stops[pid]["pinned"]:
                out.append((f"<bug2>.variants[{i}].plan.must_visit_not_pinned", pid, None))
    return out


def check_scenario(sc: dict, base: dict, new: dict, closed_forever: Optional[dict] = None) -> dict:
    """{"status": identical | allowed | diff, "diffs": [...], "allowed": [...], "candidates": {...}, "chains": {...}}

    `closed_forever` ({place_id: name}, see ``closed_forever_of``) sharpens the bug-2 guard of the
    BUG2 scenarios; see the module docstring."""
    free = sc.get("shape") == "free"
    bug2 = sc["id"] in BUG2_SCENARIOS
    diffs: list = []
    cand_mode = {}
    b2 = json.loads(json.dumps(base))
    n2 = json.loads(json.dumps(new))
    for kind in ("slot", "extra"):
        cand_mode[kind] = compare_candidate_rows(b2["candidates"].pop(kind), n2["candidates"].pop(kind),
                                                 f"candidates.{kind}", diffs)
    compare(b2, n2, "", diffs)
    allowed, unexpected = [], []
    for d in diffs:
        if _is_free_segment_shift(d[0], d[1], d[2], free):
            allowed.append(("bug1",) + d)
        elif _is_env_float_repr(d[0], d[1], d[2]):
            allowed.append(("env",) + d)
        elif bug2 and _is_bug2_path(d[0]):
            allowed.append(("bug2",) + d)
        else:
            unexpected.append(d)
    if bug2 and any(a[0] == "bug2" for a in allowed):
        unexpected.extend(bug2_problems(sc, base, new, closed_forever))
    chains = {}                                   # per-chain summary (its diffs are already in `diffs`)
    for name in base.get("edits", {}):
        c_diffs: list = []
        compare(base["edits"][name], new.get("edits", {}).get(name), f"edits.{name}", c_diffs)
        c_bad = [d for d in c_diffs if not _is_free_segment_shift(d[0], d[1], d[2], free)
                 and not _is_env_float_repr(d[0], d[1], d[2]) and not (bug2 and _is_bug2_path(d[0]))]
        chains[name] = "identical" if not c_diffs else ("allowed" if not c_bad else "diff")
    status = "identical" if not diffs and not unexpected else ("allowed" if not unexpected else "diff")
    return {"status": status, "diffs": unexpected, "allowed": allowed, "candidates": cand_mode, "chains": chains}


# --------------------------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------------------------- #
def describe_s19(result, digest) -> list[str]:
    """The new S19 behaviour (bug-2 fix), as report lines."""
    lines = ["S19_must_closed — new behaviour (bug 2 fixed):"]
    for m in result.messages:
        lines.append(f"  request message {m.code} ({m.severity}) params={json.dumps(m.params, ensure_ascii=False)}")
        lines.append(f"      ru: {m.text('ru')}")
    lines.append(f"  must-visits passed to the solver: {[r[0] for r in digest['candidates']['must']]}")
    for v in result.variants:
        stops = ", ".join(f"{s.order + 1}.{s.name}{' [pinned]' if s.pinned else ' [extra]' if s.extra else ''}"
                          for s in v.plan.stops)
        lines.append(f"  variant {v.index + 1}: {len(v.plan.stops)} stops, {v.plan.total_time_min:.1f} min — {stops}")
    for i, vd in enumerate(digest["variants"]):
        for w in vd["render"]["warnings"]:
            lines.append(f"  variant {i + 1} warning: {w}")
        for sl in vd["render"]["stop_lines"]:
            if any("Временно закрыто" in x for x in sl):
                lines.append(f"  variant {i + 1} card: {' | '.join(sl)}")
    return lines


def _guard_area(path: str) -> str:
    """The bug-2 area of a diff path ('notes', 'candidates.must', 'variants[*].plan', ...)."""
    m = re.match(r"^\.?(notes|candidates\.must|request\.n_must_visit|variants\[\d+\]\.\w+|edits\.[^.\[]+\.\w+)", path)
    return re.sub(r"\[\d+\]", "[*]", m.group(1)) if m else path


def select_scenarios(scenarios: list, only: set) -> tuple:
    """(scenarios to check, ids skipped because they have no baseline_v0 digest -- ``"baseline_v0": false``),
    both in file order and restricted to `only` when given."""
    chosen = [sc for sc in scenarios if not only or sc["id"] in only]
    return ([sc for sc in chosen if sc.get("baseline_v0") is not False],
            [sc["id"] for sc in chosen if sc.get("baseline_v0") is False])


def load_catalog(source: str) -> CityCatalog:
    df = pd.read_csv(CATALOG_CSV)
    if source == "csv":
        return CityCatalog.from_frame(df, city="Bucharest")
    with tempfile.TemporaryDirectory(prefix="wp_parity_bundle_") as tmp:   # read fully into memory
        to_bundle_frame(df).to_parquet(Path(tmp) / "walk_catalog.parquet", index=False)
        return CityCatalog.from_bundle(tmp)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--only", default="", help="comma-separated scenario ids")
    ap.add_argument("--source", choices=("csv", "bundle"), default="csv",
                    help="catalog path: CityCatalog.from_frame(CSV) or from_bundle(to_bundle_frame(CSV) as parquet)")
    ap.add_argument("--write", type=Path, default=None, help="also write the new digests into this directory")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every difference")
    args = ap.parse_args(argv)
    _hermetic()
    if CATALOG_CSV is None or not CATALOG_CSV.exists():
        print(f"catalog not found: {CATALOG_CSV or '(no research repo around this copy)'}", file=sys.stderr)
        return 2

    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))
    only = {s for s in args.only.split(",") if s}
    unknown = sorted(only - {sc["id"] for sc in scenarios})
    if unknown:
        print(f"unknown scenario ids: {', '.join(unknown)}", file=sys.stderr)
        return 2
    selected, skipped = select_scenarios(scenarios, only)
    t_all = time.perf_counter()
    catalog = load_catalog(args.source)
    print(f"catalog: {catalog!r} source={args.source}")
    if args.write:
        args.write.mkdir(parents=True, exist_ok=True)
    n_sc = n_ident = n_allowed = n_bad = 0
    chain_counts = {"identical": 0, "allowed": 0, "diff": 0}
    cand_counts = {"exact": 0, "as_set": 0, "diff": 0}
    bug1_seen = {"old": 0, "fixed": 0}
    s19_lines: list[str] = []
    for sid in skipped:
        print(f"{sid:24s}  SKIP     no baseline_v0 (new in v1; pinned by golden/expected only)", flush=True)
    for sc in selected:
        t = time.perf_counter()
        digest, result = capture(sc, catalog)
        new = norm(digest)
        if args.write:
            (args.write / f"{sc['id']}.json").write_text(dumps(new) + "\n", encoding="utf-8")
        base = json.loads((args.baseline / f"{sc['id']}.json").read_text(encoding="utf-8"))
        closed = closed_forever_of(sc, catalog) if sc["id"] in BUG2_SCENARIOS else None
        rep = check_scenario(sc, base, new, closed_forever=closed)
        n_sc += 1
        n_ident += rep["status"] == "identical"
        n_allowed += rep["status"] == "allowed"
        n_bad += rep["status"] == "diff"
        for k, v in rep["chains"].items():
            chain_counts[v] += 1
        for v in rep["candidates"].values():
            cand_counts[v] += 1
        if sc.get("shape") == "free":
            fixed = any(a[0] == "bug1" for a in rep["allowed"])
            bug1_seen["fixed" if fixed else "old"] += 1
        chains = " ".join(f"{k}:{v}" for k, v in rep["chains"].items()) or "-"
        extra = ""
        if rep["allowed"]:
            kinds = sorted({a[0] for a in rep["allowed"]})
            extra = f" allowed={len(rep['allowed'])} ({','.join(kinds)})"
        print(f"{sc['id']:24s} {time.perf_counter() - t:5.2f}s {rep['status'].upper():9s} "
              f"cand(slot={rep['candidates']['slot']}, extra={rep['candidates']['extra']}) "
              f"chains[{chains}]{extra}" + (f" UNEXPECTED={len(rep['diffs'])}" if rep["diffs"] else ""), flush=True)
        shown = rep["diffs"] if not args.verbose else rep["diffs"] + [a[1:] for a in rep["allowed"]]
        for path, b, n in shown[: (None if args.verbose else 12)]:
            print(f"    {path}: baseline={json.dumps(b, ensure_ascii=False)[:160]} new={json.dumps(n, ensure_ascii=False)[:160]}")
        if sc["id"] in BUG2_SCENARIOS:
            s19_lines = describe_s19(result, digest)
            guarded = sorted({_guard_area(a[1]) for a in rep["allowed"] if a[0] == "bug2"})
            s19_lines.append(f"  bug-2 guard: {'OK' if not rep['diffs'] else 'VIOLATED'} — closed_forever "
                             f"{json.dumps(closed, ensure_ascii=False)}; differences only in {', '.join(guarded)}")
    print()
    print(f"scenarios: {n_sc} — identical {n_ident}, allowed-only {n_allowed}, UNEXPECTED {n_bad}"
          + (f"; skipped (no baseline_v0) {len(skipped)}: {', '.join(skipped)}" if skipped else ""))
    print(f"edit chains: {sum(chain_counts.values())} — identical {chain_counts['identical']}, "
          f"allowed-only {chain_counts['allowed']}, UNEXPECTED {chain_counts['diff']}")
    print(f"candidate lists: exact {cand_counts['exact']}, equal as sets {cand_counts['as_set']}, "
          f"different {cand_counts['diff']}")
    if bug1_seen["old"] or bug1_seen["fixed"]:
        print(f"shape 'free' segment indices: baseline (pre-fix) form in {bug1_seen['old']} scenario(s), "
              f"fixed form in {bug1_seen['fixed']} scenario(s)")
    for line in s19_lines:
        print(line)
    print(f"done in {time.perf_counter() - t_all:.1f}s")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
