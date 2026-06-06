from typing import cast

from fastapi import APIRouter, HTTPException, Request, status

from recommendation_service.algorithms.embedding_recommender import EmbeddingRecommender
from recommendation_service.config import get_settings
from recommendation_service.recommendations.schemas import (
    PersonalizedRequest,
    PersonalizedResponse,
)
from recommendation_service.recommendations.service import recommend_personalized

router = APIRouter(tags=["recommendations"])


@router.post(
    "/recommendations/personalized",
    response_model=PersonalizedResponse,
)
async def personalized_recommendations(
    payload: PersonalizedRequest,
    request: Request,
) -> PersonalizedResponse:
    recommender = getattr(request.app.state, "recommender", None)
    if recommender is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recommendation model is not loaded.",
        )
    return await recommend_personalized(
        cast(EmbeddingRecommender, recommender),
        payload,
        get_settings(),
    )

