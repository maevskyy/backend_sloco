"""The package surface (walk_planner/__init__.py): every module's public API (its ``__all__``) is
re-exported under the same name, without clashes; every name the pre-refactor single-module
walk_planner.py and the stage-1 package exported is still there; and ``import walk_planner`` stays
cheap — no pandas / pyarrow / scikit-learn until a name that needs them is used (lazy, PEP 562)."""

import ast
import importlib
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

import walk_planner

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("core", "routing", "slots", "messages", "present", "version",
           "catalog", "candidates", "pipeline", "interest", "interest_build", "bundle")
LAZY = ("catalog", "candidates", "pipeline", "interest", "interest_build", "bundle")
# Public top-level names a module keeps for itself (not API): a logger, a typing alias.
NOT_EXPORTED = {"routing": {"log"}, "messages": {"Template"}}
# git HEAD f1b2196 recommendation_system/ai_location_recommender/walk_planner.py (the dashboard's import).
OLD_MODULE_EXPORTS = (
    "Candidate", "DEFAULT_DETOUR", "DEFAULT_DWELL", "DEFAULT_WALK_KMH", "DWELL_MINUTES", "EARTH_R_KM",
    "EXTRA_DWELL_MIN", "EXTRA_INTEREST_POWER", "EXTRA_MIN_INTEREST", "GMAPS_MAX_WAYPOINTS", "MIN_WALK_WEIGHT",
    "MUST_VISIT_WALK_MIN", "ORSProvider", "RoutingProvider", "SCENIC_SUBTYPES", "SCENIC_THEMES", "SKIP_SLOT_PENALTY",
    "STYLE_PRESETS", "Segment", "Stop", "VARIANT_REUSE_FACTOR", "WEEK_MIN", "WalkPlan", "WalkRequest",
    "best_insertion", "dwell_for", "estimate_dwell_min", "haversine_km", "navigation_links", "open_within",
    "plan_sequence", "plan_variants", "plan_walk", "reach_radius_km", "visit_wait", "week_intervals_from_timetable",
)
# Stage-1 package exports (routing chain + versions + the tests' _two_opt).
STAGE1_EXPORTS = (
    "Breaker", "ChainProvider", "LegCache", "ORSProvider", "ORSRouter", "OSRMRouter", "RateLimiter", "RoutingConfig",
    "RoutingError", "make_provider", "polyline6_decode", "polyline6_encode", "reset_routing_state", "routing_status",
    "ALGORITHM_VERSION", "API_VERSION", "BUNDLE_SCHEMA_VERSION", "INTEREST_VERSION", "_two_opt", "__version__",
)


def _module(name):
    return importlib.import_module(f"walk_planner.{name}")


def _public_definitions(module) -> set:
    """Public names a module defines at top level (functions, classes, assignments)."""
    tree = ast.parse(inspect.getsource(module))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {n for n in names if not n.startswith("_")}


@pytest.mark.parametrize("name", MODULES)
def test_every_module_declares_its_whole_public_api(name):
    mod = _module(name)
    api = mod.__all__
    assert len(set(api)) == len(api), name
    assert all(hasattr(mod, attr) for attr in api), name
    assert set(api) == _public_definitions(mod) - NOT_EXPORTED.get(name, set()), name


def test_the_package_reexports_every_module_api_without_name_clashes():
    owner: dict = {}
    for name in MODULES:
        mod = _module(name)
        for attr in mod.__all__:
            assert attr not in owner, f"{attr!r} is exported by both {owner[attr]} and {name}"
            owner[attr] = name
            assert getattr(walk_planner, attr) is getattr(mod, attr), (name, attr)
    assert sorted(walk_planner.__all__) == sorted(owner) and len(walk_planner.__all__) == len(owner)
    assert set(owner) <= set(dir(walk_planner))
    namespace: dict = {}
    exec("from walk_planner import *", namespace)                 # the lazy names resolve too
    assert set(owner) <= set(namespace)


def test_the_lazy_export_table_is_each_modules_api():
    assert set(walk_planner._LAZY_EXPORTS) == set(LAZY)
    for name in LAZY:
        assert walk_planner._LAZY_EXPORTS[name] == tuple(_module(name).__all__), name


def test_the_two_interest_records_have_distinct_names():
    from walk_planner import interest, pipeline
    assert walk_planner.InterestResult is interest.InterestResult        # the taste model's result
    assert walk_planner.PlanInterest is pipeline.PlanInterest            # the plan's record (+ error)
    assert not hasattr(pipeline, "InterestResult")


def test_every_earlier_export_is_still_there():
    for name in OLD_MODULE_EXPORTS + STAGE1_EXPORTS:
        assert hasattr(walk_planner, name), name
    assert walk_planner.ORSProvider is walk_planner.routing.ORSProvider is walk_planner.core.ORSProvider
    assert walk_planner.__version__ == walk_planner.ALGORITHM_VERSION
    from walk_planner import messages, pipeline
    # the API error type lives next to the error catalog (catalog.search raises it too); the contract name
    # walk_planner.pipeline.PlannerInputError keeps working
    assert walk_planner.PlannerInputError is messages.PlannerInputError is pipeline.PlannerInputError


def test_unknown_names_still_fail_normally():
    with pytest.raises(AttributeError):
        walk_planner.no_such_name                                    # noqa: B018
    with pytest.raises(ImportError):
        exec("from walk_planner import no_such_name", {})


def test_importing_the_package_is_cheap():
    # a fresh interpreter: the package alone imports numpy but no pandas / pyarrow / sklearn / requests;
    # a lazily exported name imports its module on first use (still no sklearn: it loads on demand)
    code = (
        "import sys\n"
        "import walk_planner as wp\n"
        "heavy = ('pandas', 'pyarrow', 'sklearn', 'scipy', 'requests')\n"
        "print(sorted(m for m in heavy if m in sys.modules), 'walk_planner.catalog' in sys.modules)\n"
        "wp.CityCatalog, wp.build_plan, wp.interest_map, wp.build_taste_artifacts, wp.place_candidate, wp.load_bundle\n"
        "print(sorted(m for m in ('sklearn', 'scipy', 'requests') if m in sys.modules),"
        " all(f'walk_planner.{m}' in sys.modules for m in ('catalog', 'pipeline', 'interest', 'interest_build',"
        " 'bundle')))\n"
    )
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == ["[] False", "[] True"]
