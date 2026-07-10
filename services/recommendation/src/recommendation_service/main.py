import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from recommendation_service.algorithms.base import PersonalizedRecommender
from recommendation_service.algorithms.embedding_recommender import (
    ALGORITHM_VERSION,
    EmbeddingRecommender,
)
from recommendation_service.algorithms.registry import AlgorithmDescriptor, registry
from recommendation_service.config import get_settings
from recommendation_service.health.router import router as health_router
from recommendation_service.logging import configure_logging
from recommendation_service.recommendations.router import (
    router as recommendations_router,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    started_at = time.perf_counter()

    recommender: PersonalizedRecommender
    if settings.recommender_algorithm == "location_recommender_v4":
        from recommendation_service.algorithms.location_recommender.adapter import (
            ALGORITHM_VERSION as LOCATION_V4_VERSION,
        )
        from recommendation_service.algorithms.location_recommender.adapter import (
            build_location_recommender_v4,
        )

        recommender = build_location_recommender_v4(settings)
        algorithm_version = LOCATION_V4_VERSION
    else:
        recommender = EmbeddingRecommender.from_artifacts(
            npy_path=settings.embeddings_npy_path,
            metadata_csv_path=settings.embedding_metadata_path,
            embedding_run_id=settings.embedding_run_id,
            favorites_weight=settings.favorites_weight,
            want_to_go_weight=settings.want_to_go_weight,
        )
        algorithm_version = ALGORITHM_VERSION

    app.state.recommender = recommender
    registry.register(
        AlgorithmDescriptor(
            name=algorithm_version,
            version=settings.embedding_run_id,
        )
    )
    logger.info(
        "Loaded recommender: algorithm=%s candidates=%s "
        "embedding_run_id=%s elapsed_ms=%.2f",
        algorithm_version,
        recommender.candidate_count,
        settings.embedding_run_id,
        (time.perf_counter() - started_at) * 1000,
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Sloco Recommendation Service",
        version="0.1.0",
        docs_url="/v1/docs",
        openapi_url="/v1/openapi.json",
        lifespan=lifespan,
    )
    app.include_router(health_router, prefix="/v1")
    app.include_router(recommendations_router, prefix="/v1")
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "recommendation_service.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.app_env == "development",
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    run()
