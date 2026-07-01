from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import numpy as np
import pandas as pd

from . import location_recommender_utils as utils
from . import item_to_item_rerank as itr
from .common import normalize_matrix

TEXT_ONLY_WEIGHTS = {
    "semantic_similarity": 0.72,
    "visual_similarity": 0.00,
    "direct_image_similarity": 0.00,
    "tag_overlap": 0.10,
    "axis_similarity": 0.08,
    "quality_score": 0.07,
    "price_match": 0.03,
}

TEXT_VISUAL_WEIGHTS = {
    "semantic_similarity": 0.47,
    "visual_similarity": 0.30,
    "direct_image_similarity": 0.00,
    "tag_overlap": 0.08,
    "axis_similarity": 0.06,
    "quality_score": 0.06,
    "price_match": 0.03,
}

TEXT_VISUAL_DIRECT_WEIGHTS = {
    "semantic_similarity": 0.40,
    "visual_similarity": 0.18,
    "direct_image_similarity": 0.22,
    "tag_overlap": 0.07,
    "axis_similarity": 0.05,
    "quality_score": 0.05,
    "price_match": 0.03,
}

# First text+direct preset (2026-06-10 morning): TEXT_VISUAL_DIRECT_WEIGHTS with
# visual dropped and renormalized. Kept for A/B; superseded by TEXT_DIRECT_WEIGHTS
# below after the variant experiment on the completed labeling sprint.
TEXT_DIRECT_V1_WEIGHTS = {
    "semantic_similarity": 0.49,
    "visual_similarity": 0.00,
    "direct_image_similarity": 0.27,
    "tag_overlap": 0.08,
    "axis_similarity": 0.06,
    "quality_score": 0.06,
    "price_match": 0.04,
}

# Production text+direct preset — the "more_direct" experiment winner promoted on
# 2026-06-10 evening per the pre-registered gate: with the labeling sprint complete
# (510 pairs / 20 contexts, low_confidence false) it beat TEXT_DIRECT_V1 on BOTH
# feedback pairwise (0.6412 vs 0.6020) and non-text-cohort relevance (0.2573 vs
# 0.2483). See docs/implementation/experiment-text-direct-variants.md.
TEXT_DIRECT_WEIGHTS = {
    "semantic_similarity": 0.26,
    "visual_similarity": 0.00,
    "direct_image_similarity": 0.50,
    "tag_overlap": 0.08,
    "axis_similarity": 0.06,
    "quality_score": 0.06,
    "price_match": 0.04,
}

# Data-driven weights from the leave-k-out search (Task 1.4), percentile calibration.
# Found by Dirichlet random search maximizing NDCG@20 on synthetic KNN cohorts
# (see data/eval/weight_search_v1.md). NOT the default: those cohorts are
# text-cosine neighbourhoods, so the search over-weights semantic similarity.
# Available as an opt-in preset for A/B until real saved-list / feedback data
# can re-tune it without that bias.
TUNED_WEIGHTS_V1 = {
    "semantic_similarity": 0.887453,
    "visual_similarity": 0.048810,
    "direct_image_similarity": 0.000622,
    "tag_overlap": 0.000040,
    "axis_similarity": 0.000028,
    "quality_score": 0.017933,
    "price_match": 0.045114,
}

DEFAULT_WEIGHTS = TEXT_DIRECT_WEIGHTS
MISSING_VISUAL_POLICIES = {"redistribute", "light_penalty", "zero", "exclude"}
HUBNESS_METHODS = {"csls", "none"}
PROFILE_QUOTA_MODES = {"weighted", "global"}
QUALITY_SCORE_VERSIONS = {"v2", "v1_legacy"}
SEED_AGGREGATIONS = {"centroid", "max_seed", "top2_mean"}
MODALITY_FUSIONS = {"late", "raw_blend"}
RANKERS = {"linear", "lgbm"}


def _first_existing_path(*paths: Path) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[0]


def _latest_existing_path(directory: Path, pattern: str, fallback: Path) -> Path:
    matches = sorted(directory.glob(pattern)) if directory.exists() else []
    return matches[-1] if matches else fallback


PACKAGE_DIR = Path(__file__).resolve().parent
HANDOFF_ROOT = PACKAGE_DIR.parents[1]
VISUAL_EMBEDDING_STORE_DIR = PACKAGE_DIR / "data" / "visual_photo_profiles" / "visual_embedding_store"
DIRECT_IMAGE_EMBEDDING_STORE_DIR = PACKAGE_DIR / "data" / "direct_image_embeddings" / "place_embedding_store"


DEFAULT_LOCATIONS_CSV = _first_existing_path(
    # Default model = gpt-5.4-mini (food_drink bench). Dashboard selector can switch models.
    PACKAGE_DIR / "data" / "locations_food_drink_gpt-5.4-mini.csv",
    # Task 2.7: enriched catalog (coordinates + global_cluster) is preferred when present.
    PACKAGE_DIR / "data" / "locations_enriched.csv",
    HANDOFF_ROOT / "artifacts" / "locations.csv",
    utils.PROJECT_ROOT / "artifacts" / "locations.csv",
    utils.PROJECT_ROOT
    / "data_scraping"
    / "output"
    / "ai_location_summaries"
    / "final"
    / "final_ai_dataframe_with_map_scores_latest.csv",
    utils.PROJECT_ROOT
    / "data_scraping"
    / "output"
    / "backend_export"
    / "backend_dataset_metadata_preview"
    / "locations.csv",
)
DEFAULT_EMBEDDINGS_NPY = _first_existing_path(
    # Default model = gpt-5.4-mini (food_drink bench). Old bucharest run kept as fallback.
    PACKAGE_DIR / "data" / "embedding_store" / "location_embeddings_food_drink_gpt-5.4-mini.npy",
    HANDOFF_ROOT / "artifacts" / "location_embeddings_20260531T173837Z.npy",
    PACKAGE_DIR / "data" / "embedding_store" / "location_embeddings_20260531T173837Z.npy",
)
DEFAULT_METADATA_CSV = _first_existing_path(
    # Default model = gpt-5.4-mini (food_drink bench). Old bucharest run kept as fallback.
    PACKAGE_DIR / "data" / "embedding_store" / "location_embeddings_food_drink_gpt-5.4-mini_metadata.csv",
    HANDOFF_ROOT / "artifacts" / "location_embeddings_20260531T173837Z_metadata.csv",
    PACKAGE_DIR / "data" / "embedding_store" / "location_embeddings_20260531T173837Z_metadata.csv",
)
DEFAULT_VISUAL_EMBEDDINGS_NPY = _first_existing_path(
    HANDOFF_ROOT / "artifacts" / "visual_place_embeddings_visual_place_embeddings_20260606T145954Z.npy",
    utils.PROJECT_ROOT / "artifacts" / "visual_place_embeddings_visual_place_embeddings_20260606T145954Z.npy",
    _latest_existing_path(
        VISUAL_EMBEDDING_STORE_DIR,
        "visual_place_embeddings_*.npy",
        VISUAL_EMBEDDING_STORE_DIR / "visual_place_embeddings_visual_place_embeddings_20260606T145954Z.npy",
    ),
)
DEFAULT_VISUAL_METADATA_PATH = _first_existing_path(
    HANDOFF_ROOT / "artifacts" / "visual_place_embeddings_visual_place_embeddings_20260606T145954Z_metadata.parquet",
    utils.PROJECT_ROOT / "artifacts" / "visual_place_embeddings_visual_place_embeddings_20260606T145954Z_metadata.parquet",
    _latest_existing_path(
        VISUAL_EMBEDDING_STORE_DIR,
        "visual_place_embeddings_*_metadata.parquet",
        VISUAL_EMBEDDING_STORE_DIR / "visual_place_embeddings_visual_place_embeddings_20260606T145954Z_metadata.parquet",
    ),
    _latest_existing_path(
        VISUAL_EMBEDDING_STORE_DIR,
        "visual_place_embeddings_*_metadata.csv",
        VISUAL_EMBEDDING_STORE_DIR / "visual_place_embeddings_visual_place_embeddings_20260606T145954Z_metadata.csv",
    ),
)
DEFAULT_VISUAL_PROFILES_CSV = _first_existing_path(
    HANDOFF_ROOT / "artifacts" / "visual_place_profiles_with_embedding_refs_visual_place_embeddings_20260606T145954Z.csv",
    utils.PROJECT_ROOT / "artifacts" / "visual_place_profiles_with_embedding_refs_visual_place_embeddings_20260606T145954Z.csv",
    _latest_existing_path(
        VISUAL_EMBEDDING_STORE_DIR,
        "visual_place_profiles_with_embedding_refs_*.csv",
        VISUAL_EMBEDDING_STORE_DIR / "visual_place_profiles_with_embedding_refs_visual_place_embeddings_20260606T145954Z.csv",
    ),
)
# Pin the direct-image defaults to a SINGLE OpenCLIP run. Globbing for the "latest" file
# became unsafe once a second store (SigLIP2) was added: the .npy glob picked SigLIP2 (its
# name sorts last) while the metadata glob picked OpenCLIP's parquet (SigLIP2's metadata is
# .csv), so SigLIP2 vectors loaded with OpenCLIP's place_id->row mapping. Keep npy +
# metadata + profiles from the same run. To switch the visual channel, change this run id
# (and make sure the matching metadata file exists) — don't reintroduce a "*" glob.
_DIRECT_DEFAULT_RUN_ID = "siglip2_naflex_v1"
DEFAULT_DIRECT_IMAGE_EMBEDDINGS_NPY = _first_existing_path(
    HANDOFF_ROOT / "artifacts" / f"direct_place_image_embeddings_{_DIRECT_DEFAULT_RUN_ID}.npy",
    utils.PROJECT_ROOT / "artifacts" / f"direct_place_image_embeddings_{_DIRECT_DEFAULT_RUN_ID}.npy",
    DIRECT_IMAGE_EMBEDDING_STORE_DIR / f"direct_place_image_embeddings_{_DIRECT_DEFAULT_RUN_ID}.npy",
)
DEFAULT_DIRECT_IMAGE_METADATA_PATH = _first_existing_path(
    HANDOFF_ROOT / "artifacts" / f"direct_place_image_embeddings_{_DIRECT_DEFAULT_RUN_ID}_metadata.parquet",
    utils.PROJECT_ROOT / "artifacts" / f"direct_place_image_embeddings_{_DIRECT_DEFAULT_RUN_ID}_metadata.parquet",
    DIRECT_IMAGE_EMBEDDING_STORE_DIR / f"direct_place_image_embeddings_{_DIRECT_DEFAULT_RUN_ID}_metadata.parquet",
)
DEFAULT_DIRECT_IMAGE_PROFILES_CSV = _first_existing_path(
    HANDOFF_ROOT / "artifacts" / f"direct_place_image_profiles_{_DIRECT_DEFAULT_RUN_ID}.csv",
    utils.PROJECT_ROOT / "artifacts" / f"direct_place_image_profiles_{_DIRECT_DEFAULT_RUN_ID}.csv",
    DIRECT_IMAGE_EMBEDDING_STORE_DIR / f"direct_place_image_profiles_{_DIRECT_DEFAULT_RUN_ID}.csv",
)


