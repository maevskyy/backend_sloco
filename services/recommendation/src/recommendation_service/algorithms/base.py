"""Shared recommender contract.

Both the legacy ``EmbeddingRecommender`` and the new ``location_recommender_v4``
adapter satisfy this structural protocol, so the service and router layers stay
algorithm-agnostic and the wire contract cannot drift between algorithms.
"""

from __future__ import annotations

from typing import Protocol

from recommendation_service.algorithms.embedding_recommender import (
    RecommendationPayload,
)

__all__ = ["PersonalizedRecommender", "RecommendationPayload"]


class PersonalizedRecommender(Protocol):
    """Anything the recommendations service can call at request time."""

    @property
    def candidate_count(self) -> int: ...

    def recommend(
        self,
        favourites_place_ids: list[str] | None,
        want_to_go_place_ids: list[str] | None,
        limit: int,
        dislike_place_ids: list[str] | None = ...,
        hide_place_ids: list[str] | None = ...,
        exclude_input_places: bool = ...,
    ) -> RecommendationPayload: ...
