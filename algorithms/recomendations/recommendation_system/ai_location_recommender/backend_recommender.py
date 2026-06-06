from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from recommendation_system.ai_location_recommender import location_recommender_utils as utils

DEFAULT_WEIGHTS = {
    "semantic_similarity": 0.72,
    "tag_overlap": 0.10,
    "axis_similarity": 0.08,
    "quality_score": 0.07,
    "price_match": 0.03,
}


def _first_existing_path(*paths: Path) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[0]


PACKAGE_DIR = Path(__file__).resolve().parent
HANDOFF_ROOT = PACKAGE_DIR.parents[1]


DEFAULT_LOCATIONS_CSV = _first_existing_path(
    HANDOFF_ROOT / "artifacts" / "locations.csv",
    utils.PROJECT_ROOT / "artifacts" / "locations.csv",
    utils.PROJECT_ROOT
    / "data_scraping"
    / "output"
    / "backend_export"
    / "backend_dataset_metadata_preview"
    / "locations.csv",
)
DEFAULT_EMBEDDINGS_NPY = _first_existing_path(
    HANDOFF_ROOT / "artifacts" / "location_embeddings_20260531T173837Z.npy",
    utils.PROJECT_ROOT / "artifacts" / "location_embeddings_20260531T173837Z.npy",
    utils.PROJECT_ROOT
    / "recommendation_system"
    / "ai_location_recommender"
    / "data"
    / "embedding_store"
    / "location_embeddings_20260531T173837Z.npy",
)
DEFAULT_METADATA_CSV = _first_existing_path(
    HANDOFF_ROOT / "artifacts" / "location_embeddings_20260531T173837Z_metadata.csv",
    utils.PROJECT_ROOT / "artifacts" / "location_embeddings_20260531T173837Z_metadata.csv",
    utils.PROJECT_ROOT
    / "recommendation_system"
    / "ai_location_recommender"
    / "data"
    / "embedding_store"
    / "location_embeddings_20260531T173837Z_metadata.csv",
)


@dataclass(frozen=True)
class RecommenderConfig:
    algorithm_version: str = "location_recommender_v1"
    embedding_run_id: str = "20260531T173837Z"
    favorites_weight: float = 1.0
    want_to_go_weight: float = 0.55
    min_saved_for_personalization: int = 3
    max_profile_clusters: int = 4
    min_profile_silhouette: float = 0.03
    min_map_visibility_score: float = 20.0
    exclude_low_confidence: bool = True
    fallback_quality_weight: float = 0.85
    fallback_similarity_weight: float = 0.15
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    @classmethod
    def from_dict(cls, config: dict[str, Any] | None = None) -> "RecommenderConfig":
        if not config:
            return cls()
        data = dict(config)
        if "weights" in data and data["weights"] is not None:
            data["weights"] = {**DEFAULT_WEIGHTS, **dict(data["weights"])}
        return cls(**data)

    def with_overrides(self, overrides: dict[str, Any]) -> "RecommenderConfig":
        if not overrides:
            return self
        allowed = set(self.__dataclass_fields__)
        data = {key: value for key, value in overrides.items() if key in allowed and value is not None}
        if "weights" in data:
            data["weights"] = {**self.weights, **dict(data["weights"])}
        return replace(self, **data)


def _infer_embedding_run_id(path: str | Path) -> str | None:
    match = re.search(r"location_embeddings_(.+)\.npy$", str(path))
    return match.group(1) if match else None