@dataclass(frozen=True)
class RecommenderConfig:
    algorithm_version: str = "location_recommender_v4_more_direct"
    embedding_run_id: str = "food_drink_gpt-5.4-mini"
    favorites_weight: float = 1.0
    want_to_go_weight: float = 0.55
    min_saved_for_personalization: int = 3
    # Profile clustering (Task 2.6): allow up to 6 tastes, but scale the real cap
    # with seed size (~3 seeds per cluster); only split when the silhouette clears
    # 0.10 (above noise) so single-taste lists are not over-clustered. Cold-start
    # over-split guards (2026-06-27, deep-research): an additional MIND-style log2(n)
    # cap on K and a minimum cluster size (reject splits that isolate a singleton).
    max_profile_clusters: int = 6
    min_profile_silhouette: float = 0.10
    min_profile_cluster_size: int = 2
    min_map_visibility_score: float = 20.0
    exclude_low_confidence: bool = True
    fallback_quality_weight: float = 0.85
    fallback_similarity_weight: float = 0.15
    # Missing-modality policy (Task 2.4): "redistribute" (default) drops the missing
    # modality from that row's blend and renormalizes weights over what is present
    # (no free constant); "light_penalty" gives the legacy constant; "zero" gives 0;
    # "exclude" drops candidates lacking the modality.
    missing_visual_policy: str = "redistribute"
    missing_visual_similarity_norm: float = 0.45
    missing_direct_image_policy: str = "redistribute"
    missing_direct_image_similarity_norm: float = 0.45
    # Score-component calibration (Task 1.3): "percentile" ranks each embedding
    # similarity over the candidate pool (full [0,1] range, comparable to the
    # other [0,1] components); "minmax_legacy" is the old (cos+1)/2 mapping.
    calibration: str = "percentile"
    # Stage-0 cross-channel calibration: percentile-rank EVERY blended component
    # (tag_overlap / axis / quality / price / subtype / geo) over the candidate
    # pool before the weighted sum — not just the embedding-similarity channels,
    # which are already percentile-calibrated upstream. This puts all channels on
    # the identical uniform [0,1] scale so a component's blend weight equals its
    # true ranking influence. Without it, low-variance raw channels (quality/axis)
    # are silently under-weighted and the high-variance ones (direct_image) inflate
    # past their nominal weight (measured: direct_image 0.50 -> 62.6% of influence;
    # with calibration -> 51.7%, tag_overlap 3.4% -> 8.1%).
    #
    # OPT-IN (default off): the live default keeps the byte-identical raw-scale blend
    # the production weights were tuned on, and stays compatible with the offline
    # weight-search parity (which reconstructs the blend from cached raw columns).
    # Enabled inside the Stage-1 per-category weight profiles, where honest
    # weight==influence is required for the per-category overrides to mean what they say.
    calibrate_components: bool = False
    # Stage-1.5 calibrated re-rank (Steck, RecSys 2018): greedy post-rank that makes
    # the final list's category mix match the user's favourite-category mix, trading
    # relevance against KL(favourite_dist || list_dist). lambda 0 = off (default);
    # higher = honour the category mix harder. The candidate-affinity component
    # (category_affinity weight) lifts rare on-taste categories into the top
    # `calibrated_rerank_pool` scored candidates; this re-rank then guarantees their
    # share in the top `limit`. Replaces quota/MMR when active.
    calibrated_rerank_lambda: float = 0.0
    calibrated_rerank_pool: int = 600
    calibrated_rerank_category_col: str = "primary_type"
    # Stage-2 context boosts (Layer 2): situation-dependent MULTIPLIERS on the blended
    # relevance score, applied after the blend and before the calibrated re-rank. Keyword
    # matches on primary_type + tags scale a place up/down for the time of day / weather
    # (e.g. morning -> cafes up & bars down; rainy -> terraces down). "" = off (no-op).
    # Multiplicative so each boost is an independent, scale-invariant knob that never
    # silently unbalances the Layer-1 weights. See CONTEXT_TIME_BOOSTS / CONTEXT_WEATHER_BOOSTS.
    context_time: str = ""
    context_weather: str = ""
    # --- ranking quality (Task 2.1) ---
    # CSLS hubness correction on per-profile semantic similarity: subtract
    # hubness_penalty * (mean cosine to k nearest catalog neighbours) before
    # calibration, so popular "hub" places stop inflating every profile.
    hubness_method: str = "csls"      # "csls" | "none"
    hubness_k: int = 10
    hubness_penalty: float = 0.5
    # Per-profile interleaving: "weighted" gives each profile a quota of the final
    # list proportional to its profile_weight, round-robin filled (so a sparse
    # taste is not buried by a dense one); "global" is the legacy single sort.
    profile_quota_mode: str = "weighted"   # "weighted" | "global"
    # MMR diversity re-rank of the final list; 1.0 = pure relevance (off),
    # lower = more diverse. Skipped when limit > mmr_max_items (full-catalog scoring).
    mmr_lambda: float = 0.7
    mmr_max_items: int = 200
    # --- quality prior (Task 2.2) ---
    # "v2" = Bayesian-shrunk rating (v/(v+m)*R + m/(v+m)*C); "v1_legacy" = the old
    # popularity-heavy blend (60% map_visibility). m = quality_shrinkage_prior.
    quality_score_version: str = "v2"   # "v2" | "v1_legacy"
    quality_shrinkage_prior: float = 25.0
    # map_visibility is now a weak optional signal: the hard candidate filter is
    # opt-in (default off) so niche-but-relevant places are not pre-excluded.
    apply_map_visibility_filter: bool = False
    # Restrict the candidate pool to a single city, matched against the optional
    # `city` column in the locations CSV (e.g. "Bucharest" / "Tbilisi"). "" = all
    # cities. No-op when the locations have no `city` column (other presets).
    city_filter: str = ""
    # Restrict the candidate pool to one theme group ("food_drink" / "things_to_do"),
    # matched against the optional `theme_group` column in the combined locations CSV.
    # "" = all groups. No-op when the locations have no `theme_group` column (other presets).
    theme_group_filter: str = ""
    # Cross-theme injection (2026-06-28; docs/superpowers/specs/2026-06-28-cross-theme-injection-design.md):
    # mix a small, vibe-relevant amount of the OTHER theme_group into a single-theme user's list.
    # Ships OFF. mode "auto" = adaptive to the profile's theme balance; "manual" = the slider value
    # used DIRECTLY as an absolute fraction (it dominates — it is not a multiplier of the adaptive base).
    cross_theme_inject_mode: str = "off"             # "off" | "auto" | "manual"
    cross_theme_inject_frac: float = 0.15            # manual ABSOLUTE fraction (the slider value)
    cross_theme_inject_max: float = 0.20             # adaptive (auto-mode) ceiling
    cross_theme_vibe_floor: float = 0.45             # min vibe_fit to inject (relevance gate)
    cross_theme_vibe_weights: tuple = (0.5, 0.5)     # (axis_similarity, tag_overlap) — balanced
    # Appropriateness denylist — a HARD categorical gate applied BEFORE the vibe-fit floor.
    # vibe_fit captures MOOD, not suitability (a "calm casino" scores high on the calm axes), so
    # gambling / adult venues need a block that no vibe score can override. Matched (case-insensitive)
    # against the model's tags (recommendation_tags) and the Google primary_type. NB: the noisy
    # `adults_only` tag is deliberately NOT here — it also marks paragliding/karting/ziplines.
    # Empty tuples disable the gate (opt-out). Only the primary_type list catches venues the model
    # under-tagged. Tune in the dashboard's "Advanced settings".
    cross_theme_inject_deny_tags: tuple = ("casino_gambling",)
    cross_theme_inject_deny_primary_types: tuple = (
        "casino", "casino hotel", "gambling house", "gambling", "betting agency",
        "adult entertainment club", "adult entertainment", "strip club", "sex shop",
        "brothel", "escort service",
    )
    # Cold-start fallback: stratify the top-N by global_cluster for variety.
    fallback_stratify: bool = True
    # Geo-distance feature (Task 2.3): score = exp(-distance_km / geo_decay_km).
    # Active only when user_lat/user_lon are passed to recommend() AND the
    # "geo_distance" weight > 0 (default-absent from presets => opt-in).
    geo_decay_km: float = 2.0
    # Robust profile centroids (Task 2.5): down-weight outlier favourites (members
    # below the 25th-percentile cosine to the plain centroid, factor 0.25) before
    # re-averaging, so one off-taste favourite doesn't drag the profile. Skipped for
    # <=3 members. DEFAULT OFF: the current synthetic cohorts + the single feedback
    # context are coherent (no genuine outliers), so trimming only shifts the
    # centroid toward the tightest members and slightly hurts every metric
    # (recall −1.4pp knn_text, feedback pairwise −2.4pp). The machinery is tested
    # and ready; enable once real mixed-taste saved lists exist to benefit from it.
    robust_centroids: bool = False
    # --- text+direct experiment knobs (see docs/implementation/experiment-text-direct-variants.md) ---
    # seed_aggregation: how a profile's seeds are matched against candidates WITHIN one profile.
    # "centroid" (default) — weighted centroid of the profile's seeds. Deep-research 2026-06-27
    # (docs/reports/2026-06-27-multi-interest-profile-representation-research.md) found the within-profile
    # choice barely affects relevance once seeds are clustered into coherent profiles (PinnerSage:
    # centroid≈medoid), so centroid is the evidenced default and the real lever is the clustering itself.
    # "max_seed" — best single seed (spiky, can promote a fluke single-seed match — unevidenced here);
    # "top2_mean" — mean of the two best seeds (needs >= 3 seeds, else falls back to centroid).
    seed_aggregation: str = "centroid"
    # modality_fusion: "late" (default) — calibrate text and direct-image similarities
    # separately, then blend; "raw_blend" — weight-average the RAW cosines first
    # (mathematically equivalent to concatenating the two embedding spaces) and
    # calibrate the fused similarity once. Personalized flow only.
    modality_fusion: str = "late"
    # adaptive_direct_weight: per profile, scale the direct-image weight by how
    # visually coherent the profile's seeds are relative to their text coherence
    # (both measured as lift over the catalog-average pairwise similarity of the
    # respective space). Ratio clipped to [adaptive_min_scale, adaptive_max_scale].
    adaptive_direct_weight: bool = False
    adaptive_min_scale: float = 0.5
    adaptive_max_scale: float = 1.5
    # Venue-subtype component gating: apply subtype_match only when the dominant
    # seed subtype covers >= this share of the profile's known-subtype seeds
    # (a bistro-only profile enforces category; an eclectic vibe profile doesn't).
    # 0.0 = always apply (ungated).
    subtype_coherence_gate: float = 0.0
    # Ranking head (Task 3.2): "linear" — the hand/preset-weighted blend (default);
    # "lgbm" — the trained LambdaRank model replaces the blend as the relevance
    # score inside the personalized flow (quotas/MMR still apply on top). Requires
    # a RankerModel attached to the recommender (see ranker.py); fallback and
    # item-to-item paths always stay linear.
    ranker: str = "linear"
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    @classmethod
    def from_dict(cls, config: dict[str, Any] | None = None) -> "RecommenderConfig":
        if not config:
            return cls()
        data = dict(config)
        if "weights" in data and data["weights"] is not None:
            data["weights"] = {**DEFAULT_WEIGHTS, **dict(data["weights"])}
        if data.get("missing_visual_policy") not in (None, *MISSING_VISUAL_POLICIES):
            raise ValueError(f"Unsupported missing_visual_policy: {data.get('missing_visual_policy')}")
        if data.get("missing_direct_image_policy") not in (None, *MISSING_VISUAL_POLICIES):
            raise ValueError(f"Unsupported missing_direct_image_policy: {data.get('missing_direct_image_policy')}")
        if data.get("calibration") not in (None, *CALIBRATION_METHODS):
            raise ValueError(f"Unsupported calibration: {data.get('calibration')}")
        if data.get("hubness_method") not in (None, *HUBNESS_METHODS):
            raise ValueError(f"Unsupported hubness_method: {data.get('hubness_method')}")
        if data.get("profile_quota_mode") not in (None, *PROFILE_QUOTA_MODES):
            raise ValueError(f"Unsupported profile_quota_mode: {data.get('profile_quota_mode')}")
        if data.get("quality_score_version") not in (None, *QUALITY_SCORE_VERSIONS):
            raise ValueError(f"Unsupported quality_score_version: {data.get('quality_score_version')}")
        if data.get("seed_aggregation") not in (None, *SEED_AGGREGATIONS):
            raise ValueError(f"Unsupported seed_aggregation: {data.get('seed_aggregation')}")
        if data.get("modality_fusion") not in (None, *MODALITY_FUSIONS):
            raise ValueError(f"Unsupported modality_fusion: {data.get('modality_fusion')}")
        if data.get("ranker") not in (None, *RANKERS):
            raise ValueError(f"Unsupported ranker: {data.get('ranker')}")
        return cls(**data)

    def with_overrides(self, overrides: dict[str, Any]) -> "RecommenderConfig":
        if not overrides:
            return self
        allowed = set(self.__dataclass_fields__)
        data = {key: value for key, value in overrides.items() if key in allowed and value is not None}
        if "weights" in data:
            data["weights"] = {**self.weights, **dict(data["weights"])}
        if data.get("missing_visual_policy") not in (None, *MISSING_VISUAL_POLICIES):
            raise ValueError(f"Unsupported missing_visual_policy: {data.get('missing_visual_policy')}")
        if data.get("missing_direct_image_policy") not in (None, *MISSING_VISUAL_POLICIES):
            raise ValueError(f"Unsupported missing_direct_image_policy: {data.get('missing_direct_image_policy')}")
        if data.get("calibration") not in (None, *CALIBRATION_METHODS):
            raise ValueError(f"Unsupported calibration: {data.get('calibration')}")
        if data.get("hubness_method") not in (None, *HUBNESS_METHODS):
            raise ValueError(f"Unsupported hubness_method: {data.get('hubness_method')}")
        if data.get("profile_quota_mode") not in (None, *PROFILE_QUOTA_MODES):
            raise ValueError(f"Unsupported profile_quota_mode: {data.get('profile_quota_mode')}")
        if data.get("quality_score_version") not in (None, *QUALITY_SCORE_VERSIONS):
            raise ValueError(f"Unsupported quality_score_version: {data.get('quality_score_version')}")
        if data.get("seed_aggregation") not in (None, *SEED_AGGREGATIONS):
            raise ValueError(f"Unsupported seed_aggregation: {data.get('seed_aggregation')}")
        if data.get("modality_fusion") not in (None, *MODALITY_FUSIONS):
            raise ValueError(f"Unsupported modality_fusion: {data.get('modality_fusion')}")
        if data.get("ranker") not in (None, *RANKERS):
            raise ValueError(f"Unsupported ranker: {data.get('ranker')}")
        return replace(self, **data)


def _infer_embedding_run_id(path: str | Path) -> str | None:
    match = re.search(r"location_embeddings_(.+)\.npy$", str(path))
    return match.group(1) if match else None


def _read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _nonempty_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    text = str(path).strip()
    if not text or text.lower() in {"none", "null", "-"}:
        return None
    return Path(text)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number):
        return default
    return number


def _round_float(value: Any, digits: int = 6) -> float:
    return round(_as_float(value), digits)


# Score-component calibration methods (Task 1.3).
CALIBRATION_METHODS = {"minmax_legacy", "percentile"}


def _minmax_norm(values: np.ndarray) -> np.ndarray:
    """Legacy normalization: map cosine similarity [-1, 1] onto [0, 1]."""
    return np.clip((np.asarray(values, dtype=float) + 1) / 2, 0, 1)


def _percentile_norm(values: np.ndarray) -> np.ndarray:
    """Percentile-rank normalization over the candidate pool (rank / n).

    Maps each value to its empirical CDF position in [0, 1], so every component
    occupies the full range regardless of its raw scale. Ties get the average
    rank. A single value maps to 1.0.
    """
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr
    return pd.Series(arr).rank(method="average", pct=True).to_numpy()


def _calibrate_similarity(values: np.ndarray, method: str) -> np.ndarray:
    """Calibrate raw cosine similarities into a [0, 1] component score."""
    if method == "percentile":
        return _percentile_norm(values)
    return _minmax_norm(values)


# Component value columns already on the percentile [0,1] scale (calibrated upstream
# in _score_candidates_for_profile). Stage-0 `calibrate_components` percentile-norms
# every OTHER blended component so the weights equal true ranking influence.
_PRECALIBRATED_VALUE_COLS = frozenset(
    {"semantic_similarity_norm", "visual_similarity_norm", "direct_image_similarity_norm"}
)

# Stage-2 context boosts (Layer 2). Keyword -> multiplier maps; a candidate's boost is the
# product of every multiplier whose keyword appears in its (primary_type + tags) text, then
# clipped to a sane band. Hand-set "analyst knobs" — tune freely; "" / unknown key = no-op.
CONTEXT_TIME_BOOSTS: dict[str, dict[str, float]] = {
    "morning": {"cafe": 1.4, "coffee": 1.4, "breakfast": 1.4, "brunch": 1.4, "bakery": 1.3,
                "juice": 1.3, "tea": 1.2, "bar": 0.6, "pub": 0.6, "club": 0.4, "cocktail": 0.5,
                "wine": 0.7, "night": 0.5},
    "afternoon": {},  # neutral baseline
    "evening": {"restaurant": 1.3, "bistro": 1.3, "steak": 1.2, "wine": 1.25, "bar": 1.2,
                "pub": 1.15, "cocktail": 1.2, "breakfast": 0.6, "brunch": 0.6, "juice": 0.7},
    "late_night": {"bar": 1.5, "pub": 1.4, "club": 1.5, "cocktail": 1.45, "wine": 1.2,
                   "cafe": 0.5, "coffee": 0.5, "bakery": 0.4, "breakfast": 0.3, "brunch": 0.4,
                   "juice": 0.5, "tea": 0.6},
}
CONTEXT_WEATHER_BOOSTS: dict[str, dict[str, float]] = {
    "": {},
    "rainy": {"terrace": 0.6, "garden": 0.6, "rooftop": 0.6, "beach": 0.5, "cozy": 1.2},
    "cold": {"terrace": 0.7, "rooftop": 0.7, "garden": 0.8, "ice cream": 0.6, "gelato": 0.6,
             "tea": 1.2, "soup": 1.2, "cozy": 1.2},
    "hot": {"ice cream": 1.5, "gelato": 1.5, "terrace": 1.3, "rooftop": 1.3, "garden": 1.2,
            "juice": 1.25, "beach": 1.3, "bar": 1.1},
}
CONTEXT_BOOST_CLIP = (0.25, 3.0)


