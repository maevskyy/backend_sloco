from starlette.concurrency import run_in_threadpool

from recommendation_service.algorithms.embedding_recommender import EmbeddingRecommender
from recommendation_service.config import Settings
from recommendation_service.recommendations.schemas import (
    InputSummary,
    PersonalizedRequest,
    PersonalizedResponse,
    RecommendationItem,
)


async def recommend_personalized(
    recommender: EmbeddingRecommender,
    request: PersonalizedRequest,
    settings: Settings,
) -> PersonalizedResponse:
    limit = (
        request.limit
        if request.limit is not None
        else settings.recommend_default_limit
    )
    limit = min(limit, settings.recommend_max_limit)
    result = await run_in_threadpool(
        recommender.recommend,
        request.favourites_place_ids,
        request.want_to_go_place_ids,
        limit,
        request.exclude_input_places,
    )

    recommendations = [
        RecommendationItem(
            rank=item["rank"],
            place_id=item["place_id"],
            score=item["score"],
            similarity=item["similarity"] if request.debug else None,
        )
        for item in result["recommendations"]
    ]

    return PersonalizedResponse(
        user_id=request.user_id,
        algorithm_version=result["algorithm_version"],
        embedding_run_id=result["embedding_run_id"],
        input_summary=InputSummary(**result["input_summary"]),
        recommendations=recommendations,
    )
