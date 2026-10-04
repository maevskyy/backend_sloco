#!/usr/bin/env python3
"""Compare two golden expected sets (``golden/expected/<bundle_id>/``) and classify every difference.

The proof that a NEW data bundle with the same data changes nothing but float noise -- run it before
replacing an expected set (``golden update``) when only the bundle was rebuilt (golden/README.md,
"Re-generating for a rebuilt bundle"):

    python golden/tools/compare_expected_sets.py OLD_SET_DIR NEW_SET_DIR [--bundles OLD_BUNDLE NEW_BUNDLE] [-v]

Both sets must hold the same scenario files with the same structure (keys, list lengths). Every value that
differs is put into one class:

  identity           plan_id, versions.catalog, request.catalog_version: they embed the bundle id
  float              two numbers within 1e-6 (the golden tolerance); the report gives the largest |delta|
  clock_half_minute  a TimePoint clock "HH:MM" one minute apart whose offset lies within 1e-6 of a half
                     minute (``cli._is_half_minute_flip``: Python's round() of float noise either way)
  coordinate_6dp     a navigation URL whose only change is a coordinate printed with 6 decimals moving by
                     one unit (1e-6): the coordinate sits on a half-way point (x.xxxxxx5) and float noise
                     decides the rounding
  exact_tie          (only with --bundles) every difference inside ONE variant / edit response whose stops are
                     the same places with the same kinds in another order, where BOTH orders take the same
                     time (within 1e-9 min) on BOTH bundles -- re-timed with the package's exact-order scheduler
                     (pipeline.schedule). The solver met an exact tie (e.g. a loop and its reverse) and the
                     last bits of the inputs picked the winner
  SUBSTANTIVE        anything else: another place, order, count, status, code or text -- must be 0

It also states, per scenario, whether every variant's and every edit call's stop sequence (place ids in
order) is identical. Exit 0 = no substantive difference and every changed stop sequence a verified exact tie;
1 otherwise; 2 = usage. Read-only; no network; works on the JSON alone (--bundles only to verify ties).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))                 # services/walk_planner: the package

from walk_planner.cli import GOLDEN_TOLERANCE, _is_half_minute_flip, compare_json  # noqa: E402

IDENTITY = re.compile(r"(?:\.plan_id|\.versions\.catalog|\.catalog_version)$")
NUMBER = re.compile(r"(-?\d+\.\d+)")
COORD_STEP = 1e-6                                         # one unit of a coordinate printed with 6 decimals


def _coordinate_flip(e: str, a: str) -> bool:
    """`e` and `a` are the same URL except for 6-decimal numbers that moved by exactly one unit."""
    if not (e.startswith("http") and a.startswith("http")):
        return False
    pe, pa = NUMBER.split(e), NUMBER.split(a)
    if len(pe) != len(pa):
        return False
    moved = 0
    for i, (x, y) in enumerate(zip(pe, pa)):
        if i % 2 == 0:                                    # text between numbers: identical
            if x != y:
                return False
        elif x != y:
            if len(x.split(".")[1]) != 6 or len(y.split(".")[1]) != 6:
                return False
            if abs(abs(float(x) - float(y)) - COORD_STEP) > 1e-9:
                return False
            moved += 1
    return moved > 0


def classify(diff: tuple, old: dict, new: dict) -> str:
    path, e, a = diff
    if IDENTITY.search(path):
        return "identity"
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (e, a))
    if numbers:
        return "float" if abs(float(e) - float(a)) <= GOLDEN_TOLERANCE else "SUBSTANTIVE"
    if isinstance(e, str) and isinstance(a, str):
        if _is_half_minute_flip(diff, old, new):
            return "clock_half_minute"
        if _coordinate_flip(e, a):
            return "coordinate_6dp"
    return "SUBSTANTIVE"


def _stop_sequences(doc: dict) -> list:
    """[(where, [place ids])] of every variant of the plan and of every successful edit call."""
    out = []
    plan = (doc.get("plan") or {}).get("response") or {}
    for i, v in enumerate(plan.get("variants") or []):
        out.append((f"plan.variants[{i}]", [s["place_id"] for s in v.get("stops") or []]))
    for chain, steps in sorted((doc.get("edits") or {}).items()):
        for i, step in enumerate(steps):
            resp = ((step.get("call") or {}).get("response") or {})
            if "variant" in resp:
                out.append((f"edits.{chain}[{i}]", [s["place_id"] for s in resp["variant"].get("stops") or []]))
    return out


def _leaves(obj) -> int:
    """Number of scalar values in a JSON document."""
    if isinstance(obj, dict):
        return sum(_leaves(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_leaves(v) for v in obj)
    return 1


TIE_TOLERANCE_MIN = 1e-9


def _route_at(doc: dict, where: str) -> dict:
    """The variant JSON behind a ``_stop_sequences`` label ("plan.variants[i]" / "edits.<chain>[i]")."""
    m = re.match(r"^plan\.variants\[(\d+)\]$", where)
    if m:
        return doc["plan"]["response"]["variants"][int(m.group(1))]
    m = re.match(r"^edits\.(.+)\[(\d+)\]$", where)
    return doc["edits"][m.group(1)][int(m.group(2))]["call"]["response"]["variant"]


def _request_of(doc: dict, where: str) -> dict:
    if where.startswith("plan."):
        return doc["plan"]["response"]["request"]
    m = re.match(r"^edits\.(.+)\[(\d+)\]$", where)
    return doc["edits"][m.group(1)][int(m.group(2))]["call"]["response"]["request"]


def _diff_prefix(where: str) -> str:
    m = re.match(r"^plan\.variants\[(\d+)\]$", where)
    if m:
        return f".plan.response.variants[{m.group(1)}]"
    m = re.match(r"^edits\.(.+)\[(\d+)\]$", where)
    return f".edits.{m.group(1)}[{m.group(2)}].call.response.variant"


def verify_tie(old: dict, new: dict, where: str, bundles: tuple) -> tuple:
    """(is an exact tie, evidence text) for the route `where` that differs between `old` and `new`: the same
    (place_id, kind) multiset, and on each bundle both orders re-timed by ``pipeline.schedule`` take the same
    time within TIE_TOLERANCE_MIN."""
    from walk_planner.pipeline import from_request_echo, schedule

    vo, vn = _route_at(old, where), _route_at(new, where)
    kinds = (sorted((s["place_id"], s["kind"]) for s in vo["stops"]), sorted((s["place_id"], s["kind"]) for s in vn["stops"]))
    if kinds[0] != kinds[1]:
        return False, "the stops are not the same places / kinds"
    notes = []
    for label, bundle in zip(("old", "new"), bundles):
        echo = dict(_request_of(old, where), catalog_version=bundle.bundle_id)
        ctx = from_request_echo(echo, bundle.catalog)
        t_old = schedule(ctx, vo["sequence"], bundle.catalog).plan.total_time_min
        t_new = schedule(ctx, vn["sequence"], bundle.catalog).plan.total_time_min
        notes.append(f"{label} bundle: old order {t_old!r} min, new order {t_new!r} min")
        if abs(t_old - t_new) > TIE_TOLERANCE_MIN:
            return False, "; ".join(notes)
    return True, "; ".join(notes)


def all_diffs(old: dict, new: dict) -> list:
    """Every leaf difference (``compare_json`` with tolerance 0)."""
    return compare_json(old, new, tol=0.0)


def compare_sets(old_dir: Path, new_dir: Path, verbose: bool = False, out=print, bundles: tuple = ()) -> int:
    old_files = sorted(p.name for p in old_dir.glob("*.json") if p.name != "index.json")
    new_files = sorted(p.name for p in new_dir.glob("*.json") if p.name != "index.json")
    if old_files != new_files:
        out(f"SUBSTANTIVE: the sets hold different scenarios: only old {sorted(set(old_files) - set(new_files))}, "
            f"only new {sorted(set(new_files) - set(old_files))}")
        return 1
    totals: dict = {}
    max_float = 0.0
    bad_routes = 0
    n_values = 0
    tie_lines: list = []
    out(f"old: {old_dir}\nnew: {new_dir}")
    out(f"{'scenario':24s} {'leaves':>7s} {'identity':>8s} {'float':>6s} {'clock':>6s} {'coord':>6s} {'tie':>5s} "
        f"{'SUBST':>6s} {'max |d|':>9s} routes")
    for name in old_files:
        old = json.loads((old_dir / name).read_text(encoding="utf-8"))
        new = json.loads((new_dir / name).read_text(encoding="utf-8"))
        leaves = _leaves(old)
        n_values += leaves
        counts = {"identity": 0, "float": 0, "clock_half_minute": 0, "coordinate_6dp": 0, "exact_tie": 0,
                  "SUBSTANTIVE": 0}
        examples: dict = {}
        mx = 0.0
        routes_old, routes_new = _stop_sequences(old), _stop_sequences(new)
        changed = [w for (w, a), (_w, b) in zip(routes_old, routes_new) if a != b]
        same_routes = [w for w, _ in routes_old] == [w for w, _ in routes_new] and not changed
        ties: list = []
        if changed and bundles and [w for w, _ in routes_old] == [w for w, _ in routes_new]:
            for where in changed:
                ok, evidence = verify_tie(old, new, where, bundles)
                if ok:
                    ties.append(_diff_prefix(where))
                out_line = f"    {'exact tie' if ok else 'NOT a tie'}: {name[:-5]} {where}: {evidence}"
                tie_lines.append(out_line)
        for d in all_diffs(old, new):
            k = "exact_tie" if any(d[0].startswith(t + ".") or d[0].startswith(t + "[") for t in ties) \
                else classify(d, old, new)
            counts[k] += 1
            examples.setdefault(k, []).append(d)
            if k == "float":
                mx = max(mx, abs(float(d[1]) - float(d[2])))
        max_float = max(max_float, mx)
        unexplained = len(changed) - len(ties) if changed else (0 if same_routes else 1)
        bad_routes += bool(unexplained)
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
        out(f"{name[:-5]:24s} {leaves:>7d} {counts['identity']:>8d} {counts['float']:>6d} "
            f"{counts['clock_half_minute']:>6d} {counts['coordinate_6dp']:>6d} {counts['exact_tie']:>5d} "
            f"{counts['SUBSTANTIVE']:>6d} {mx:>9.2g} "
            + ("identical" if same_routes else (f"{len(ties)} exact tie(s)" if not unexplained else "DIFFERENT")))
        for line in tie_lines:
            out(line)
        tie_lines.clear()
        shown = ["SUBSTANTIVE"] + (["exact_tie", "coordinate_6dp", "clock_half_minute", "float", "identity"]
                                   if verbose else [])
        for k in shown:
            for p, e, a in examples.get(k, [])[: (None if verbose else 5)]:
                out(f"    {k}: {p}: {json.dumps(e, ensure_ascii=False)[:140]} -> {json.dumps(a, ensure_ascii=False)[:140]}")
    out(f"total over {len(old_files)} scenarios: " + ", ".join(f"{k} {v}" for k, v in totals.items())
        + f"; largest float |delta| {max_float:.3g}; unexplained stop sequence changes in {bad_routes} scenario(s)")
    ok = totals.get("SUBSTANTIVE", 0) == 0 and bad_routes == 0
    if ok and totals.get("exact_tie"):
        out("RESULT: float noise only (identity fields aside) -- including exact ties whose winner the float noise "
            "picked (see the 'exact tie' lines: same places, same time both ways on both bundles)")
    else:
        out("RESULT: " + ("float noise only (identity fields aside)" if ok else "SUBSTANTIVE differences"))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("old", type=Path, help="the expected set before (golden/expected/<old bundle id>)")
    ap.add_argument("new", type=Path, help="the expected set after (e.g. made by golden update --expected-root TMP)")
    ap.add_argument("--bundles", nargs=2, type=Path, metavar=("OLD_BUNDLE", "NEW_BUNDLE"), default=None,
                    help="the two bundles: verify changed stop orders as exact ties (re-timed on both)")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every difference of every class")
    args = ap.parse_args(argv)
    for d in (args.old, args.new) + tuple(args.bundles or ()):
        if not d.is_dir():
            print(f"not a directory: {d}", file=sys.stderr)
            return 2
    bundles = ()
    if args.bundles:
        from walk_planner.bundle import load_bundle

        bundles = tuple(load_bundle(b) for b in args.bundles)
    return compare_sets(args.old, args.new, verbose=args.verbose, bundles=bundles)


if __name__ == "__main__":
    sys.exit(main())
