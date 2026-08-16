from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PersonalizedRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str | None = None
    favourites_place_ids: list[str] = Field(default_factory=list)
    want_to_go_place_ids: list[str] = Field(default_factory=list)
    dislike_place_ids: list[str] = Field(default_factory=list)
    hide_place_ids: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=0)
    exclude_input_places: bool = True
    debug: bool = False


class InputSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    favourites_count: int
    want_to_go_count: int
    dislike_count: int = 0
    hide_count: int = 0
    valid_input_count: int
    invalid_place_ids: list[str]
    candidate_count: int
    profiles_count: int = 0


class RecommendationItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int
    place_id: str
    # Serving-receipt fields (event-log spec 2.1): position is 0-based; the app
    # echoes request_id + position back inside telemetry events.
    position: int
    profile_id: int | None = None
    score: float
    similarity: float | None = None
    # Full score breakdown as scored at serve time — always present, not only in
    # debug mode. The gateway persists it into rec_served_items.
    score_components: dict[str, Any] | None = None


class PersonalizedResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str | None
    request_id: str
    algorithm_version: str
    embedding_run_id: str
    weights_preset: str | None = None
    fallback_used: bool = False
    input_summary: InputSummary
    recommendations: list[RecommendationItem]
