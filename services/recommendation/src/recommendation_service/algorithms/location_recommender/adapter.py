"""Adapter for the vendored ``location_recommender_v4`` engine.

The new ``LocationRecommender.recommend()`` returns a rich object (taste profiles,
per-signal ``score_components``, reason tags, ...). This adapter maps that result
DOWN to the legacy ``RecommendationPayload`` so the gateway contract
(``POST /v1/recommendations/personalized``) stays byte-compatible and the rest of
the service does not change.

This module wraps vendored research code and is intentionally excluded from strict
mypy / ruff (see ``pyproject.toml``); keep it thin and obvious.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from recommendation_service.algorithms.embedding_recommender import (
    RecommendationPayload,
)

from . import LocationRecommender
from .backend_recommender import TEXT_DIRECT_WEIGHTS, TEXT_ONLY_WEIGHTS

if TYPE_CHECKING:
    from recommendation_service.config import Settings

# The new engine's own algorithm_version label (surfaced to the gateway/OpenAPI).
ALGORITHM_VERSION = "location_recommender_v4_more_direct"

_WEIGHTS_PRESETS = {
    "text_only": TEXT_ONLY_WEIGHTS,
    "text_direct": TEXT_DIRECT_WEIGHTS,
}


class LocationRecommenderV4Adapter:
    """Wraps ``LocationRecommender`` and exposes the legacy recommender surface."""

    def __init__(self, recommender: LocationRecommender) -> None:
        self._recommender = recommender

    @property
    def candidate_count(self) -> int:
        # The engine has no candidate_count attribute; derive the embedded-catalog
        # size from its prepared locations frame.
        locations = self._recommender.locations
        return int(locations["has_embedding"].sum())

    @property
    def locations_count(self) -> int:
        # Catalog size before the embedding join — the denominator for the
        # startup coverage guard (candidate_count / locations_count).
        return int(len(self._recommender.locations))

    def recommend(
        self,
        favourites_place_ids: list[str] | None,
        want_to_go_place_ids: list[str] | None,
        limit: int,
        dislike_place_ids: list[str] | None = None,
        hide_place_ids: list[str] | None = None,
        exclude_input_places: bool = True,
    ) -> RecommendationPayload:
        result: dict[str, Any] = self._recommender.recommend(
            favourites_place_ids,
            want_to_go_place_ids,
            dislike_place_ids=dislike_place_ids,
            hide_place_ids=hide_place_ids,
            limit=limit,
            exclude_input_places=exclude_input_places,
        )
        summary = result["input_summary"]
        recommendations = [
            {
                "rank": item["rank"],
                "place_id": item["place_id"],
                "score": item["score"],
                # legacy contract exposes a flat `similarity`; the rich result
                # keeps it inside score_components.
                "similarity": item["score_components"]["similarity"],
            }
            for item in result["recommendations"]
        ]
        return {
            "algorithm_version": result["algorithm_version"],
            "embedding_run_id": result["embedding_run_id"],
            "input_summary": {
                "favourites_count": summary["favourites_count"],
                "want_to_go_count": summary["want_to_go_count"],
                "dislike_count": summary["dislike_count"],
                "hide_count": summary["hide_count"],
                "valid_input_count": summary["valid_input_count"],
                "invalid_place_ids": summary["invalid_place_ids"],
                "candidate_count": summary["candidate_count"],
            },
            "recommendations": recommendations,
        }


def build_location_recommender_v4(
    settings: Settings,
) -> LocationRecommenderV4Adapter:
    """Construct the v4 recommender from artifacts (text-only, DB-free)."""
    weights = _WEIGHTS_PRESETS[settings.recommender_weights_preset]
    recommender = LocationRecommender.from_artifacts(
        locations_csv=settings.locations_csv_path,
        embeddings_npy=settings.embeddings_npy_path,
        metadata_csv=settings.embedding_metadata_path,
        visual_embeddings_npy=None,
        visual_metadata_path=None,
        visual_profiles_csv=None,
        direct_image_embeddings_npy=None,
        direct_image_metadata_path=None,
        direct_image_profiles_csv=None,
        config={
            "weights": weights,
            "embedding_run_id": settings.embedding_run_id,
        },
    )
    return LocationRecommenderV4Adapter(recommender)
