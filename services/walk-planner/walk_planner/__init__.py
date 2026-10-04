"""SLOCO Walk Planner — a walking-route planner (time window + activity slots + style -> ordered stops).

One package is the single source of truth for both the research dashboard (Streamlit test bench) and
the `walk-planner` microservice. `core` is the routing/scheduling algorithm; the other modules wrap it
(candidate selection, interest, routing chain, API views, CLI, service). See docs/SPEC.md.

Public API = the ``__all__`` of every module, re-exported here under the same names (unique across
modules; the pipeline's plan-level interest record is ``PlanInterest``, ``InterestResult`` is the
taste model's):

  * imported eagerly (stdlib + numpy only): core, routing, slots, messages, present, version;
  * exported LAZILY (PEP 562: imported on first access of one of their names): catalog, candidates,
    pipeline, interest, interest_build, bundle — they need pandas (which loads pyarrow), and interest
    may load scikit-learn. ``import walk_planner`` therefore stays cheap: no pandas, pyarrow or sklearn
    until such a name is used (``from walk_planner import CityCatalog`` works as usual).

The developer CLI is ``walk_planner.cli`` (``python -m walk_planner``) and the HTTP service
``walk_planner.service``; neither is re-exported here.

Every name the old single-module ``walk_planner.py`` exported is still here (and ``_two_opt``, used by
tests); ``ORSProvider`` is the routing module's (compatibility class of the old name).
"""
import typing as _typing

from .core import *  # noqa: F401,F403  (core.__all__: the algorithm)
from .core import _two_opt  # noqa: F401  (used by tests)
from .messages import *  # noqa: F401,F403  (message catalog)
from .present import *  # noqa: F401,F403  (API views + RU formatters)
from .routing import *  # noqa: F401,F403  (production routing chain; ORSProvider = the old class name)
from .slots import *  # noqa: F401,F403  (activity / style / shape registry)
from .version import *  # noqa: F401,F403
from . import core as _core, messages as _messages, present as _present, routing as _routing, slots as _slots
from . import version as _version
from .version import ALGORITHM_VERSION as _ALGORITHM_VERSION

if _typing.TYPE_CHECKING:  # pragma: no cover  (static analysers / IDEs see the lazy names)
    from .candidates import *  # noqa: F401,F403
    from .catalog import *  # noqa: F401,F403
    from .interest import *  # noqa: F401,F403
    from .interest_build import *  # noqa: F401,F403
    from .pipeline import *  # noqa: F401,F403
    from .bundle import *  # noqa: F401,F403

# The __all__ of each lazily exported module (kept equal to it by tests/test_package.py).
_LAZY_EXPORTS = {
    "catalog": (
        "CATALOG_FILE", "MANIFEST_FILE", "GOOGLE_MAPS_URL", "CITY_TIMEZONES", "DEFAULT_TIMEZONE", "STATUS_ALIASES",
        "USED_COLUMNS", "DISPLAY_COLUMNS", "NUMERIC_COLUMNS", "BUNDLE_COLUMNS", "MAX_BUNDLE_PHOTOS", "CARD_PHOTOS",
        "DETAIL_PHOTOS", "SECTION_TITLES", "SEARCH_DISTANCE_WEIGHT", "SEARCH_TIERS",
        "parse_hours", "normalize_status", "text_series", "cold_start_scores", "photo_keys_by_place", "photo_url",
        "to_bundle_frame", "CityCatalog", "catalog_frames_equal",
    ),
    "candidates": (
        "place_dwell", "row_candidate", "distances_from", "slot_candidates", "extra_activities", "ExtraCandidates",
        "extra_candidates", "PlaceCandidate", "place_candidate",
    ),
    "pipeline": (
        "LIMITS", "DEFAULTS", "STOP_KINDS",
        "SlotSpec", "PlanParams", "PlanContext", "EditStop", "PlanInterest", "VariantState", "PlanResult",
        "normalize_params", "make_context", "to_request_echo", "from_request_echo", "resolve_interest", "build_plan",
        "sequence_request", "edit_stops", "sequence_candidates", "schedule", "insert_place",
    ),
    "interest": (
        "TASTE_WEIGHTS", "CSLS_K", "CSLS_PENALTY", "FAVOURITE_WEIGHT", "WANT_TO_GO_WEIGHT", "DEFAULT_STRENGTH",
        "MAX_SEEDS", "MAX_PROFILE_CLUSTERS", "MIN_PROFILE_SILHOUETTE", "MIN_PROFILE_CLUSTER_SIZE",
        "MIN_SEEDS_TO_CLUSTER", "AXIS_COLUMNS", "PRICE_AXIS", "AXIS_FILL", "QUALITY_SHRINKAGE_PRIOR",
        "MODE_FAVOURITES", "MODE_POPULARITY", "TEXT_FILE", "IMAGE_FILE", "HAS_IMAGE_FILE", "DENSITY_FILE",
        "FEATURES_FILE", "META_FILE", "ARTIFACT_FILES", "COLD_START_COLUMNS",
        "cold_start_available", "cold_start", "cold_start_map", "TasteArtifacts", "TasteScores", "resolve_seeds",
        "cluster_profiles", "taste_scores", "InterestResult", "interest_map", "warmup",
    ),
    "interest_build": (
        "TEXT_MODEL_DEFAULT", "clean_text", "parse_listish", "parse_ai_tags_json", "parse_tag_names",
        "quality_score_v2", "axis_matrix", "tag_lists", "l2_normalize_rows", "csls_density",
        "build_taste_artifacts", "taste_artifacts_from_files",
    ),
    "bundle": (
        "INTEREST_DIR", "INTEREST_FILES", "CONTENT_HASH_FORMAT", "BUNDLE_ID_RE", "CITY_SLUG_RE", "PHOTO_KEY_RE",
        "PLACE_ID_RE", "CITY_BBOXES", "MAX_CITY_RADIUS_KM", "KNOWN_THEME_GROUPS", "KNOWN_STATUSES", "REQUIRED_COLUMNS",
        "CSV_READ_OPTIONS", "BundleError", "BundleExistsError", "LoadedBundle",
        "slugify", "file_sha256", "catalog_content_sha256", "content_sha256", "read_source_csv", "layout_problems",
        "build_bundle", "validate_bundle", "load_bundle", "read_manifest", "resolve_bundle_dirs", "bundle_info",
    ),
}
_LAZY = {name: module for module, names in _LAZY_EXPORTS.items() for name in names}

__all__ = [*_core.__all__, *_routing.__all__, *_slots.__all__, *_messages.__all__, *_present.__all__,
           *_version.__all__, *_LAZY]
__version__ = _ALGORITHM_VERSION


def __getattr__(name: str):
    """Resolve a lazily exported name: import its module (once) and cache the value here."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY))