def _haversine_km(lat1: float, lon1: float, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Great-circle distance in km from one point to arrays of lat/lon (Earth R=6371)."""
    r = 6371.0088
    lat1r = math.radians(lat1)
    lon1r = math.radians(lon1)
    lat2r = np.radians(np.asarray(lat2, dtype=float))
    lon2r = np.radians(np.asarray(lon2, dtype=float))
    dlat = lat2r - lat1r
    dlon = lon2r - lon1r
    a = np.sin(dlat / 2) ** 2 + math.cos(lat1r) * np.cos(lat2r) * np.sin(dlon / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _dedupe_preserve_order(values: list[str] | None) -> list[str]:
    seen = set()
    result = []
    for value in values or []:
        if value is None:
            continue
        value = str(value)
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _agglomerative_cosine(n_clusters: int):
    from sklearn.cluster import AgglomerativeClustering

    try:
        return AgglomerativeClustering(n_clusters=n_clusters, metric="cosine", linkage="average")
    except TypeError:
        return AgglomerativeClustering(n_clusters=n_clusters, affinity="cosine", linkage="average")


def _normalized_score_weights(
    weights: dict[str, float],
    visual_enabled: bool,
    direct_image_enabled: bool = False,
) -> dict[str, float]:
    selected = {key: max(_as_float(value), 0.0) for key, value in weights.items()}
    if not visual_enabled:
        selected["visual_similarity"] = 0.0
    if not direct_image_enabled:
        selected["direct_image_similarity"] = 0.0
    total = sum(selected.values())
    if total <= 0:
        return dict(TEXT_ONLY_WEIGHTS)
    return {key: value / total for key, value in selected.items()}


class LocationRecommender:
    """Backend-ready in-memory recommender.

    This class never calls OpenAI at request time. It only uses precomputed
    location embeddings and metadata loaded at service startup.
    """

    def __init__(
        self,
        locations: pd.DataFrame,
        embedding_matrix: np.ndarray,
        embedding_metadata: pd.DataFrame,
        visual_embedding_matrix: np.ndarray | None = None,
        visual_embedding_metadata: pd.DataFrame | None = None,
        visual_profiles: pd.DataFrame | None = None,
        direct_image_embedding_matrix: np.ndarray | None = None,
        direct_image_embedding_metadata: pd.DataFrame | None = None,
        direct_image_profiles: pd.DataFrame | None = None,
        config: RecommenderConfig | dict[str, Any] | None = None,
    ):
        self.config = config if isinstance(config, RecommenderConfig) else RecommenderConfig.from_dict(config)
        self.embedding_matrix = np.asarray(embedding_matrix, dtype=np.float32)
        self.normalized_embeddings = normalize_matrix(self.embedding_matrix)
        self.embedding_metadata = embedding_metadata.copy()
        self.visual_embedding_matrix = (
            np.asarray(visual_embedding_matrix, dtype=np.float32) if visual_embedding_matrix is not None else None
        )
        self.normalized_visual_embeddings = (
            normalize_matrix(self.visual_embedding_matrix) if self.visual_embedding_matrix is not None else None
        )
        self.visual_embedding_metadata = (
            visual_embedding_metadata.copy() if visual_embedding_metadata is not None else pd.DataFrame()
        )
        self.visual_profiles = visual_profiles.copy() if visual_profiles is not None else pd.DataFrame()
        self.direct_image_embedding_matrix = (
            np.asarray(direct_image_embedding_matrix, dtype=np.float32)
            if direct_image_embedding_matrix is not None
            else None
        )
        self.normalized_direct_image_embeddings = (
            normalize_matrix(self.direct_image_embedding_matrix)
            if self.direct_image_embedding_matrix is not None
            else None
        )
        self.direct_image_embedding_metadata = (
            direct_image_embedding_metadata.copy() if direct_image_embedding_metadata is not None else pd.DataFrame()
        )
        self.direct_image_profiles = direct_image_profiles.copy() if direct_image_profiles is not None else pd.DataFrame()
        self.locations = self._prepare_locations(locations, self.embedding_metadata)
        self.place_id_to_index = {place_id: idx for idx, place_id in enumerate(self.locations["place_id"])}
        # Optional learned ranking head (Task 3.2); attach via attach_ranker_model().
        self.ranker_model = None

    def attach_ranker_model(self, model) -> None:
        """Attach a trained RankerModel (ranker.py); used when cfg.ranker == 'lgbm'."""
        self.ranker_model = model

    @classmethod
    def from_artifacts(
        cls,
        locations_csv: str | Path = DEFAULT_LOCATIONS_CSV,
        embeddings_npy: str | Path = DEFAULT_EMBEDDINGS_NPY,
        metadata_csv: str | Path = DEFAULT_METADATA_CSV,
        visual_embeddings_npy: str | Path | None = DEFAULT_VISUAL_EMBEDDINGS_NPY,
        visual_metadata_path: str | Path | None = DEFAULT_VISUAL_METADATA_PATH,
        visual_profiles_csv: str | Path | None = DEFAULT_VISUAL_PROFILES_CSV,
        direct_image_embeddings_npy: str | Path | None = DEFAULT_DIRECT_IMAGE_EMBEDDINGS_NPY,
        direct_image_metadata_path: str | Path | None = DEFAULT_DIRECT_IMAGE_METADATA_PATH,
        direct_image_profiles_csv: str | Path | None = DEFAULT_DIRECT_IMAGE_PROFILES_CSV,
        config: RecommenderConfig | dict[str, Any] | None = None,
    ) -> "LocationRecommender":
        locations_csv = Path(locations_csv)
        embeddings_npy = Path(embeddings_npy)
        metadata_csv = Path(metadata_csv)

        locations = pd.read_csv(locations_csv)
        embedding_matrix = np.load(embeddings_npy)
        metadata = pd.read_csv(metadata_csv)
        visual_embedding_matrix, visual_metadata, visual_profiles = cls._load_visual_artifacts(
            visual_embeddings_npy=visual_embeddings_npy,
            visual_metadata_path=visual_metadata_path,
            visual_profiles_csv=visual_profiles_csv,
        )
        direct_image_embedding_matrix, direct_image_metadata, direct_image_profiles = cls._load_direct_image_artifacts(
            direct_image_embeddings_npy=direct_image_embeddings_npy,
            direct_image_metadata_path=direct_image_metadata_path,
            direct_image_profiles_csv=direct_image_profiles_csv,
        )

        cfg = config if isinstance(config, RecommenderConfig) else RecommenderConfig.from_dict(config)
        inferred_run_id = _infer_embedding_run_id(embeddings_npy)
        explicit_run_id = isinstance(config, RecommenderConfig) or (
            isinstance(config, dict) and "embedding_run_id" in config
        )
        if inferred_run_id and not explicit_run_id:
            cfg = replace(cfg, embedding_run_id=inferred_run_id)

        return cls(
            locations,
            embedding_matrix,
            metadata,
            visual_embedding_matrix,
            visual_metadata,
            visual_profiles,
            direct_image_embedding_matrix,
            direct_image_metadata,
            direct_image_profiles,
            cfg,
        )

    @classmethod
    def from_dataframes(
        cls,
        locations: pd.DataFrame,
        embeddings_npy: str | Path = DEFAULT_EMBEDDINGS_NPY,
        metadata_csv: str | Path = DEFAULT_METADATA_CSV,
        visual_embeddings_npy: str | Path | None = DEFAULT_VISUAL_EMBEDDINGS_NPY,
        visual_metadata_path: str | Path | None = DEFAULT_VISUAL_METADATA_PATH,
        visual_profiles_csv: str | Path | None = DEFAULT_VISUAL_PROFILES_CSV,
        direct_image_embeddings_npy: str | Path | None = DEFAULT_DIRECT_IMAGE_EMBEDDINGS_NPY,
        direct_image_metadata_path: str | Path | None = DEFAULT_DIRECT_IMAGE_METADATA_PATH,
        direct_image_profiles_csv: str | Path | None = DEFAULT_DIRECT_IMAGE_PROFILES_CSV,
        config: RecommenderConfig | dict[str, Any] | None = None,
    ) -> "LocationRecommender":
        """Build recommender from a DB-backed locations dataframe plus embedding artifacts."""
        embeddings_npy = Path(embeddings_npy)
        metadata_csv = Path(metadata_csv)
        embedding_matrix = np.load(embeddings_npy)
        metadata = pd.read_csv(metadata_csv)
        visual_embedding_matrix, visual_metadata, visual_profiles = cls._load_visual_artifacts(
            visual_embeddings_npy=visual_embeddings_npy,
            visual_metadata_path=visual_metadata_path,
            visual_profiles_csv=visual_profiles_csv,
        )
        direct_image_embedding_matrix, direct_image_metadata, direct_image_profiles = cls._load_direct_image_artifacts(
            direct_image_embeddings_npy=direct_image_embeddings_npy,
            direct_image_metadata_path=direct_image_metadata_path,
            direct_image_profiles_csv=direct_image_profiles_csv,
        )

        cfg = config if isinstance(config, RecommenderConfig) else RecommenderConfig.from_dict(config)
        inferred_run_id = _infer_embedding_run_id(embeddings_npy)
        explicit_run_id = isinstance(config, RecommenderConfig) or (
            isinstance(config, dict) and "embedding_run_id" in config
        )
        if inferred_run_id and not explicit_run_id:
            cfg = replace(cfg, embedding_run_id=inferred_run_id)
        return cls(
            locations,
            embedding_matrix,
            metadata,
            visual_embedding_matrix,
            visual_metadata,
            visual_profiles,
            direct_image_embedding_matrix,
            direct_image_metadata,
            direct_image_profiles,
            cfg,
        )

    @staticmethod
    def _load_visual_artifacts(
        visual_embeddings_npy: str | Path | None,
        visual_metadata_path: str | Path | None,
        visual_profiles_csv: str | Path | None,
    ) -> tuple[np.ndarray | None, pd.DataFrame | None, pd.DataFrame | None]:
        embeddings_path = _nonempty_path(visual_embeddings_npy)
        metadata_path = _nonempty_path(visual_metadata_path)
        profiles_path = _nonempty_path(visual_profiles_csv)

        if embeddings_path is None or metadata_path is None or not embeddings_path.exists() or not metadata_path.exists():
            return None, None, None

        visual_matrix = np.load(embeddings_path)
        visual_metadata = _read_table(metadata_path)
        visual_profiles = pd.read_csv(profiles_path) if profiles_path is not None and profiles_path.exists() else None
        return visual_matrix, visual_metadata, visual_profiles

    @staticmethod
    def _load_direct_image_artifacts(
        direct_image_embeddings_npy: str | Path | None,
        direct_image_metadata_path: str | Path | None,
        direct_image_profiles_csv: str | Path | None,
    ) -> tuple[np.ndarray | None, pd.DataFrame | None, pd.DataFrame | None]:
        embeddings_path = _nonempty_path(direct_image_embeddings_npy)
        metadata_path = _nonempty_path(direct_image_metadata_path)
        profiles_path = _nonempty_path(direct_image_profiles_csv)

        if embeddings_path is None or metadata_path is None or not embeddings_path.exists() or not metadata_path.exists():
            return None, None, None

        direct_matrix = np.load(embeddings_path)
        direct_metadata = _read_table(metadata_path)
        direct_profiles = pd.read_csv(profiles_path) if profiles_path is not None and profiles_path.exists() else None
        return direct_matrix, direct_metadata, direct_profiles

    def _prepare_locations(self, locations: pd.DataFrame, metadata: pd.DataFrame) -> pd.DataFrame:
        required_location_cols = {"place_id", "name", "ai_tags_json", "map_visibility_score", "ai_confidence"}
        missing_location_cols = sorted(required_location_cols - set(locations.columns))
        if missing_location_cols:
            raise ValueError(f"locations.csv is missing required columns: {missing_location_cols}")

        # place_id is an identifier, not a number: force str so purely-numeric cid
        # catalogs merge cleanly with string-keyed metadata (visual/direct stores).
        locations = locations.copy()
        locations["place_id"] = locations["place_id"].astype(str)
        metadata = metadata.copy()
        metadata["place_id"] = metadata["place_id"].astype(str)

        required_meta_cols = {"place_id", "embedding_row", "has_embedding"}
        missing_meta_cols = sorted(required_meta_cols - set(metadata.columns))
        if missing_meta_cols:
            raise ValueError(f"embedding metadata is missing required columns: {missing_meta_cols}")

        if len(self.embedding_matrix) != len(metadata):
            raise ValueError(
                f"Embedding matrix rows ({len(self.embedding_matrix)}) do not match metadata rows ({len(metadata)})"
            )
        if locations["place_id"].duplicated().any():
            duplicates = locations.loc[locations["place_id"].duplicated(), "place_id"].head(10).tolist()
            raise ValueError(f"locations.csv has duplicate place_id values: {duplicates}")
        if metadata["place_id"].duplicated().any():
            duplicates = metadata.loc[metadata["place_id"].duplicated(), "place_id"].head(10).tolist()
            raise ValueError(f"embedding metadata has duplicate place_id values: {duplicates}")

        missing_from_locations = sorted(set(metadata["place_id"]) - set(locations["place_id"]))
        if missing_from_locations:
            raise ValueError(f"Metadata place_ids missing from locations.csv: {missing_from_locations[:10]}")

        meta_cols = ["place_id", "embedding_row", "has_embedding", "custom_id", "embedding_text_hash"]
        available_meta_cols = [col for col in meta_cols if col in metadata.columns]
        prepared = locations.merge(metadata[available_meta_cols], on="place_id", how="left")
        prepared["has_embedding"] = prepared["has_embedding"].fillna(False).astype(bool)
        prepared["embedding_row"] = prepared["embedding_row"].fillna(-1).astype(int)

        bad_embedding_rows = prepared.loc[
            prepared["has_embedding"] & ~prepared["embedding_row"].between(0, len(self.embedding_matrix) - 1),
            ["place_id", "embedding_row"],
        ]
        if not bad_embedding_rows.empty:
            raise ValueError(f"Invalid embedding_row values: {bad_embedding_rows.head(10).to_dict('records')}")

        prepared = self._merge_visual_metadata(prepared)
        prepared = self._merge_direct_image_metadata(prepared)
        if "google_maps_uri" not in prepared.columns:
            prepared["google_maps_uri"] = prepared.apply(self._build_google_maps_uri, axis=1)
        else:
            prepared["google_maps_uri"] = prepared.apply(
                lambda row: row["google_maps_uri"] if utils.clean_text(row.get("google_maps_uri")) else self._build_google_maps_uri(row),
                axis=1,
            )

        prepared["recommendation_tags"] = prepared.apply(utils.parse_tag_names, axis=1)
        # Venue subtype + chain flag (wrong_category / too_chain_like fixes):
        # derived in-place from fields every catalog has, so test fixtures and
        # un-enriched catalogs work identically. Pre-existing columns win.
        if "venue_subtype" not in prepared.columns:
            prepared["venue_subtype"] = prepared.apply(utils.derive_venue_subtype, axis=1)
        prepared["venue_subtype"] = prepared["venue_subtype"].fillna("other")
        if "is_chain" not in prepared.columns:
            prepared["is_chain"] = utils.compute_is_chain(prepared)
        prepared["is_chain"] = prepared["is_chain"].fillna(False).astype(bool)
        if self.config.quality_score_version == "v2":
            prepared["quality_score"] = utils.compute_quality_score_v2(prepared, self.config.quality_shrinkage_prior)
        else:
            prepared["quality_score"] = utils.compute_quality_score(prepared)

        # Track which rows actually have axis data BEFORE filling (Task 2.4): a row
        # with no axis values must not look "average" — it is flagged not-available.
        axis_cols = [col for col in utils.AXIS_DEFINITIONS if col in prepared.columns]
        if axis_cols:
            raw_axes = prepared[axis_cols].apply(pd.to_numeric, errors="coerce")
            prepared["has_axes"] = raw_axes.notna().any(axis=1)
            for column in axis_cols:
                prepared[column] = raw_axes[column].fillna(50.0)
        else:
            prepared["has_axes"] = False

        return prepared.reset_index(drop=True)

    def _merge_visual_metadata(self, prepared: pd.DataFrame) -> pd.DataFrame:
        prepared = prepared.copy()
        prepared["has_visual_embedding"] = False
        prepared["visual_embedding_row"] = -1
        prepared["visual_embedding_custom_id"] = ""
        prepared["visual_embedding_text_hash"] = ""
        prepared["photo_profiles_total"] = 0
        prepared["photo_profiles_used"] = 0
        prepared["photo_profiles_skipped_for_embedding"] = 0
        prepared["visual_profile_selection_strategy"] = ""

        if self.visual_embedding_matrix is None or self.visual_embedding_metadata.empty:
            return prepared

        required_visual_cols = {"place_id", "visual_embedding_row", "has_visual_embedding"}
        missing_visual_cols = sorted(required_visual_cols - set(self.visual_embedding_metadata.columns))
        if missing_visual_cols:
            raise ValueError(f"Visual embedding metadata is missing required columns: {missing_visual_cols}")
        if len(self.visual_embedding_matrix) != len(self.visual_embedding_metadata):
            raise ValueError(
                "Visual embedding matrix rows "
                f"({len(self.visual_embedding_matrix)}) do not match visual metadata rows "
                f"({len(self.visual_embedding_metadata)})"
            )
        if self.visual_embedding_metadata["place_id"].duplicated().any():
            duplicates = self.visual_embedding_metadata.loc[
                self.visual_embedding_metadata["place_id"].duplicated(), "place_id"
            ].head(10).tolist()
            raise ValueError(f"Visual embedding metadata has duplicate place_id values: {duplicates}")

        visual_cols = [
            "place_id",
            "visual_embedding_row",
            "has_visual_embedding",
            "visual_embedding_custom_id",
            "visual_embedding_text_hash",
        ]
        visual_meta = self.visual_embedding_metadata[[col for col in visual_cols if col in self.visual_embedding_metadata.columns]].copy()

        if not self.visual_profiles.empty:
            profile_cols = [
                "place_id",
                "photo_profiles_total",
                "photo_profiles_used",
                "photo_profiles_skipped_for_embedding",
                "visual_profile_selection_strategy",
            ]
            profile_meta = self.visual_profiles[[col for col in profile_cols if col in self.visual_profiles.columns]].drop_duplicates(
                "place_id"
            )
            visual_meta = visual_meta.merge(profile_meta, on="place_id", how="left")

        prepared = prepared.drop(
            columns=[col for col in visual_meta.columns if col != "place_id" and col in prepared.columns],
            errors="ignore",
        ).merge(visual_meta, on="place_id", how="left")

        prepared["has_visual_embedding"] = prepared["has_visual_embedding"].fillna(False).astype(bool)
        prepared["visual_embedding_row"] = prepared["visual_embedding_row"].fillna(-1).astype(int)
        prepared["visual_embedding_custom_id"] = prepared["visual_embedding_custom_id"].fillna("")
        prepared["visual_embedding_text_hash"] = prepared["visual_embedding_text_hash"].fillna("")
        for col in ["photo_profiles_total", "photo_profiles_used", "photo_profiles_skipped_for_embedding"]:
            prepared[col] = pd.to_numeric(prepared[col], errors="coerce").fillna(0).astype(int)
        prepared["visual_profile_selection_strategy"] = prepared["visual_profile_selection_strategy"].fillna("")

        bad_visual_rows = prepared.loc[
            prepared["has_visual_embedding"]
            & ~prepared["visual_embedding_row"].between(0, len(self.visual_embedding_matrix) - 1),
            ["place_id", "visual_embedding_row"],
        ]
        if not bad_visual_rows.empty:
            raise ValueError(f"Invalid visual_embedding_row values: {bad_visual_rows.head(10).to_dict('records')}")

        return prepared

    def _merge_direct_image_metadata(self, prepared: pd.DataFrame) -> pd.DataFrame:
        prepared = prepared.copy()
        prepared["has_direct_image_embedding"] = False
        prepared["direct_place_embedding_row"] = -1
        prepared["direct_image_selection_strategy"] = ""
        prepared["direct_photo_profiles_total"] = 0
        prepared["direct_photo_embeddings_total"] = 0
        prepared["direct_photo_profiles_use_count"] = 0
        prepared["direct_photo_profiles_maybe_count"] = 0
        prepared["direct_photo_profiles_skip_count"] = 0
        prepared["direct_photo_embeddings_used_for_place"] = 0
        prepared["direct_photo_weight_sum"] = 0.0
        prepared["direct_image_model"] = ""
        prepared["direct_image_pretrained"] = ""

        if self.direct_image_embedding_matrix is None or self.direct_image_embedding_metadata.empty:
            return prepared

        required_cols = {"place_id", "direct_place_embedding_row", "has_direct_image_embedding"}
        missing_cols = sorted(required_cols - set(self.direct_image_embedding_metadata.columns))
        if missing_cols:
            raise ValueError(f"Direct image metadata is missing required columns: {missing_cols}")
        if self.direct_image_embedding_metadata["place_id"].duplicated().any():
            duplicates = self.direct_image_embedding_metadata.loc[
                self.direct_image_embedding_metadata["place_id"].duplicated(), "place_id"
            ].head(10).tolist()
            raise ValueError(f"Direct image metadata has duplicate place_id values: {duplicates}")

        direct_cols = [
            "place_id",
            "direct_place_embedding_row",
            "has_direct_image_embedding",
            "direct_image_selection_strategy",
            "direct_photo_profiles_total",
            "direct_photo_embeddings_total",
            "direct_photo_profiles_use_count",
            "direct_photo_profiles_maybe_count",
            "direct_photo_profiles_skip_count",
            "direct_photo_embeddings_used_for_place",
            "direct_photo_weight_sum",
            "direct_image_model",
            "direct_image_pretrained",
        ]
        direct_meta = self.direct_image_embedding_metadata[
            [col for col in direct_cols if col in self.direct_image_embedding_metadata.columns]
        ].copy()

        if not self.direct_image_profiles.empty:
            profile_cols = [
                "place_id",
                "direct_image_selection_strategy",
                "direct_photo_profiles_total",
                "direct_photo_embeddings_total",
                "direct_photo_profiles_use_count",
                "direct_photo_profiles_maybe_count",
                "direct_photo_profiles_skip_count",
                "direct_photo_embeddings_used_for_place",
                "direct_photo_weight_sum",
            ]
            profile_meta = self.direct_image_profiles[
                [col for col in profile_cols if col in self.direct_image_profiles.columns]
            ].drop_duplicates("place_id")
            direct_meta = direct_meta.drop(
                columns=[col for col in profile_meta.columns if col != "place_id" and col in direct_meta.columns],
                errors="ignore",
            ).merge(profile_meta, on="place_id", how="left")

        prepared = prepared.drop(
            columns=[col for col in direct_meta.columns if col != "place_id" and col in prepared.columns],
            errors="ignore",
        ).merge(direct_meta, on="place_id", how="left")

        prepared["has_direct_image_embedding"] = prepared["has_direct_image_embedding"].fillna(False).astype(bool)
        prepared["direct_place_embedding_row"] = pd.to_numeric(
            prepared["direct_place_embedding_row"], errors="coerce"
        ).fillna(-1).astype(int)
        prepared["direct_image_selection_strategy"] = prepared["direct_image_selection_strategy"].fillna("")
        for col in [
            "direct_photo_profiles_total",
            "direct_photo_embeddings_total",
            "direct_photo_profiles_use_count",
            "direct_photo_profiles_maybe_count",
            "direct_photo_profiles_skip_count",
            "direct_photo_embeddings_used_for_place",
        ]:
            prepared[col] = pd.to_numeric(prepared[col], errors="coerce").fillna(0).astype(int)
        prepared["direct_photo_weight_sum"] = pd.to_numeric(
            prepared["direct_photo_weight_sum"], errors="coerce"
        ).fillna(0.0)
        prepared["direct_image_model"] = prepared["direct_image_model"].fillna("")
        prepared["direct_image_pretrained"] = prepared["direct_image_pretrained"].fillna("")

        bad_direct_rows = prepared.loc[
            prepared["has_direct_image_embedding"]
            & ~prepared["direct_place_embedding_row"].between(0, len(self.direct_image_embedding_matrix) - 1),
            ["place_id", "direct_place_embedding_row"],
        ]
        if not bad_direct_rows.empty:
            raise ValueError(f"Invalid direct_place_embedding_row values: {bad_direct_rows.head(10).to_dict('records')}")

        return prepared

    @staticmethod
    def _build_google_maps_uri(row: pd.Series) -> str:
        place_id = utils.clean_text(row.get("place_id"))
        name = utils.clean_text(row.get("name"))
        lat = _as_float(row.get("latitude"), default=float("nan"))
        lon = _as_float(row.get("longitude"), default=float("nan"))
        # A numeric place_id IS the Google Maps CID -> link straight to the venue listing
        # (query_place_id expects a ChIJ Place ID, so a cid there silently falls back to coords).
        if place_id and place_id.isdigit():
            return f"https://www.google.com/maps?cid={place_id}"
        if not math.isnan(lat) and not math.isnan(lon):
            query = f"{lat},{lon}"
        else:
            query = quote_plus(name) if name else quote_plus(place_id)
        if place_id:
            return f"https://www.google.com/maps/search/?api=1&query={query}&query_place_id={quote_plus(place_id)}"
        return f"https://www.google.com/maps/search/?api=1&query={query}"

    def recommend(
        self,
        favourites_place_ids: list[str] | None,
        want_to_go_place_ids: list[str] | None,
        limit: int = 100,
        exclude_input_places: bool = True,
        debug: bool = False,
        user_id: str | None = None,
        user_lat: float | None = None,
        user_lon: float | None = None,
        **params,
    ) -> dict[str, Any]:
        cfg = self.config.with_overrides(params)
        limit = max(int(limit or 0), 0)

        favourites = _dedupe_preserve_order(favourites_place_ids)
        want_to_go = _dedupe_preserve_order(want_to_go_place_ids)
        input_place_ids = _dedupe_preserve_order(favourites + want_to_go)

        seed_df, invalid_place_ids = self._build_seed_dataframe(favourites, want_to_go, cfg)
        candidate_df = self._candidate_pool(cfg)
        if exclude_input_places and input_place_ids:
            candidate_df = candidate_df[~candidate_df["place_id"].isin(input_place_ids)].copy()
        candidate_df = self._attach_geo_score(candidate_df, cfg, user_lat, user_lon)

        profiles_silhouette = None
        if limit == 0:
            recommendations = []
            profiles = []
            fallback_used = len(seed_df) < cfg.min_saved_for_personalization
        elif len(seed_df) < cfg.min_saved_for_personalization:
            fallback_used = True
            profiles, recommendations = self._fallback_recommend(seed_df, candidate_df, limit, cfg, debug)
        else:
            fallback_used = False
            seed_df, profiles_silhouette = self._cluster_seed_places(seed_df, cfg)
            profiles = self._profile_payloads(seed_df, debug=debug)
            recommendations = self._personalized_recommend(
                seed_df, candidate_df, limit, cfg, debug, profiles_silhouette=profiles_silhouette
            )

        return {
            "user_id": user_id,
            "algorithm_version": cfg.algorithm_version,
            "embedding_run_id": cfg.embedding_run_id,
            "fallback_used": fallback_used,
            "input_summary": {
                "favourites_count": len(favourites),
                "want_to_go_count": len(want_to_go),
                "valid_input_count": int(len(seed_df)),
                "invalid_place_ids": invalid_place_ids,
                "profiles_count": len(profiles),
                "profiles_silhouette": _round_float(profiles_silhouette) if profiles_silhouette is not None else None,
                "candidate_count": int(len(candidate_df)),
            },
            "profiles": profiles,
            "recommendations": recommendations,
        }

    # Feature columns emitted by score_feature_frame (Task 3.1 training builder).
    # geo_score is included only when geo scoring is active; profiles_silhouette is
    # None for single-profile users (silhouette needs >= 2 clusters).
    FEATURE_COLUMNS = (
        "text_similarity",            # raw text cosine to the chosen profile centroid
        "semantic_similarity_norm",   # calibrated text similarity (the blended component)
        "visual_similarity",
        "visual_similarity_norm",
        "visual_available",
        "direct_image_similarity",
        "direct_image_similarity_norm",
        "direct_image_available",
        "tag_overlap",
        "axis_similarity",
        "axes_available",
        "quality_score",
        "price_match",
        "geo_score",
        "subtype_match",              # venue-subtype affinity to the profile (wrong_category fix)
        "subtype_available",
        "category_affinity",          # candidate primary_type match to the favourite-category mix
        "is_chain",                   # catalog-derived chain flag (too_chain_like)
        "profiles_silhouette",
        "profile_size",
        "profile_weight",
        "profiles_count",
    )

    def score_feature_frame(
        self,
        favourites_place_ids: list[str] | None,
        want_to_go_place_ids: list[str] | None = None,
        *,
        candidate_place_ids: list[str] | None = None,
        user_lat: float | None = None,
        user_lon: float | None = None,
        **params,
    ) -> pd.DataFrame:
        """One row per candidate with every scored component, for ranker training.

        Routes through the same per-profile scoring path as ``recommend`` but skips
        quota/MMR/truncation: every candidate is scored against each taste profile
        and kept once, from the profile that scores it highest (its natural owner).
        Returns the feature columns in ``FEATURE_COLUMNS`` plus ``place_id``,
        ``profile_id`` and ``score``. ``candidate_place_ids`` restricts the pool
        (e.g. to held-out positives + sampled negatives); ``None`` scores the full
        catalogue. The seed clustering uses the live config, so silhouette / profile
        sizes match what ``recommend`` would produce for the same favourites.
        """
        cfg = self.config.with_overrides(params)
        favourites = _dedupe_preserve_order(favourites_place_ids)
        want_to_go = _dedupe_preserve_order(want_to_go_place_ids)
        input_place_ids = _dedupe_preserve_order(favourites + want_to_go)

        seed_df, _ = self._build_seed_dataframe(favourites, want_to_go, cfg)
        candidate_df = self._candidate_pool(cfg)
        candidate_df = candidate_df[~candidate_df["place_id"].isin(input_place_ids)].copy()
        if candidate_place_ids is not None:
            wanted = set(map(str, candidate_place_ids))
            candidate_df = candidate_df[candidate_df["place_id"].isin(wanted)].copy()
        candidate_df = self._attach_geo_score(candidate_df, cfg, user_lat, user_lon)

        empty = pd.DataFrame(columns=["place_id", "profile_id", "score", *self.FEATURE_COLUMNS])
        if candidate_df.empty or len(seed_df) < cfg.min_saved_for_personalization:
            return empty

        seed_df, profiles_silhouette = self._cluster_seed_places(seed_df, cfg)
        groups = list(seed_df.groupby("profile_id", sort=True))
        profiles_count = len(groups)

        scored_parts = []
        for profile_id, group in groups:
            scored = self._score_candidates_for_profile(candidate_df, group, int(profile_id), cfg)
            scored["profile_size"] = int(len(group))
            scored["profile_weight"] = float(group["signal_weight"].sum())
            scored_parts.append(scored)
        if not scored_parts:
            return empty

        merged = pd.concat(scored_parts, ignore_index=True)
        # keep each candidate once, from the profile that scores it highest
        merged = merged.sort_values(["score", "place_id"], ascending=[False, True])
        merged = merged.drop_duplicates("place_id", keep="first").reset_index(drop=True)
        merged["profiles_count"] = int(profiles_count)
        merged["profiles_silhouette"] = profiles_silhouette  # None -> NaN column

        if "geo_score" not in merged.columns:
            merged["geo_score"] = 0.0
        for flag in ("visual_available", "direct_image_available", "axes_available", "subtype_available", "is_chain"):
            merged[flag] = merged[flag].astype(bool)

        return merged[["place_id", "profile_id", "score", *self.FEATURE_COLUMNS]].copy()

    def _build_seed_dataframe(
        self,
        favourites: list[str],
        want_to_go: list[str],
        cfg: RecommenderConfig,
    ) -> tuple[pd.DataFrame, list[str]]:
        weights_by_place: dict[str, float] = {}
        list_type_by_place: dict[str, str] = {}

        for place_id in want_to_go:
            weights_by_place[place_id] = max(weights_by_place.get(place_id, 0.0), cfg.want_to_go_weight)
            list_type_by_place.setdefault(place_id, "want_to_go")
        for place_id in favourites:
            weights_by_place[place_id] = max(weights_by_place.get(place_id, 0.0), cfg.favorites_weight)
            list_type_by_place[place_id] = "favourite"

        valid_rows = []
        invalid_place_ids = []
        for place_id in _dedupe_preserve_order(list(weights_by_place)):
            idx = self.place_id_to_index.get(place_id)
            if idx is None:
                invalid_place_ids.append(place_id)
                continue
            row = self.locations.iloc[idx]
            if not bool(row["has_embedding"]):
                invalid_place_ids.append(place_id)
                continue
            valid_rows.append(row.to_dict() | {"signal_weight": weights_by_place[place_id], "source_list": list_type_by_place[place_id]})

        return pd.DataFrame(valid_rows), invalid_place_ids

    def _candidate_pool(self, cfg: RecommenderConfig) -> pd.DataFrame:
        candidates = self.locations[self.locations["has_embedding"]].copy()
        if cfg.exclude_low_confidence:
            candidates = candidates[candidates["ai_confidence"].fillna("").str.lower() != "low"].copy()
        if cfg.apply_map_visibility_filter:
            candidates = candidates[candidates["map_visibility_score"].fillna(0) >= cfg.min_map_visibility_score].copy()
        if cfg.city_filter and "city" in candidates.columns:
            candidates = candidates[candidates["city"].astype(str) == cfg.city_filter].copy()
        if cfg.theme_group_filter and "theme_group" in candidates.columns:
            candidates = candidates[candidates["theme_group"].astype(str) == cfg.theme_group_filter].copy()
        return candidates

    def _attach_geo_score(
        self, candidates: pd.DataFrame, cfg: RecommenderConfig, user_lat: float | None, user_lon: float | None
    ) -> pd.DataFrame:
        """Add a ``geo_score`` column (exp(-distance/geo_decay_km)) when the geo
        feature is active. Inactive -> returns candidates untouched (so payloads
        stay byte-identical). Candidates without coordinates get geo_score 0."""
        geo_weight = _as_float(cfg.weights.get("geo_distance"), 0.0)
        if user_lat is None or user_lon is None or geo_weight <= 0 or candidates.empty:
            return candidates
        if "latitude" not in candidates.columns or "longitude" not in candidates.columns:
            return candidates
        candidates = candidates.copy()
        lat = pd.to_numeric(candidates["latitude"], errors="coerce").to_numpy()
        lon = pd.to_numeric(candidates["longitude"], errors="coerce").to_numpy()
        distance_km = _haversine_km(float(user_lat), float(user_lon), lat, lon)
        geo = np.exp(-distance_km / max(cfg.geo_decay_km, 1e-9))
        candidates["geo_score"] = np.where(np.isnan(geo), 0.0, geo)
        return candidates

    def _cluster_seed_places(self, seed_df: pd.DataFrame, cfg: RecommenderConfig) -> tuple[pd.DataFrame, float | None]:
        """Cluster favourites into taste profiles. Returns (clustered_df, silhouette)
        where silhouette is the best multi-cluster score considered (None if a single
        profile was used without evaluating any split)."""
        seed_vectors = self.normalized_embeddings[seed_df["embedding_row"].astype(int).to_numpy()]
        n_clusters, silhouette = self._choose_profile_cluster_count(seed_vectors, cfg)
        if n_clusters == 1:
            labels = np.zeros(len(seed_df), dtype=int)
        else:
            labels = _agglomerative_cosine(n_clusters).fit_predict(seed_vectors)
        clustered = seed_df.copy()
        clustered["profile_id"] = labels.astype(int)
        return clustered, silhouette

    def _choose_profile_cluster_count(self, seed_vectors: np.ndarray, cfg: RecommenderConfig) -> tuple[int, float | None]:
        from sklearn.metrics import silhouette_score

        n = len(seed_vectors)
        if n < 4:
            return 1, None

        # Cap K by: ~3 seeds/cluster, the config max, AND a MIND-style log2(n) ceiling so larger seed
        # sets can't over-split into many thin profiles (cold-start guard, deep-research 2026-06-27,
        # docs/reports/2026-06-27-multi-interest-profile-representation-research.md).
        log2_cap = max(1, int(math.floor(math.log2(n))))
        max_k = min(cfg.max_profile_clusters, n // 3, n - 1, log2_cap)
        if max_k < 2:
            return 1, None
        min_size = max(1, int(cfg.min_profile_cluster_size))
        scores = []
        for k in range(2, max_k + 1):
            labels = _agglomerative_cosine(k).fit_predict(seed_vectors)
            if len(set(labels)) < 2:
                continue
            # Reject splits that isolate a too-small (e.g. singleton outlier) cluster: silhouette often
            # rewards carving one outlier off, which over-splits an otherwise coherent profile.
            if int(np.bincount(labels).min()) < min_size:
                continue
            score = silhouette_score(seed_vectors, labels, metric="cosine")
            scores.append((k, score))

        if not scores:
            return 1, None
        best_k, best_score = max(scores, key=lambda item: item[1])
        # Report the best silhouette found regardless of the accept/reject decision.
        return (best_k if best_score >= cfg.min_profile_silhouette else 1), float(best_score)

    def _profile_payloads(self, seed_df: pd.DataFrame, debug: bool = False) -> list[dict[str, Any]]:
        if seed_df.empty or "profile_id" not in seed_df.columns:
            return []

        profiles = []
        for profile_id, group in seed_df.groupby("profile_id", sort=True):
            tags = self._top_profile_tags(group)
            payload = {
                "profile_id": int(profile_id),
                "profile_weight": _round_float(group["signal_weight"].sum()),
                "seed_place_ids": group["place_id"].tolist(),
                "visual_seed_count": int(group.get("has_visual_embedding", pd.Series(dtype=bool)).sum()),
                "direct_image_seed_count": int(group.get("has_direct_image_embedding", pd.Series(dtype=bool)).sum()),
                "top_tags": tags,
            }
            if debug:
                payload["seed_names"] = group["name"].tolist()
                payload["source_lists"] = group[["place_id", "source_list", "signal_weight"]].to_dict("records")
            profiles.append(payload)
        return profiles

    def _top_profile_tags(self, group: pd.DataFrame, limit: int = 10) -> list[str]:
        counter = Counter()
        for _, row in group.iterrows():
            weight = _as_float(row.get("signal_weight"), 1.0)
            for tag in row.get("recommendation_tags") or []:
                counter[tag] += weight
        return [tag for tag, _ in counter.most_common(limit)]

    @staticmethod
    def _robust_weights(vectors: np.ndarray, weights: np.ndarray, robust: bool) -> np.ndarray:
        """Down-weight outlier members (cosine to the plain centroid below the 25th
        percentile) by 0.25 before re-averaging. No-op for <=3 members or robust off."""
        if not robust or len(weights) <= 3:
            return weights
        plain = np.average(vectors, axis=0, weights=weights)
        norm = np.linalg.norm(plain)
        if norm == 0:
            return weights
        cosines = vectors @ (plain / norm)   # vectors are L2-normalized
        threshold = np.percentile(cosines, 25)
        adjusted = np.asarray(weights, dtype=float).copy()
        adjusted[cosines < threshold] *= 0.25
        return adjusted

    def _weighted_centroid(self, group: pd.DataFrame, robust: bool = False) -> np.ndarray:
        rows = group["embedding_row"].astype(int).to_numpy()
        weights = group["signal_weight"].astype(float).to_numpy()
        vectors = self.normalized_embeddings[rows]
        weights = self._robust_weights(vectors, weights, robust)
        centroid = np.average(vectors, axis=0, weights=weights)
        norm = np.linalg.norm(centroid)
        if norm == 0:
            return centroid
        return centroid / norm

    def _weighted_visual_centroid(self, group: pd.DataFrame, robust: bool = False) -> np.ndarray | None:
        if self.normalized_visual_embeddings is None or "has_visual_embedding" not in group.columns:
            return None
        visual_group = group[group["has_visual_embedding"]].copy()
        if visual_group.empty:
            return None
        rows = visual_group["visual_embedding_row"].astype(int).to_numpy()
        weights = visual_group["signal_weight"].astype(float).to_numpy()
        vectors = self.normalized_visual_embeddings[rows]
        weights = self._robust_weights(vectors, weights, robust)
        centroid = np.average(vectors, axis=0, weights=weights)
        norm = np.linalg.norm(centroid)
        if norm == 0:
            return centroid
        return centroid / norm

    def _weighted_direct_image_centroid(self, group: pd.DataFrame, robust: bool = False) -> np.ndarray | None:
        if self.normalized_direct_image_embeddings is None or "has_direct_image_embedding" not in group.columns:
            return None
        direct_group = group[group["has_direct_image_embedding"]].copy()
        if direct_group.empty:
            return None
        rows = direct_group["direct_place_embedding_row"].astype(int).to_numpy()
        weights = direct_group["signal_weight"].astype(float).to_numpy()
        vectors = self.normalized_direct_image_embeddings[rows]
        weights = self._robust_weights(vectors, weights, robust)
        centroid = np.average(vectors, axis=0, weights=weights)
        norm = np.linalg.norm(centroid)
        if norm == 0:
            return centroid
        return centroid / norm

    def _weighted_axis_centroid(self, group: pd.DataFrame) -> pd.Series:
        axis_cols = [col for col in utils.AXIS_DEFINITIONS if col in group.columns]
        weights = group["signal_weight"].astype(float).to_numpy()
        values = group[axis_cols].astype(float).to_numpy()
        return pd.Series(np.average(values, axis=0, weights=weights), index=axis_cols)

    def _profile_tag_set(self, group: pd.DataFrame) -> set[str]:
        tags = set()
        for row_tags in group["recommendation_tags"]:
            tags.update(row_tags or [])
        return tags

    @staticmethod
    def _aggregate_seed_similarity(
        candidate_vectors: np.ndarray,
        seed_vectors: np.ndarray,
        centroid: np.ndarray,
        mode: str,
    ) -> np.ndarray:
        """Per-candidate similarity to a profile under the chosen seed aggregation.

        "centroid" compares to the (robust/weighted) centroid; "max_seed" takes the
        best single seed (a candidate close to ANY seed scores high — no centroid
        smearing); "top2_mean" averages the two best seeds (softer than max).
        Seed signal weights are intentionally ignored for max/top2: percentile
        calibration downstream makes the absolute scale irrelevant.
        """
        n_seeds = 0 if seed_vectors is None else len(seed_vectors)
        # Fall back to the centroid for any mode with a single seed, and for top2_mean with < 3 seeds
        # (with 1-2 seeds there is no real "top-2 of many" to select, so it degenerates).
        if mode == "centroid" or n_seeds < 2 or (mode == "top2_mean" and n_seeds < 3):
            return candidate_vectors @ centroid
        sims = candidate_vectors @ seed_vectors.T
        if mode == "max_seed":
            return sims.max(axis=1)
        top2 = np.partition(sims, -2, axis=1)[:, -2:]
        return top2.mean(axis=1)

    @staticmethod
    def _mean_pairwise_similarity(vectors: np.ndarray) -> float:
        if vectors is None or len(vectors) < 2:
            return 0.0
        sims = vectors @ vectors.T
        iu = np.triu_indices(len(vectors), k=1)
        return float(sims[iu].mean())

    def _channel_coherence_baseline(self, channel: str) -> float:
        """Catalog-average pairwise similarity of an embedding space (cached).

        Uses a fixed deterministic sample (first 256 embedded rows) so the baseline
        is stable across calls. Needed because absolute cosine levels differ wildly
        between spaces (CLIP image-image runs much higher than text-text), so raw
        profile coherences are only comparable as lift over their space's baseline.
        """
        cache = getattr(self, "_coherence_baselines", None)
        if cache is None:
            cache = {}
            self._coherence_baselines = cache
        if channel not in cache:
            matrix = self.normalized_embeddings if channel == "text" else self.normalized_direct_image_embeddings
            sample = matrix[:256] if matrix is not None else None
            cache[channel] = max(self._mean_pairwise_similarity(sample), 1e-6)
        return cache[channel]

    def _direct_weight_multiplier(self, group: pd.DataFrame, cfg: RecommenderConfig) -> float:
        """Adaptive direct-image weight: visual-coherence lift over text-coherence lift.

        If the profile's seeds cluster tighter in CLIP space than in text space
        (relative to each space's catalog baseline), the photos carry more of this
        taste's identity and the direct weight scales up — and vice versa. Clipped
        to [adaptive_min_scale, adaptive_max_scale]; 1.0 when < 2 direct seeds.
        """
        direct_group = group[group.get("has_direct_image_embedding", pd.Series(dtype=bool)).fillna(False)]
        if len(direct_group) < 2 or self.normalized_direct_image_embeddings is None:
            return 1.0
        direct_vectors = self.normalized_direct_image_embeddings[
            direct_group["direct_place_embedding_row"].astype(int).to_numpy()
        ]
        text_vectors = self.normalized_embeddings[direct_group["embedding_row"].astype(int).to_numpy()]
        direct_lift = self._mean_pairwise_similarity(direct_vectors) / self._channel_coherence_baseline("direct")
        text_lift = self._mean_pairwise_similarity(text_vectors) / self._channel_coherence_baseline("text")
        if text_lift <= 0:
            return 1.0
        return float(np.clip(direct_lift / text_lift, cfg.adaptive_min_scale, cfg.adaptive_max_scale))

    def _score_candidates_for_profile(
        self,
        candidates: pd.DataFrame,
        group: pd.DataFrame,
        profile_id: int,
        cfg: RecommenderConfig,
    ) -> pd.DataFrame:
        centroid = self._weighted_centroid(group, robust=cfg.robust_centroids)
        candidate_rows = candidates["embedding_row"].astype(int).to_numpy()
        candidate_vectors = self.normalized_embeddings[candidate_rows]
        seed_text_vectors = self.normalized_embeddings[group["embedding_row"].astype(int).to_numpy()]
        text_similarity = self._aggregate_seed_similarity(
            candidate_vectors, seed_text_vectors, centroid, cfg.seed_aggregation
        )
        # CSLS hubness correction (Task 2.1): penalize candidates that are similar
        # to everything (hubs) before calibration. Raw cosine is kept in `similarity`.
        if cfg.hubness_method == "csls" and len(candidate_rows) > 1:
            density = itr.hubness_density(self.normalized_embeddings, candidate_rows, cfg.hubness_k)
            adjusted_similarity = text_similarity - cfg.hubness_penalty * density
        else:
            adjusted_similarity = text_similarity
        text_similarity_norm = _calibrate_similarity(adjusted_similarity, cfg.calibration)

        scored = candidates.copy()
        scored["profile_id"] = int(profile_id)
        scored["similarity"] = text_similarity
        scored["text_similarity"] = text_similarity
        scored["_text_adjusted"] = adjusted_similarity
        scored["semantic_similarity_norm"] = text_similarity_norm
        scored["text_similarity_norm"] = text_similarity_norm
        scored["visual_similarity"] = 0.0
        scored["visual_similarity_norm"] = 0.0
        scored["visual_available"] = False
        scored["visual_seed_count"] = int(group.get("has_visual_embedding", pd.Series(dtype=bool)).sum())
        scored["direct_image_similarity"] = 0.0
        scored["direct_image_similarity_norm"] = 0.0
        scored["direct_image_available"] = False
        scored["direct_image_seed_count"] = int(group.get("has_direct_image_embedding", pd.Series(dtype=bool)).sum())

        # _<modality>_in_blend marks whether the modality participates in this row's
        # weighting. Under "redistribute" a missing modality drops out (its weight is
        # spread over the present components); other policies keep it in the blend.
        scored["_visual_in_blend"] = True
        scored["_direct_in_blend"] = True

        visual_centroid = self._weighted_visual_centroid(group, robust=cfg.robust_centroids)
        visual_weight_requested = _as_float(cfg.weights.get("visual_similarity"), 0.0) > 0
        visual_enabled = visual_centroid is not None and visual_weight_requested and self.normalized_visual_embeddings is not None
        if visual_enabled:
            visual_available = scored["has_visual_embedding"].fillna(False).astype(bool).to_numpy()
            scored["visual_available"] = visual_available
            if cfg.missing_visual_policy == "light_penalty":
                visual_norm = np.full(len(scored), cfg.missing_visual_similarity_norm, dtype=np.float32)
            else:
                visual_norm = np.zeros(len(scored), dtype=np.float32)
            visual_raw = np.zeros(len(scored), dtype=np.float32)
            if visual_available.any():
                visual_rows = scored.loc[visual_available, "visual_embedding_row"].astype(int).to_numpy()
                visual_vectors = self.normalized_visual_embeddings[visual_rows]
                visual_seed_group = group[group["has_visual_embedding"].fillna(False)]
                visual_seed_vectors = self.normalized_visual_embeddings[
                    visual_seed_group["visual_embedding_row"].astype(int).to_numpy()
                ]
                visual_raw_values = self._aggregate_seed_similarity(
                    visual_vectors, visual_seed_vectors, visual_centroid, cfg.seed_aggregation
                )
                visual_raw[visual_available] = visual_raw_values
                visual_norm[visual_available] = _calibrate_similarity(visual_raw_values, cfg.calibration)
            scored["visual_similarity"] = visual_raw
            scored["visual_similarity_norm"] = visual_norm
            if cfg.missing_visual_policy == "redistribute":
                scored["_visual_in_blend"] = visual_available
            if cfg.missing_visual_policy == "exclude":
                scored = scored[scored["visual_available"]].copy()

        direct_image_centroid = self._weighted_direct_image_centroid(group, robust=cfg.robust_centroids)
        direct_image_weight_requested = _as_float(cfg.weights.get("direct_image_similarity"), 0.0) > 0
        direct_image_enabled = (
            direct_image_centroid is not None
            and direct_image_weight_requested
            and self.normalized_direct_image_embeddings is not None
        )
        if direct_image_enabled:
            direct_available = scored["has_direct_image_embedding"].fillna(False).astype(bool).to_numpy()
            scored["direct_image_available"] = direct_available
            if cfg.missing_direct_image_policy == "light_penalty":
                direct_norm = np.full(len(scored), cfg.missing_direct_image_similarity_norm, dtype=np.float32)
            else:
                direct_norm = np.zeros(len(scored), dtype=np.float32)
            direct_raw = np.zeros(len(scored), dtype=np.float32)
            if direct_available.any():
                direct_rows = scored.loc[direct_available, "direct_place_embedding_row"].astype(int).to_numpy()
                direct_vectors = self.normalized_direct_image_embeddings[direct_rows]
                direct_seed_group = group[group["has_direct_image_embedding"].fillna(False)]
                direct_seed_vectors = self.normalized_direct_image_embeddings[
                    direct_seed_group["direct_place_embedding_row"].astype(int).to_numpy()
                ]
                direct_raw_values = self._aggregate_seed_similarity(
                    direct_vectors, direct_seed_vectors, direct_image_centroid, cfg.seed_aggregation
                )
                direct_raw[direct_available] = direct_raw_values
                direct_norm[direct_available] = _calibrate_similarity(direct_raw_values, cfg.calibration)
            scored["direct_image_similarity"] = direct_raw
            scored["direct_image_similarity_norm"] = direct_norm
            if cfg.missing_direct_image_policy == "redistribute":
                scored["_direct_in_blend"] = direct_available
            if cfg.missing_direct_image_policy == "exclude":
                scored = scored[scored["direct_image_available"]].copy()

        scored["tag_overlap"] = self._tag_overlap(scored, self._profile_tag_set(group))
        # Axes (Task 2.4): rows without axis data take the candidate-pool median
        # axis_similarity (computed, not a hardcoded 50) so they neither help nor hurt.
        axis_similarity = self._axis_similarity(scored, self._weighted_axis_centroid(group))
        if "has_axes" in scored.columns:
            has_axes = scored["has_axes"].fillna(True).astype(bool).to_numpy()
        else:
            has_axes = np.ones(len(scored), dtype=bool)
        if not has_axes.all():
            axis_values = axis_similarity.to_numpy(dtype=float).copy()
            pool_median = float(np.median(axis_values[has_axes])) if has_axes.any() else 0.5
            axis_values[~has_axes] = pool_median
            axis_similarity = pd.Series(axis_values, index=scored.index)
        scored["axis_similarity"] = axis_similarity
        scored["axes_available"] = has_axes
        scored["price_match"] = self._price_match(scored, group)
        subtype_scores, subtype_available = self._subtype_match(scored, group, cfg)
        scored["subtype_match"] = subtype_scores
        scored["subtype_available"] = subtype_available
        scored["category_affinity"] = self._category_affinity(scored, group)

        # --- experiment knobs (seed_aggregation handled above): adaptive direct
        # weight + raw-cosine fusion. Both leave the default path byte-identical.
        effective_weights = dict(cfg.weights)
        direct_weight_multiplier = 1.0
        if cfg.adaptive_direct_weight and direct_image_enabled:
            direct_weight_multiplier = self._direct_weight_multiplier(group, cfg)
            effective_weights["direct_image_similarity"] = (
                _as_float(effective_weights.get("direct_image_similarity"), 0.0) * direct_weight_multiplier
            )
        raw_blend_active = cfg.modality_fusion == "raw_blend" and direct_image_enabled
        if raw_blend_active:
            w_text = max(_as_float(effective_weights.get("semantic_similarity"), 0.0), 0.0)
            w_direct = max(_as_float(effective_weights.get("direct_image_similarity"), 0.0), 0.0)
            text_adjusted = scored["_text_adjusted"].to_numpy(dtype=float)
            direct_raw_col = scored["direct_image_similarity"].to_numpy(dtype=float)
            has_direct = scored["direct_image_available"].to_numpy(dtype=bool)
            fused_raw = text_adjusted.copy()
            if w_text + w_direct > 0 and has_direct.any():
                fused_raw[has_direct] = (
                    w_text * text_adjusted[has_direct] + w_direct * direct_raw_col[has_direct]
                ) / (w_text + w_direct)
            fused_norm = _calibrate_similarity(fused_raw, cfg.calibration)
            scored["semantic_similarity_norm"] = fused_norm
            scored["text_similarity_norm"] = fused_norm
            # The direct channel is folded into the fused similarity, so its weight
            # moves onto the semantic component; rows without photos use pure text.
            effective_weights["semantic_similarity"] = w_text + w_direct
            effective_weights["direct_image_similarity"] = 0.0
        scored["direct_weight_multiplier"] = direct_weight_multiplier

        score_weights = _normalized_score_weights(
            effective_weights,
            visual_enabled=visual_enabled,
            direct_image_enabled=direct_image_enabled and not raw_blend_active,
        )
        # Per-row weighted average over the components present in each row. When no
        # modality is missing this equals the plain weighted sum (denominator == 1),
        # so legacy/text-only behaviour is unchanged.
        component_specs = [
            ("semantic_similarity", "semantic_similarity_norm", None),
            ("visual_similarity", "visual_similarity_norm", "_visual_in_blend"),
            ("direct_image_similarity", "direct_image_similarity_norm", "_direct_in_blend"),
            ("tag_overlap", "tag_overlap", None),
            ("axis_similarity", "axis_similarity", None),
            ("quality_score", "quality_score", None),
            ("price_match", "price_match", None),
            # Opt-in like geo: absent from presets => weight 0 => no-op.
            ("subtype_match", "subtype_match", None),
            ("category_affinity", "category_affinity", None),
        ]
        if "geo_score" in scored.columns:
            component_specs.append(("geo_distance", "geo_score", None))
        n_rows = len(scored)
        numerator = np.zeros(n_rows, dtype=float)
        denominator = np.zeros(n_rows, dtype=float)
        for weight_key, value_col, mask_col in component_specs:
            weight = score_weights.get(weight_key, 0.0)
            if weight <= 0:
                continue
            values = scored[value_col].fillna(0).to_numpy(dtype=float)
            # Stage-0 cross-channel calibration: percentile-rank the raw components
            # (tag/axis/quality/price/subtype/geo) so every channel shares the
            # uniform [0,1] scale and weight == ranking influence. Similarity
            # channels are already percentile-calibrated, so they are left as-is.
            if cfg.calibrate_components and value_col not in _PRECALIBRATED_VALUE_COLS:
                values = _percentile_norm(values)
            mask = scored[mask_col].to_numpy(dtype=float) if mask_col else np.ones(n_rows, dtype=float)
            numerator += mask * weight * values
            denominator += mask * weight
        scored["score"] = np.where(denominator > 0, numerator / denominator, 0.0)
        # Layer 2 — Stage-2 context boost: multiply the blended relevance by the
        # situation multiplier (time of day / weather). No-op when no context is set.
        if cfg.context_time or cfg.context_weather:
            scored["score"] = scored["score"].to_numpy(dtype=float) * self._context_boost(
                scored, cfg.context_time, cfg.context_weather
            )
        scored["visual_weight_active"] = score_weights.get("visual_similarity", 0.0)
        scored["direct_image_weight_active"] = score_weights.get("direct_image_similarity", 0.0)
        scored = scored.drop(columns=["_visual_in_blend", "_direct_in_blend", "_text_adjusted"])
        return scored

    def _tag_overlap(self, candidates: pd.DataFrame, profile_tags: set[str]) -> pd.Series:
        if not profile_tags:
            return pd.Series(0.0, index=candidates.index)

        scores = []
        for tags in candidates["recommendation_tags"]:
            candidate_tags = set(tags or [])
            if not candidate_tags:
                scores.append(0.0)
            else:
                scores.append(len(candidate_tags & profile_tags) / len(candidate_tags | profile_tags))
        return pd.Series(scores, index=candidates.index)

    def _axis_similarity(self, candidates: pd.DataFrame, axis_centroid: pd.Series) -> pd.Series:
        axis_cols = list(axis_centroid.index)
        if not axis_cols:
            return pd.Series(0.5, index=candidates.index)
        distances = candidates[axis_cols].astype(float).sub(axis_centroid, axis=1).abs().mean(axis=1)
        return (1 - distances / 100).clip(0, 1)

    def _subtype_match(
        self, candidates: pd.DataFrame, group: pd.DataFrame, cfg: RecommenderConfig | None = None
    ) -> tuple[pd.Series, pd.Series]:
        """Venue-subtype affinity of each candidate to the profile's seed subtypes.

        Max affinity over the seeds' (known) subtypes: a cafe+bar profile gives a
        bar candidate 1.0. Candidates/profiles with unknown subtype get a neutral
        0.5 and `subtype_available=False` (no reward, no penalty — Task 2.4 style).

        With ``cfg.subtype_coherence_gate`` > 0 the component activates only for
        category-coherent profiles (dominant seed subtype share >= gate): the A/B
        showed a blanket category penalty hurts cross-category vibe matches the
        labels call good, while the wrong_category complaints all came from
        coherent (e.g. all-bistro) seed sets.
        """
        neutral = pd.Series(0.5, index=candidates.index), pd.Series(False, index=candidates.index)
        cand_subtypes = candidates.get("venue_subtype", pd.Series("other", index=candidates.index)).fillna("other")
        available = (cand_subtypes != "other")
        seed_list = [s for s in group.get("venue_subtype", pd.Series(dtype=object)).fillna("other") if s != "other"]
        if not seed_list:
            return neutral
        gate = float(getattr(cfg, "subtype_coherence_gate", 0.0) or 0.0) if cfg is not None else 0.0
        if gate > 0:
            dominant_share = Counter(seed_list).most_common(1)[0][1] / len(seed_list)
            if dominant_share < gate:
                return neutral
        seed_subtypes = sorted(set(seed_list))
        score_by_subtype = {
            cs: max(utils.subtype_affinity(cs, ss) for ss in seed_subtypes) for cs in cand_subtypes.unique()
        }
        scores = cand_subtypes.map(score_by_subtype).astype(float)
        scores[~available] = 0.5
        return scores, available

    def _price_match(self, candidates: pd.DataFrame, group: pd.DataFrame) -> pd.Series:
        if "axis_cheap_expensive" not in candidates.columns:
            return pd.Series(0.5, index=candidates.index)
        weights = group["signal_weight"].astype(float).to_numpy()
        centroid = np.average(group["axis_cheap_expensive"].astype(float).to_numpy(), weights=weights)
        distances = candidates["axis_cheap_expensive"].astype(float).sub(centroid).abs()
        return (1 - distances / 100).clip(0, 1)

    def _category_affinity(self, candidates: pd.DataFrame, group: pd.DataFrame) -> pd.Series:
        """Affinity of each candidate's ``primary_type`` to the profile's favourite-
        category mix — the hard categorical/dietary signal the vibe-based embedding
        channels miss (text/image encode aesthetic, not "is this place vegan").

        The favourites induce a category distribution p(type); a candidate of type t
        scores p(t), i.e. how much of the user's taste lives in that exact category,
        normalised so the most-favourited category maps to 1.0. Types absent from the
        favourites score 0, so burger/pizza candidates drop out of a vegan/health
        taste while every Vegan/Vegetarian/Health-food/Juice candidate is rewarded in
        proportion to how often the user actually saves that category. Generalises to
        any taste (a wine list lifts Wine bars, a coffee list lifts Cafes)."""
        if "primary_type" not in candidates.columns:
            return pd.Series(0.0, index=candidates.index)
        fav_types = group.get("primary_type", pd.Series(dtype=object))
        fav_types = fav_types.astype(str).str.strip()
        fav_types = fav_types[fav_types != ""]
        if fav_types.empty:
            return pd.Series(0.0, index=candidates.index)
        counts = fav_types.value_counts()
        top = float(counts.iloc[0])  # most-favourited category count -> normaliser
        dist = {t: float(c) / top for t, c in counts.items()}
        cand_types = candidates["primary_type"].astype(str).str.strip()
        return cand_types.map(lambda t: dist.get(t, 0.0)).astype(float)

    def _context_boost(self, candidates: pd.DataFrame, time_key: str, weather_key: str) -> np.ndarray:
        """Stage-2 situation multiplier per candidate (Layer 2).

        Product of every CONTEXT_*_BOOSTS multiplier whose keyword appears in the
        candidate's (primary_type + tags) text, clipped to CONTEXT_BOOST_CLIP. Lets the
        time of day / weather scale a place up or down (morning -> cafes up, bars down)
        without touching the Layer-1 blend weights. 1.0 everywhere when no context is set."""
        time_map = CONTEXT_TIME_BOOSTS.get(time_key or "", {})
        weather_map = CONTEXT_WEATHER_BOOSTS.get(weather_key or "", {})
        n = len(candidates)
        if (not time_map and not weather_map) or n == 0:
            return np.ones(n, dtype=float)
        pt = candidates.get("primary_type", pd.Series([""] * n, index=candidates.index)).astype(str).str.lower()
        if "recommendation_tags" in candidates.columns:
            tags = candidates["recommendation_tags"].apply(
                lambda t: " ".join(t).lower() if isinstance(t, (list, tuple)) else ""
            )
        else:
            tags = pd.Series([""] * n, index=candidates.index)
        blob = (pt + " " + tags).to_numpy()
        boost = np.ones(n, dtype=float)
        for kw_map in (time_map, weather_map):
            for kw, mult in kw_map.items():
                hit = np.fromiter((kw in b for b in blob), dtype=bool, count=n)
                if hit.any():
                    boost[hit] *= float(mult)
        return np.clip(boost, *CONTEXT_BOOST_CLIP)

    # --- item-to-item ("more like this") --------------------------------------

    def _seed_group(self, place_id: str) -> pd.DataFrame | None:
        idx = self.place_id_to_index.get(place_id)
        if idx is None:
            return None
        row = self.locations.iloc[idx]
        if not bool(row["has_embedding"]):
            return None
        return pd.DataFrame([row.to_dict() | {"signal_weight": 1.0, "source_list": "seed"}])

    def _polarity_tag_overlap(self, candidates: pd.DataFrame, seed_group: pd.DataFrame) -> pd.Series:
        seed_tags = utils.parse_ai_tags_json(seed_group.iloc[0].get("ai_tags_json"))
        scores = []
        for raw in candidates["ai_tags_json"]:
            cand_tags = utils.parse_ai_tags_json(raw)
            scores.append(itr.polarity_weighted_tag_overlap(seed_tags, cand_tags))
        return pd.Series(scores, index=candidates.index)

    def _agent_place_payload(self, row: pd.Series) -> dict[str, Any]:
        """Compact place dict for the agent CLI (search / recommend / place) — just enough
        for a persona to judge a place by eye."""
        def g(key: str, default: Any = "") -> Any:
            v = row.get(key)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return default
            return v
        out = {
            "place_id": str(row.get("place_id")),
            "name": g("name"),
            "city": g("city"),
            "type": g("ai_place_type_summary") or g("primary_type"),
            "tags": g("ai_tags_csv"),
            "price_level": g("price_level"),
            "google_rating": g("google_rating"),
            "summary": g("ai_card_summary"),
            "has_photo": bool(row.get("has_direct_image_embedding", False)),
        }
        if "_match" in row.index:
            out["match_score"] = int(row.get("_match", 0))
        return out

    def list_categories(self, query: str = "", *, city: str = "", limit: int = 50) -> list[dict[str, Any]]:
        """Distinct place categories (``primary_type`` — e.g. Cafe, Coffee shop, Bar, Bakery)
        matching a substring ``query``, with place counts, most common first. Lets an agent
        discover the exact category names to then filter on (``search --category ...``) instead
        of guessing. Empty query -> the top categories overall (within the optional city)."""
        cands = self.locations[self.locations["has_embedding"]].copy()
        if city and "city" in cands.columns:
            cands = cands[cands["city"].astype(str) == city]
        pt = cands["primary_type"].fillna("").astype(str)
        pt = pt[pt.str.strip() != ""]
        if query:
            q = str(query).lower()
            pt = pt[pt.str.lower().str.contains(q, regex=False)]
        counts = pt.value_counts().head(max(int(limit), 0))
        return [{"category": str(k), "count": int(v)} for k, v in counts.items()]

    def search_by_text(
        self,
        query: str = "",
        *,
        city: str = "",
        place_type: str = "",
        categories: list[str] | None = None,
        limit: int = 20,
        include_low_confidence: bool = False,
    ) -> list[dict[str, Any]]:
        """Keyword/substring catalog search — the same idea as the dashboard's multiselect
        "Search by name, type, rating..." filter. No query embedding, no external API.

        A place matches if ANY whitespace-separated word of ``query`` is a substring of its
        name / AI place type / tags / card summary. Optional ``city`` (exact) and ``place_type``
        (substring over type fields) filters. Ranked by #words matched, then map_visibility_score
        (so popular, well-described places surface first). Empty query -> top places by
        map_visibility within the filters. Returns lightweight ``_agent_place_payload`` dicts."""
        cands = self.locations[self.locations["has_embedding"]].copy()
        if not include_low_confidence:
            cands = cands[cands["ai_confidence"].fillna("").str.lower() != "low"]
        if city and "city" in cands.columns:
            cands = cands[cands["city"].astype(str) == city]
        if place_type:
            pt = place_type.lower()
            type_cols = [c for c in ("ai_place_type_summary", "primary_type", "types") if c in cands.columns]
            if type_cols:
                mask = pd.Series(False, index=cands.index)
                for col in type_cols:
                    mask = mask | cands[col].fillna("").astype(str).str.lower().str.contains(pt, regex=False)
                cands = cands[mask]
        if categories:
            cats = {str(c).strip().lower() for c in categories if str(c).strip()}
            if cats and "primary_type" in cands.columns:
                cands = cands[cands["primary_type"].fillna("").astype(str).str.lower().isin(cats)]
        if cands.empty:
            return []
        words = [w for w in str(query or "").lower().split() if w]
        has_map = "map_visibility_score" in cands.columns
        if words:
            hay_cols = [c for c in ("name", "ai_place_type_summary", "ai_tags_csv", "ai_card_summary")
                        if c in cands.columns]
            hay = cands[hay_cols].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
            match = sum(hay.str.contains(w, regex=False).astype(int) for w in words)
            cands = cands.assign(_match=match)
            cands = cands[cands["_match"] > 0]
            if cands.empty:
                return []
            cands = cands.sort_values(["_match", *(["map_visibility_score"] if has_map else [])], ascending=False)
        elif has_map:
            cands = cands.sort_values("map_visibility_score", ascending=False)
        cands = cands.head(max(int(limit), 0))
        return [self._agent_place_payload(row) for _, row in cands.iterrows()]

    def recommend_similar(
        self,
        place_id: str,
        limit: int = 50,
        config: "itr.ItemToItemConfig | None" = None,
        exclude_seed: bool = True,
        debug: bool = False,
    ) -> dict[str, Any]:
        cfg = config or itr.ItemToItemConfig()
        limit = max(int(limit or 0), 0)

        seed_group = self._seed_group(place_id)
        if seed_group is None:
            return {
                "seed_place_id": place_id,
                "algorithm_version": "item_to_item_v1",
                "embedding_run_id": self.config.embedding_run_id,
                "error": "invalid_seed",
                "candidate_count": 0,
                "recommendations": [],
            }

        candidates = self._candidate_pool(self.config)
        if exclude_seed:
            candidates = candidates[candidates["place_id"] != place_id].copy()
        candidate_count = int(len(candidates))
        if candidates.empty or limit == 0:
            return {
                "seed_place_id": place_id,
                "algorithm_version": "item_to_item_v1",
                "embedding_run_id": self.config.embedding_run_id,
                "candidate_count": candidate_count,
                "recommendations": [],
            }

        scored = self._score_similar(candidates, seed_group, cfg)
        scored = scored.sort_values(
            ["score", "place_id"], ascending=[False, True]
        ).head(int(cfg.candidate_topN))

        ordered = self._rerank_similar(scored, cfg, limit)
        seed_tags = self._profile_tag_set(seed_group)
        recommendations = self._similar_payloads(ordered, seed_tags, debug)
        return {
            "seed_place_id": place_id,
            "algorithm_version": "item_to_item_v1",
            "embedding_run_id": self.config.embedding_run_id,
            "candidate_count": candidate_count,
            "recommendations": recommendations,
        }

    def _score_similar(self, candidates: pd.DataFrame, seed_group: pd.DataFrame, cfg) -> pd.DataFrame:
        seed_vector = self._weighted_centroid(seed_group, robust=self.config.robust_centroids)
        candidate_rows = candidates["embedding_row"].astype(int).to_numpy()
        candidate_vectors = self.normalized_embeddings[candidate_rows]
        similarity = candidate_vectors @ seed_vector

        if cfg.hubness_method == "csls":
            density = itr.hubness_density(self.normalized_embeddings, candidate_rows, cfg.hubness_k)
            adjusted = similarity - cfg.hubness_penalty * density
        elif cfg.hubness_method == "mutual_knn":
            adjusted = similarity  # mutual-kNN is applied as a filter in _rerank_similar
        else:
            adjusted = similarity

        scored = candidates.copy()
        scored["similarity"] = similarity
        scored["semantic_similarity_norm"] = _calibrate_similarity(adjusted, self.config.calibration)
        if cfg.tag_overlap_version == "v1.1":
            scored["tag_overlap"] = self._polarity_tag_overlap(scored, seed_group)
        else:
            scored["tag_overlap"] = self._tag_overlap(scored, self._profile_tag_set(seed_group))
        scored["axis_similarity"] = self._axis_similarity(scored, self._weighted_axis_centroid(seed_group))
        scored["price_match"] = self._price_match(scored, seed_group)
        w = cfg.weights
        scored["score"] = (
            scored["semantic_similarity_norm"] * w.get("semantic", 0.0)
            + scored["tag_overlap"] * w.get("tag", 0.0)
            + scored["axis_similarity"] * w.get("axis", 0.0)
            + scored["quality_score"] * w.get("quality", 0.0)
            + scored["price_match"] * w.get("price", 0.0)
        )
        return scored

    def _rerank_similar(self, scored: pd.DataFrame, cfg, limit: int) -> pd.DataFrame:
        scored = scored.reset_index(drop=True)

        if cfg.hubness_method == "mutual_knn" and len(scored) > limit:
            scored = self._mutual_knn_filter(scored, cfg, limit)
            scored = scored.reset_index(drop=True)

        if not cfg.diversity_enabled or len(scored) <= limit:
            return scored.head(limit)
        candidate_rows = scored["embedding_row"].astype(int).to_numpy()
        vectors = self.normalized_embeddings[candidate_rows]
        relevance = scored["score"].to_numpy()
        order = itr.mmr_select(relevance, vectors, cfg.mmr_lambda, len(scored))
        if cfg.cluster_col in scored.columns:
            labels = scored[cfg.cluster_col].to_numpy()
            order = itr.cluster_cap(order, labels, cfg.cluster_cap)
        return scored.iloc[order].head(limit)

    def _mutual_knn_filter(self, scored: pd.DataFrame, cfg, limit: int) -> pd.DataFrame:
        """Prefer candidates whose own k-NN neighbourhood (within the shortlist)
        is reciprocated. Keeps strict order by score; used only to break hub ties.
        Falls back to the full shortlist if the filter would drop below `limit`."""
        rows = scored["embedding_row"].astype(int).to_numpy()
        sub = self.normalized_embeddings[rows]
        sims = sub @ sub.T
        np.fill_diagonal(sims, -np.inf)
        k = min(int(cfg.hubness_k), len(scored) - 1)
        if k <= 0:
            return scored
        knn = {i: set(np.argsort(sims[i])[-k:]) for i in range(len(scored))}
        mutual = [i for i in range(len(scored)) if any(i in knn[j] for j in knn[i])]
        if len(mutual) >= limit:
            return scored.iloc[sorted(mutual)]
        return scored

    def _similar_payloads(self, ordered: pd.DataFrame, seed_tags: set[str], debug: bool) -> list[dict[str, Any]]:
        recommendations = []
        for rank, (_, row) in enumerate(ordered.iterrows(), start=1):
            candidate_tags = set(row.get("recommendation_tags") or [])
            reason = sorted(candidate_tags & seed_tags) or sorted(candidate_tags)
            payload = {
                "rank": rank,
                "place_id": row["place_id"],
                "score": _round_float(row.get("score")),
                "score_components": {
                    "similarity": _round_float(row.get("similarity")),
                    "semantic_similarity_norm": _round_float(row.get("semantic_similarity_norm")),
                    "tag_overlap": _round_float(row.get("tag_overlap")),
                    "axis_similarity": _round_float(row.get("axis_similarity")),
                    "price_match": _round_float(row.get("price_match")),
                    "quality_score": _round_float(row.get("quality_score")),
                },
                "reason_tags": reason[:5],
            }
            if debug:
                payload["name"] = row.get("name")
                payload["ai_confidence"] = row.get("ai_confidence")
                payload["map_visibility_score"] = _round_float(row.get("map_visibility_score"))
            recommendations.append(payload)
        return recommendations

    def recommend_pure_component(
        self,
        selected_place_ids: list[str],
        *,
        component: str,
        weights: dict[str, float],
        limit: int,
        min_map_visibility_score: float,
        include_low_confidence: bool,
        missing_visual_policy: str | None = None,
        missing_direct_image_policy: str | None = None,
        require_column: str | None = None,
        city_filter: str = "",
        theme_group_filter: str = "",
        debug: bool = True,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Single-modality ("pure") recommendation routed through the one scoring path.

        Scores every candidate with ``_score_candidates_for_profile`` using the
        supplied ``weights`` preset, then ranks by a single component column
        (``text_similarity`` | ``visual_similarity`` | ``direct_image_similarity``).
        ``require_column`` (e.g. ``has_visual_embedding``) restricts both seeds and
        candidates to rows that carry the modality. Returns ``(profiles, payloads)``.
        """
        cfg_kwargs: dict[str, Any] = dict(
            min_saved_for_personalization=1,
            min_map_visibility_score=min_map_visibility_score,
            apply_map_visibility_filter=True,  # dashboard slider is an explicit filter
            exclude_low_confidence=not include_low_confidence,
            city_filter=city_filter,
            theme_group_filter=theme_group_filter,
            weights=weights,
        )
        if missing_visual_policy is not None:
            cfg_kwargs["missing_visual_policy"] = missing_visual_policy
        if missing_direct_image_policy is not None:
            cfg_kwargs["missing_direct_image_policy"] = missing_direct_image_policy
        cfg = RecommenderConfig(**cfg_kwargs)

        seed_df, _ = self._build_seed_dataframe(selected_place_ids, [], cfg)
        if seed_df.empty:
            return [], []
        if require_column is not None and (require_column not in seed_df.columns or not seed_df[require_column].any()):
            return [], []

        candidate_df = self._candidate_pool(cfg)
        candidate_df = candidate_df[~candidate_df["place_id"].isin(selected_place_ids)].copy()
        if require_column is not None:
            candidate_df = candidate_df[candidate_df[require_column]].copy()
        if candidate_df.empty:
            return [], []

        seed_df, _ = self._cluster_seed_places(seed_df, cfg)
        profiles = self._profile_payloads(seed_df, debug=debug)

        scored_parts = []
        for profile_id, group in seed_df.groupby("profile_id", sort=True):
            if require_column is not None and not group[require_column].any():
                continue
            scored = self._score_candidates_for_profile(candidate_df, group, int(profile_id), cfg)
            scored["score"] = scored[component]
            scored_parts.append(scored)
        if not scored_parts:
            return profiles, []

        scored_all = pd.concat(scored_parts, ignore_index=True)
        scored_all = scored_all.sort_values([component, "place_id"], ascending=[False, True])
        scored_all = scored_all.drop_duplicates("place_id", keep="first").head(limit)
        return profiles, self._recommendation_payloads(scored_all, seed_df, debug=debug)

    @staticmethod
    def _sort_scored(scored: pd.DataFrame) -> pd.DataFrame:
        return scored.sort_values(["score", "place_id"], ascending=[False, True])

    @staticmethod
    def _profile_quotas(weights: list[float], limit: int) -> list[int]:
        """Final-list slots per profile, proportional to profile_weight.

        Each profile gets >= 1 slot when ``limit >= n_profiles`` (so a sparse taste
        is never dropped); otherwise the highest-weight profiles get the slots.
        Quotas sum to ``min(limit, n_profiles*?)`` (round-robin fills any shortfall).
        """
        n = len(weights)
        if n == 0:
            return []
        if limit <= n:
            order = sorted(range(n), key=lambda i: (-weights[i], i))
            quotas = [0] * n
            for i in order[:limit]:
                quotas[i] = 1
            return quotas
        total = sum(weights) or 1.0
        raw = [limit * w / total for w in weights]
        quotas = [max(1, int(math.floor(r))) for r in raw]
        # trim if over budget (from the largest), then distribute remainder by frac part
        while sum(quotas) > limit:
            i = max(range(n), key=lambda j: (quotas[j], j))
            quotas[i] -= 1
        frac_order = sorted(range(n), key=lambda j: (raw[j] - math.floor(raw[j]), j), reverse=True)
        k = 0
        while sum(quotas) < limit:
            quotas[frac_order[k % n]] += 1
            k += 1
        return quotas

    def _mmr_order(self, scored: pd.DataFrame, cfg: RecommenderConfig, limit: int) -> pd.DataFrame:
        """MMR diversity re-rank of the final list (text-embedding space).

        Skipped (pure relevance) when mmr_lambda >= 1 or the list is large
        (limit > mmr_max_items) — the latter keeps full-catalog scoring O(n)."""
        scored = scored.reset_index(drop=True)
        if cfg.mmr_lambda >= 1.0 or len(scored) <= 1 or limit > cfg.mmr_max_items:
            return scored.head(limit)
        vectors = self.normalized_embeddings[scored["embedding_row"].astype(int).to_numpy()]
        order = itr.mmr_select(scored["score"].to_numpy(), vectors, cfg.mmr_lambda, min(limit, len(scored)))
        return scored.iloc[order].head(limit)

    def _apply_ranker(
        self,
        scored: pd.DataFrame,
        group: pd.DataFrame,
        profiles_count: int,
        profiles_silhouette: float | None,
        cfg: RecommenderConfig,
    ) -> pd.DataFrame:
        """Replace the linear-blend score with the learned ranker's relevance.

        Runs before quotas/MMR so the diversity machinery operates on the ranker's
        ordering, exactly like the linear path. Predictions are min-max normalized
        over the candidate pool: LambdaRank scores are unbounded and MMR balances
        relevance against [0,1] cosine similarities.
        """
        if cfg.ranker != "lgbm":
            return scored
        if self.ranker_model is None:
            raise ValueError(
                "ranker='lgbm' requires a trained RankerModel — load one with "
                "ranker.RankerModel.load(...) and recommender.attach_ranker_model(model)"
            )
        scored = scored.copy()
        scored["profile_size"] = int(len(group))
        scored["profile_weight"] = float(group["signal_weight"].sum())
        scored["profiles_count"] = int(profiles_count)
        scored["profiles_silhouette"] = profiles_silhouette
        if "geo_score" not in scored.columns:
            scored["geo_score"] = 0.0
        for flag in ("visual_available", "direct_image_available", "axes_available", "subtype_available", "is_chain"):
            scored[flag] = scored[flag].astype(bool)
        raw = self.ranker_model.score(scored)
        scored["ranker_raw_score"] = raw
        span = float(raw.max() - raw.min())
        scored["score"] = (raw - float(raw.min())) / span if span > 0 else 0.5
        return scored

    def _personalized_recommend(
        self,
        seed_df: pd.DataFrame,
        candidates: pd.DataFrame,
        limit: int,
        cfg: RecommenderConfig,
        debug: bool,
        profiles_silhouette: float | None = None,
    ) -> list[dict[str, Any]]:
        if candidates.empty or seed_df.empty:
            return []

        groups = list(seed_df.groupby("profile_id", sort=True))
        profile_scored = []
        profile_weights = []
        for profile_id, group in groups:
            scored = self._score_candidates_for_profile(candidates, group, int(profile_id), cfg)
            scored = self._apply_ranker(scored, group, len(groups), profiles_silhouette, cfg)
            profile_scored.append(self._sort_scored(scored).reset_index(drop=True))
            profile_weights.append(float(group["signal_weight"].sum()))

        if cfg.calibrated_rerank_lambda > 0:
            # Stage-1.5: the calibrated re-rank IS the final ordering (skip quota/MMR).
            merged = self._calibrated_rerank(profile_scored, seed_df, cfg, limit)
        else:
            if cfg.profile_quota_mode == "global" or len(profile_scored) <= 1:
                merged = pd.concat(profile_scored, ignore_index=True)
                merged = self._sort_scored(merged).drop_duplicates("place_id", keep="first").head(limit)
            else:
                merged = self._quota_round_robin(profile_scored, profile_weights, limit)
            merged = self._mmr_order(merged, cfg, limit)
        merged = self._apply_cross_theme_injection(merged, profile_scored, seed_df, cfg, limit)
        return self._recommendation_payloads(merged, seed_df, debug)

    def _apply_cross_theme_injection(self, merged, profile_scored, seed_df, cfg, limit):
        """Mix a small, vibe-relevant amount of the OTHER theme_group into a single-theme list.

        No-op when OFF, a hard theme_group_filter is set, or the data lacks theme_group. Picks the
        best minority-theme candidates by vibe_fit (axis_similarity + tag_overlap — the
        category-AGNOSTIC components, NOT the type-dominated embedding), gates them by a relevance
        floor, and interleaves K of them at lower-but-visible slots. Magnitude:
        off->0, auto->max*(1-balance) (balance = normalised theme entropy of the seeds),
        manual->cross_theme_inject_frac used DIRECTLY (absolute, dominates the adaptive base).
        Spec: docs/superpowers/specs/2026-06-28-cross-theme-injection-design.md."""
        mode = getattr(cfg, "cross_theme_inject_mode", "off")
        if (mode not in ("auto", "manual") or cfg.theme_group_filter
                or "theme_group" not in merged.columns or "theme_group" not in seed_df.columns
                or merged.empty or seed_df.empty):
            return merged
        counts = seed_df["theme_group"].astype(str).value_counts()
        if counts.empty:
            return merged
        dominant = counts.index[0]
        if mode == "auto":
            p = (counts / counts.sum()).to_numpy(dtype=float)
            balance = 0.0 if len(p) <= 1 else float(-(p * np.log(p)).sum() / np.log(len(p)))
            frac = float(cfg.cross_theme_inject_max) * (1.0 - balance)
        else:  # manual: slider value used DIRECTLY as the absolute fraction
            frac = min(max(float(cfg.cross_theme_inject_frac), 0.0), 1.0)
        k = int(round(frac * limit))
        if k <= 0:
            return merged
        pool = pd.concat(profile_scored, ignore_index=True)
        pool = pool.sort_values("score", ascending=False).drop_duplicates("place_id", keep="first")
        in_merged = set(merged["place_id"])
        minority = pool[(pool["theme_group"].astype(str) != dominant)
                        & (~pool["place_id"].isin(in_merged))].copy()
        if minority.empty:
            return merged
        minority = self._drop_inject_denylisted(minority, cfg)   # appropriateness gate (before vibe-fit)
        if minority.empty:
            return merged
        wa, wt = cfg.cross_theme_vibe_weights
        minority["vibe_fit"] = (float(wa) * minority["axis_similarity"].fillna(0.0)
                                + float(wt) * minority["tag_overlap"].fillna(0.0))
        minority = minority[minority["vibe_fit"] >= float(cfg.cross_theme_vibe_floor)]
        if minority.empty:
            return merged
        picks = minority.sort_values("vibe_fit", ascending=False).head(k).copy()
        picks["injected"] = True
        picks["inject_reason"] = "cross_theme_vibe"
        base = merged.head(limit).copy()
        if "injected" not in base.columns:
            base["injected"] = False
        rows = [base.iloc[i] for i in range(len(base))]
        stride = max(2, limit // (len(picks) + 1))
        for j in range(len(picks)):
            rows.insert(min((j + 1) * stride, len(rows)), picks.iloc[j])
        return pd.DataFrame(rows).head(limit).reset_index(drop=True)

    def _drop_inject_denylisted(self, cand: pd.DataFrame, cfg: RecommenderConfig) -> pd.DataFrame:
        """Hard appropriateness gate for cross-theme injection: drop never-injectable places
        (gambling / adult) regardless of vibe-fit.

        vibe_fit ranks on mood, not suitability — a "calm casino" scores high on the calm axes — so
        gambling/adult venues need a categorical block. Matches the model's tags
        (``recommendation_tags``) and the Google ``primary_type``, both case-insensitive. No-op when
        both denylists are empty or the columns are absent. See ``cross_theme_inject_deny_*``."""
        deny_tags = {str(t).strip().lower()
                     for t in (getattr(cfg, "cross_theme_inject_deny_tags", ()) or ()) if str(t).strip()}
        deny_types = {str(t).strip().lower()
                      for t in (getattr(cfg, "cross_theme_inject_deny_primary_types", ()) or ()) if str(t).strip()}
        if cand.empty or (not deny_tags and not deny_types):
            return cand
        keep = pd.Series(True, index=cand.index)
        if deny_types and "primary_type" in cand.columns:
            keep &= ~cand["primary_type"].astype(str).str.strip().str.lower().isin(deny_types)
        if deny_tags and "recommendation_tags" in cand.columns:
            keep &= ~cand["recommendation_tags"].apply(
                lambda ts: bool(deny_tags & {str(t).strip().lower() for t in (ts or [])}))
        return cand[keep]

    def _quota_round_robin(
        self, profile_scored: list[pd.DataFrame], weights: list[float], limit: int
    ) -> pd.DataFrame:
        """Interleave per-profile ranked candidates by weight-proportional quota.

        Round-robin keeps both dense and sparse profiles near the top; any shortfall
        (a profile exhausting its quota) is back-filled from the remaining best
        candidates across all profiles, deduped by place_id."""
        quotas = self._profile_quotas(weights, limit)
        taken: dict[str, pd.Series] = {}
        order: list[str] = []
        cursors = [0] * len(profile_scored)
        remaining = list(quotas)

        progress = True
        while len(order) < limit and progress:
            progress = False
            for i, sp in enumerate(profile_scored):
                if remaining[i] <= 0:
                    continue
                while cursors[i] < len(sp):
                    row = sp.iloc[cursors[i]]
                    cursors[i] += 1
                    pid = row["place_id"]
                    if pid not in taken:
                        taken[pid] = row
                        order.append(pid)
                        remaining[i] -= 1
                        progress = True
                        break
                if len(order) >= limit:
                    break

        if len(order) < limit:
            # back-fill from the global best remaining candidates
            leftover = pd.concat(profile_scored, ignore_index=True)
            leftover = self._sort_scored(leftover).drop_duplicates("place_id", keep="first")
            for _, row in leftover.iterrows():
                if len(order) >= limit:
                    break
                if row["place_id"] not in taken:
                    taken[row["place_id"]] = row
                    order.append(row["place_id"])

        return pd.DataFrame([taken[pid] for pid in order]).reset_index(drop=True)

    def _calibrated_rerank(
        self,
        profile_scored: list[pd.DataFrame],
        seed_df: pd.DataFrame,
        cfg: RecommenderConfig,
        limit: int,
    ) -> pd.DataFrame:
        """Stage-1.5 calibrated re-rank (Steck, RecSys 2018).

        Pulls the top ``calibrated_rerank_pool`` scored candidates (deduped across
        profiles) and greedily selects ``limit`` of them so the list's category mix
        tracks the user's favourite-category mix, trading relevance against
        KL(favourite_dist || list_dist). Falls back to plain score order when the
        category column or favourite distribution is unavailable."""
        pool = pd.concat(profile_scored, ignore_index=True)
        pool = self._sort_scored(pool).drop_duplicates("place_id", keep="first").reset_index(drop=True)
        pool = pool.head(max(int(cfg.calibrated_rerank_pool), limit))
        col = cfg.calibrated_rerank_category_col
        if col not in seed_df.columns or col not in pool.columns:
            return pool.head(limit)
        fav = seed_df[col].astype(str).str.strip()
        fav = fav[fav != ""]
        if fav.empty:
            return pool.head(limit)
        counts = fav.value_counts()
        total = float(counts.sum())
        fav_dist = {str(g): float(c) / total for g, c in counts.items()}
        return self._steck_select(pool, fav_dist, limit, float(cfg.calibrated_rerank_lambda), col)

    @staticmethod
    def _steck_select(
        pool: pd.DataFrame,
        fav_dist: dict[str, float],
        limit: int,
        lam: float,
        category_col: str,
        alpha: float = 0.01,
    ) -> pd.DataFrame:
        """Greedy calibrated selection: maximise (1-lam)*relevance - lam*KL(p||q).

        ``relevance`` is the min-max-normalised blend score over the pool; ``q`` is the
        category distribution of the running list (Steck-smoothed toward ``p`` with
        weight ``alpha`` so an empty bucket never makes KL diverge)."""
        n = len(pool)
        take = min(int(limit), n)
        if take <= 0 or not fav_dist:
            return pool.head(take)
        cats = pool[category_col].astype(str).str.strip().tolist()
        scores = pool["score"].to_numpy(dtype=float)
        smin, smax = float(scores.min()), float(scores.max())
        rng = smax - smin
        rel = (scores - smin) / rng if rng > 1e-12 else np.ones(n, dtype=float)
        chosen: list[int] = []
        chosen_counts: dict[str, int] = {}
        available = list(range(n))
        while len(chosen) < take:
            k = len(chosen)
            best_j, best_obj = available[0], -math.inf
            for j in available:
                cj = cats[j]
                kl = 0.0
                for g, pg in fav_dist.items():
                    cnt = chosen_counts.get(g, 0) + (1 if g == cj else 0)
                    qg = (1.0 - alpha) * (cnt / (k + 1)) + alpha * pg
                    if pg > 0 and qg > 0:
                        kl += pg * math.log(pg / qg)
                obj = (1.0 - lam) * rel[j] - lam * kl
                if obj > best_obj:
                    best_obj, best_j = obj, j
            chosen.append(best_j)
            cbest = cats[best_j]
            chosen_counts[cbest] = chosen_counts.get(cbest, 0) + 1
            available.remove(best_j)
        return pool.iloc[chosen].reset_index(drop=True)

    def _fallback_recommend(
        self,
        seed_df: pd.DataFrame,
        candidates: pd.DataFrame,
        limit: int,
        cfg: RecommenderConfig,
        debug: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if candidates.empty:
            return [], []

        scored = candidates.copy()
        scored["profile_id"] = 0 if len(seed_df) > 0 else None
        scored["tag_overlap"] = 0.0
        scored["axis_similarity"] = 0.0
        scored["price_match"] = 0.0
        scored["text_similarity"] = 0.0
        scored["text_similarity_norm"] = 0.0
        scored["visual_similarity"] = 0.0
        scored["visual_similarity_norm"] = 0.0
        scored["visual_available"] = scored.get("has_visual_embedding", False)
        scored["visual_seed_count"] = 0
        scored["visual_weight_active"] = 0.0
        scored["direct_image_similarity"] = 0.0
        scored["direct_image_similarity_norm"] = 0.0
        scored["direct_image_available"] = scored.get("has_direct_image_embedding", False)
        scored["direct_image_seed_count"] = 0
        scored["direct_image_weight_active"] = 0.0

        if len(seed_df) == 0:
            scored["similarity"] = 0.0
            scored["semantic_similarity_norm"] = 0.0
            scored["score"] = scored["quality_score"]
            seed_for_payload = pd.DataFrame()
            profiles = []
        else:
            seed_for_payload = seed_df.copy()
            seed_for_payload["profile_id"] = 0
            centroid = self._weighted_centroid(seed_for_payload, robust=cfg.robust_centroids)
            candidate_vectors = self.normalized_embeddings[scored["embedding_row"].astype(int).to_numpy()]
            similarity = candidate_vectors @ centroid
            scored["similarity"] = similarity
            scored["text_similarity"] = similarity
            scored["semantic_similarity_norm"] = _calibrate_similarity(similarity, cfg.calibration)
            scored["text_similarity_norm"] = scored["semantic_similarity_norm"]
            scored["score"] = (
                scored["quality_score"] * cfg.fallback_quality_weight
                + scored["semantic_similarity_norm"] * cfg.fallback_similarity_weight
            )
            profiles = self._profile_payloads(seed_for_payload, debug=debug)

        if "geo_score" in scored.columns:
            scored["score"] = scored["score"] + scored["geo_score"].fillna(0) * _as_float(cfg.weights.get("geo_distance"), 0.0)
        scored = scored.sort_values(["score", "place_id"], ascending=[False, True])
        if cfg.fallback_stratify and "global_cluster" in scored.columns and scored["global_cluster"].notna().any():
            scored = self._stratify_by_cluster(scored, limit, "global_cluster")
        else:
            scored = scored.head(limit)
        return profiles, self._recommendation_payloads(scored, seed_for_payload, debug)

    @staticmethod
    def _stratify_by_cluster(scored: pd.DataFrame, limit: int, cluster_col: str) -> pd.DataFrame:
        """Round-robin across clusters (each pre-sorted by score) so a cold user
        sees varied place types, not one cluster's hit-parade. Clusters are visited
        in order of their best score; deterministic given the score sort."""
        groups = []
        for _, group in scored.groupby(cluster_col, sort=False):
            groups.append(group.reset_index(drop=True))
        groups.sort(key=lambda g: (-float(g["score"].iloc[0]), str(g[cluster_col].iloc[0])))
        rows, cursors = [], [0] * len(groups)
        progress = True
        while len(rows) < limit and progress:
            progress = False
            for i, group in enumerate(groups):
                if cursors[i] < len(group):
                    rows.append(group.iloc[cursors[i]])
                    cursors[i] += 1
                    progress = True
                    if len(rows) >= limit:
                        break
        return pd.DataFrame(rows).reset_index(drop=True)

    def _reason_tags(self, row: pd.Series, seed_df: pd.DataFrame, profile_id: int | None, limit: int = 5) -> list[str]:
        candidate_tags = set(row.get("recommendation_tags") or [])
        if seed_df.empty or profile_id is None or "profile_id" not in seed_df.columns:
            return sorted(candidate_tags)[:limit]
        profile_rows = seed_df[seed_df["profile_id"] == profile_id]
        profile_tags = self._profile_tag_set(profile_rows)
        overlap = sorted(candidate_tags & profile_tags)
        if overlap:
            return overlap[:limit]
        return sorted(candidate_tags)[:limit]

    def _recommendation_payloads(self, scored: pd.DataFrame, seed_df: pd.DataFrame, debug: bool) -> list[dict[str, Any]]:
        recommendations = []
        for rank, (_, row) in enumerate(scored.iterrows(), start=1):
            profile_id = row.get("profile_id")
            profile_id_value = None if pd.isna(profile_id) else int(profile_id)
            payload = {
                "rank": rank,
                "place_id": row["place_id"],
                "profile_id": profile_id_value,
                "score": _round_float(row.get("score")),
                "score_components": {
                    "similarity": _round_float(row.get("similarity")),
                    "text_similarity": _round_float(row.get("text_similarity")),
                    "semantic_similarity_norm": _round_float(row.get("semantic_similarity_norm")),
                    "text_similarity_norm": _round_float(row.get("text_similarity_norm")),
                    "visual_similarity": _round_float(row.get("visual_similarity")),
                    "visual_similarity_norm": _round_float(row.get("visual_similarity_norm")),
                    "visual_available": bool(row.get("visual_available", False)),
                    "visual_seed_count": int(_as_float(row.get("visual_seed_count"), 0.0)),
                    "visual_weight_active": _round_float(row.get("visual_weight_active")),
                    "direct_image_similarity": _round_float(row.get("direct_image_similarity")),
                    "direct_image_similarity_norm": _round_float(row.get("direct_image_similarity_norm")),
                    "direct_image_available": bool(row.get("direct_image_available", False)),
                    "direct_image_seed_count": int(_as_float(row.get("direct_image_seed_count"), 0.0)),
                    "direct_image_weight_active": _round_float(row.get("direct_image_weight_active")),
                    "tag_overlap": _round_float(row.get("tag_overlap")),
                    "axis_similarity": _round_float(row.get("axis_similarity")),
                    "quality_score": _round_float(row.get("quality_score")),
                    "price_match": _round_float(row.get("price_match")),
                    "axes_available": bool(row.get("axes_available", True)),
                    "subtype_match": _round_float(row.get("subtype_match")),
                    "subtype_available": bool(row.get("subtype_available", False)),
                    "venue_subtype": str(row.get("venue_subtype") or "other"),
                    "is_chain": bool(row.get("is_chain", False)),
                },
                "reason_tags": self._reason_tags(row, seed_df, profile_id_value),
            }
            # Geo distance is opt-in: only surfaced when active (keeps payloads
            # byte-identical when the feature is off).
            if "geo_score" in row.index:
                payload["score_components"]["geo_score"] = _round_float(row.get("geo_score"))
            # Cross-theme injection: opt-in flag, only present on injected items (keeps payloads
            # byte-identical when the feature is off).
            if bool(row.get("injected", False)):
                payload["injected"] = True
                payload["inject_reason"] = str(row.get("inject_reason") or "cross_theme_vibe")
            if debug:
                payload["debug"] = {
                    "name": row.get("name"),
                    "ai_place_type_summary": row.get("ai_place_type_summary"),
                    "ai_card_summary": row.get("ai_card_summary"),
                    "ai_tags_csv": row.get("ai_tags_csv"),
                    "google_rating": None if pd.isna(row.get("google_rating")) else _round_float(row.get("google_rating")),
                    "google_user_rating_count": None
                    if pd.isna(row.get("google_user_rating_count"))
                    else int(row.get("google_user_rating_count")),
                    "map_visibility_score": _round_float(row.get("map_visibility_score")),
                    "ai_confidence": row.get("ai_confidence"),
                }
            recommendations.append(payload)
        return recommendations


def _parse_place_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run backend-ready AI location recommendations.")
    parser.add_argument("--locations-csv", default=str(DEFAULT_LOCATIONS_CSV))
    parser.add_argument("--embeddings-npy", default=str(DEFAULT_EMBEDDINGS_NPY))
    parser.add_argument("--metadata-csv", default=str(DEFAULT_METADATA_CSV))
    parser.add_argument("--visual-embeddings-npy", default=str(DEFAULT_VISUAL_EMBEDDINGS_NPY))
    parser.add_argument("--visual-metadata-path", default=str(DEFAULT_VISUAL_METADATA_PATH))
    parser.add_argument("--visual-profiles-csv", default=str(DEFAULT_VISUAL_PROFILES_CSV))
    parser.add_argument("--direct-image-embeddings-npy", default=str(DEFAULT_DIRECT_IMAGE_EMBEDDINGS_NPY))
    parser.add_argument("--direct-image-metadata-path", default=str(DEFAULT_DIRECT_IMAGE_METADATA_PATH))
    parser.add_argument("--direct-image-profiles-csv", default=str(DEFAULT_DIRECT_IMAGE_PROFILES_CSV))
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--favourites", default="", help="Comma-separated favourite place_ids.")
    parser.add_argument("--want-to-go", default="", help="Comma-separated want-to-go place_ids.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--min-map-visibility-score", type=float, default=None)
    parser.add_argument("--include-low-confidence", action="store_true")
    parser.add_argument("--missing-visual-policy", choices=sorted(MISSING_VISUAL_POLICIES), default=None)
    parser.add_argument("--missing-direct-image-policy", choices=sorted(MISSING_VISUAL_POLICIES), default=None)
    args = parser.parse_args(argv)

    config_overrides = {}
    if args.min_map_visibility_score is not None:
        config_overrides["min_map_visibility_score"] = args.min_map_visibility_score
    if args.include_low_confidence:
        config_overrides["exclude_low_confidence"] = False
    if args.missing_visual_policy:
        config_overrides["missing_visual_policy"] = args.missing_visual_policy
    if args.missing_direct_image_policy:
        config_overrides["missing_direct_image_policy"] = args.missing_direct_image_policy

    recommender = LocationRecommender.from_artifacts(
        locations_csv=args.locations_csv,
        embeddings_npy=args.embeddings_npy,
        metadata_csv=args.metadata_csv,
        visual_embeddings_npy=args.visual_embeddings_npy,
        visual_metadata_path=args.visual_metadata_path,
        visual_profiles_csv=args.visual_profiles_csv,
        direct_image_embeddings_npy=args.direct_image_embeddings_npy,
        direct_image_metadata_path=args.direct_image_metadata_path,
        direct_image_profiles_csv=args.direct_image_profiles_csv,
    )
    result = recommender.recommend(
        favourites_place_ids=_parse_place_ids(args.favourites),
        want_to_go_place_ids=_parse_place_ids(args.want_to_go),
        limit=args.limit,
        debug=args.debug,
        user_id=args.user_id,
        **config_overrides,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
