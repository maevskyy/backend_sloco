from pydantic import BaseModel, ConfigDict, Field


class PersonalizedRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str | None = None
    favourites_place_ids: list[str] = Field(default_factory=list)
    want_to_go_place_ids: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=0)
    exclude_input_places: bool = True
    debug: bool = False


class InputSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    favourites_count: int
    want_to_go_count: int
    valid_input_count: int
    invalid_place_ids: list[str]
    candidate_count: int


class RecommendationItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int
    place_id: str
    score: float
    similarity: float | None = None


class PersonalizedResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str | None
    algorithm_version: str
    embedding_run_id: str
    input_summary: InputSummary
    recommendations: list[RecommendationItem]