def _normalize_matrix(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


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
        config: RecommenderConfig | dict[str, Any] | None = None,
    ):
        self.config = config if isinstance(config, RecommenderConfig) else RecommenderConfig.from_dict(config)
        self.embedding_matrix = np.asarray(embedding_matrix, dtype=np.float32)
        self.normalized_embeddings = _normalize_matrix(self.embedding_matrix)
        self.embedding_metadata = embedding_metadata.copy()
        self.locations = self._prepare_locations(locations, self.embedding_metadata)
        self.place_id_to_index = {place_id: idx for idx, place_id in enumerate(self.locations["place_id"])}

    @classmethod
    def from_artifacts(
        cls,
        locations_csv: str | Path = DEFAULT_LOCATIONS_CSV,
        embeddings_npy: str | Path = DEFAULT_EMBEDDINGS_NPY,
        metadata_csv: str | Path = DEFAULT_METADATA_CSV,
        config: RecommenderConfig | dict[str, Any] | None = None,
    ) -> "LocationRecommender":
        locations_csv = Path(locations_csv)
        embeddings_npy = Path(embeddings_npy)
        metadata_csv = Path(metadata_csv)

        locations = pd.read_csv(locations_csv)
        embedding_matrix = np.load(embeddings_npy)
        metadata = pd.read_csv(metadata_csv)

        cfg = config if isinstance(config, RecommenderConfig) else RecommenderConfig.from_dict(config)
        inferred_run_id = _infer_embedding_run_id(embeddings_npy)
        explicit_run_id = isinstance(config, RecommenderConfig) or (
            isinstance(config, dict) and "embedding_run_id" in config
        )
        if inferred_run_id and not explicit_run_id:
            cfg = replace(cfg, embedding_run_id=inferred_run_id)

        return cls(locations, embedding_matrix, metadata, cfg)

    @classmethod
    def from_dataframes(
        cls,
        locations: pd.DataFrame,
        embeddings_npy: str | Path = DEFAULT_EMBEDDINGS_NPY,
        metadata_csv: str | Path = DEFAULT_METADATA_CSV,
        config: RecommenderConfig | dict[str, Any] | None = None,
    ) -> "LocationRecommender":
        """Build recommender from a DB-backed locations dataframe plus embedding artifacts."""
        embeddings_npy = Path(embeddings_npy)
        metadata_csv = Path(metadata_csv)
        embedding_matrix = np.load(embeddings_npy)
        metadata = pd.read_csv(metadata_csv)

        cfg = config if isinstance(config, RecommenderConfig) else RecommenderConfig.from_dict(config)
        inferred_run_id = _infer_embedding_run_id(embeddings_npy)
        explicit_run_id = isinstance(config, RecommenderConfig) or (
            isinstance(config, dict) and "embedding_run_id" in config
        )
        if inferred_run_id and not explicit_run_id:
            cfg = replace(cfg, embedding_run_id=inferred_run_id)
        return cls(locations, embedding_matrix, metadata, cfg)

    def _prepare_locations(self, locations: pd.DataFrame, metadata: pd.DataFrame) -> pd.DataFrame:
        required_location_cols = {"place_id", "name", "ai_tags_json", "map_visibility_score", "ai_confidence"}
        missing_location_cols = sorted(required_location_cols - set(locations.columns))
        if missing_location_cols:
            raise ValueError(f"locations.csv is missing required columns: {missing_location_cols}")

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

        prepared["recommendation_tags"] = prepared.apply(utils.parse_tag_names, axis=1)
        prepared["quality_score"] = utils.compute_quality_score(prepared)

        for column in utils.AXIS_DEFINITIONS:
            if column in prepared.columns:
                prepared[column] = pd.to_numeric(prepared[column], errors="coerce").fillna(50.0)

        return prepared.reset_index(drop=True)

    def recommend(
        self,
        favourites_place_ids: list[str] | None,
        want_to_go_place_ids: list[str] | None,
        limit: int = 100,
        exclude_input_places: bool = True,
        debug: bool = False,
        user_id: str | None = None,
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

        if limit == 0:
            recommendations = []
            profiles = []
            fallback_used = len(seed_df) < cfg.min_saved_for_personalization
        elif len(seed_df) < cfg.min_saved_for_personalization:
            fallback_used = True
            profiles, recommendations = self._fallback_recommend(seed_df, candidate_df, limit, cfg, debug)
        else:
            fallback_used = False
            seed_df = self._cluster_seed_places(seed_df, cfg)
            profiles = self._profile_payloads(seed_df, debug=debug)
            recommendations = self._personalized_recommend(seed_df, candidate_df, limit, cfg, debug)

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
                "candidate_count": int(len(candidate_df)),
            },
            "profiles": profiles,
            "recommendations": recommendations,
        }

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
        candidates = candidates[candidates["map_visibility_score"].fillna(0) >= cfg.min_map_visibility_score].copy()
        return candidates

    def _cluster_seed_places(self, seed_df: pd.DataFrame, cfg: RecommenderConfig) -> pd.DataFrame:
        seed_vectors = self.normalized_embeddings[seed_df["embedding_row"].astype(int).to_numpy()]
        n_clusters = self._choose_profile_cluster_count(seed_vectors, cfg)
        if n_clusters == 1:
            labels = np.zeros(len(seed_df), dtype=int)
        else:
            labels = _agglomerative_cosine(n_clusters).fit_predict(seed_vectors)
        clustered = seed_df.copy()
        clustered["profile_id"] = labels.astype(int)
        return clustered

    def _choose_profile_cluster_count(self, seed_vectors: np.ndarray, cfg: RecommenderConfig) -> int:
        from sklearn.metrics import silhouette_score

        n = len(seed_vectors)
        if n < 4:
            return 1

        max_k = min(cfg.max_profile_clusters, n - 1)
        scores = []
        for k in range(2, max_k + 1):
            labels = _agglomerative_cosine(k).fit_predict(seed_vectors)
            if len(set(labels)) < 2:
                continue
            score = silhouette_score(seed_vectors, labels, metric="cosine")
            scores.append((k, score))

        if not scores:
            return 1
        best_k, best_score = max(scores, key=lambda item: item[1])
        return best_k if best_score >= cfg.min_profile_silhouette else 1

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

    def _weighted_centroid(self, group: pd.DataFrame) -> np.ndarray:
        rows = group["embedding_row"].astype(int).to_numpy()
        weights = group["signal_weight"].astype(float).to_numpy()
        vectors = self.normalized_embeddings[rows]
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

    def _score_candidates_for_profile(
        self,
        candidates: pd.DataFrame,
        group: pd.DataFrame,
        profile_id: int,
        cfg: RecommenderConfig,
    ) -> pd.DataFrame:
        centroid = self._weighted_centroid(group)
        candidate_rows = candidates["embedding_row"].astype(int).to_numpy()
        candidate_vectors = self.normalized_embeddings[candidate_rows]
        similarity = candidate_vectors @ centroid
        similarity_norm = np.clip((similarity + 1) / 2, 0, 1)

        scored = candidates.copy()
        scored["profile_id"] = int(profile_id)
        scored["similarity"] = similarity
        scored["semantic_similarity_norm"] = similarity_norm
        scored["tag_overlap"] = self._tag_overlap(scored, self._profile_tag_set(group))
        scored["axis_similarity"] = self._axis_similarity(scored, self._weighted_axis_centroid(group))
        scored["price_match"] = self._price_match(scored, group)
        scored["score"] = (
            scored["semantic_similarity_norm"] * cfg.weights["semantic_similarity"]
            + scored["tag_overlap"] * cfg.weights["tag_overlap"]
            + scored["axis_similarity"] * cfg.weights["axis_similarity"]
            + scored["quality_score"] * cfg.weights["quality_score"]
            + scored["price_match"] * cfg.weights["price_match"]
        )
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

    def _price_match(self, candidates: pd.DataFrame, group: pd.DataFrame) -> pd.Series:
        if "axis_cheap_expensive" not in candidates.columns:
            return pd.Series(0.5, index=candidates.index)
        weights = group["signal_weight"].astype(float).to_numpy()
        centroid = np.average(group["axis_cheap_expensive"].astype(float).to_numpy(), weights=weights)
        distances = candidates["axis_cheap_expensive"].astype(float).sub(centroid).abs()
        return (1 - distances / 100).clip(0, 1)

    def _personalized_recommend(
        self,
        seed_df: pd.DataFrame,
        candidates: pd.DataFrame,
        limit: int,
        cfg: RecommenderConfig,
        debug: bool,
    ) -> list[dict[str, Any]]:
        if candidates.empty or seed_df.empty:
            return []

        scored_parts = []
        for profile_id, group in seed_df.groupby("profile_id", sort=True):
            scored_parts.append(self._score_candidates_for_profile(candidates, group, int(profile_id), cfg))

        scored = pd.concat(scored_parts, ignore_index=True)
        scored = scored.sort_values(["score", "map_visibility_score", "place_id"], ascending=[False, False, True])
        scored = scored.drop_duplicates("place_id", keep="first").head(limit)
        return self._recommendation_payloads(scored, seed_df, debug)

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

        if len(seed_df) == 0:
            scored["similarity"] = 0.0
            scored["semantic_similarity_norm"] = 0.0
            scored["score"] = scored["quality_score"]
            seed_for_payload = pd.DataFrame()
            profiles = []
        else:
            seed_for_payload = seed_df.copy()
            seed_for_payload["profile_id"] = 0
            centroid = self._weighted_centroid(seed_for_payload)
            candidate_vectors = self.normalized_embeddings[scored["embedding_row"].astype(int).to_numpy()]
            similarity = candidate_vectors @ centroid
            scored["similarity"] = similarity
            scored["semantic_similarity_norm"] = np.clip((similarity + 1) / 2, 0, 1)
            scored["score"] = (
                scored["quality_score"] * cfg.fallback_quality_weight
                + scored["semantic_similarity_norm"] * cfg.fallback_similarity_weight
            )
            profiles = self._profile_payloads(seed_for_payload, debug=debug)

        scored = scored.sort_values(["score", "map_visibility_score", "place_id"], ascending=[False, False, True]).head(limit)
        return profiles, self._recommendation_payloads(scored, seed_for_payload, debug)

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
                    "semantic_similarity_norm": _round_float(row.get("semantic_similarity_norm")),
                    "tag_overlap": _round_float(row.get("tag_overlap")),
                    "axis_similarity": _round_float(row.get("axis_similarity")),
                    "quality_score": _round_float(row.get("quality_score")),
                    "price_match": _round_float(row.get("price_match")),
                },
                "reason_tags": self._reason_tags(row, seed_df, profile_id_value),
            }
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
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--favourites", default="", help="Comma-separated favourite place_ids.")
    parser.add_argument("--want-to-go", default="", help="Comma-separated want-to-go place_ids.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--min-map-visibility-score", type=float, default=None)
    parser.add_argument("--include-low-confidence", action="store_true")
    args = parser.parse_args(argv)

    config_overrides = {}
    if args.min_map_visibility_score is not None:
        config_overrides["min_map_visibility_score"] = args.min_map_visibility_score
    if args.include_low_confidence:
        config_overrides["exclude_low_confidence"] = False

    recommender = LocationRecommender.from_artifacts(
        locations_csv=args.locations_csv,
        embeddings_npy=args.embeddings_npy,
        metadata_csv=args.metadata_csv,
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
