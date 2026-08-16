from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from recommendation_service.algorithms.base import PersonalizedRecommender
from recommendation_service.config import Settings
from recommendation_service.recommendations.schemas import (
    InputSummary,
    PersonalizedRequest,
    PersonalizedResponse,
    RecommendationItem,
)


async def recommend_personalized(
    recommender: PersonalizedRecommender,
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
        favourites_place_ids=request.favourites_place_ids,
        want_to_go_place_ids=request.want_to_go_place_ids,
        dislike_place_ids=request.dislike_place_ids,
        hide_place_ids=request.hide_place_ids,
        limit=limit,
        exclude_input_places=request.exclude_input_places,
    )

    recommendations = [
        RecommendationItem(
            rank=item["rank"],
            place_id=item["place_id"],
            position=item["rank"] - 1,
            profile_id=item["profile_id"],
            score=item["score"],
            similarity=item["similarity"] if request.debug else None,
            score_components=dict(item["score_components"]),
        )
        for item in result["recommendations"]
    ]

    return PersonalizedResponse(
        user_id=request.user_id,
        # The serving id ("receipt number", event-log spec 2.1). Minted per HTTP
        # request; the gateway logs it and forwards it to the client.
        request_id=str(uuid4()),
        algorithm_version=result["algorithm_version"],
        embedding_run_id=result["embedding_run_id"],
        weights_preset=result["weights_preset"],
        fallback_used=result["fallback_used"],
        input_summary=InputSummary(**result["input_summary"]),
        recommendations=recommendations,
    )
