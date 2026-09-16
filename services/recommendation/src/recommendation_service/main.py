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

# A mismatched artifact set (embeddings/metadata vs locations CSV) silently marks
# unmatched places has_embedding=False and drops them from candidates — see
# recommender-config-audit.md P0-2. Full matching sets sit at 100%.
COVERAGE_WARN_RATIO = 0.95


def log_v4_embedding_coverage(candidate_count: int, locations_count: int) -> None:
    coverage = candidate_count / locations_count if locations_count else 0.0
    logger.info(
        "v4 embedding coverage: %s/%s locations have embeddings (%.1f%%)",
        candidate_count,
        locations_count,
        coverage * 100,
    )
    if coverage < COVERAGE_WARN_RATIO:
        logger.warning(
            "v4 embedding coverage %.1f%% is below %.0f%% — the embeddings/metadata "
            "artifact set likely does not match LOCATIONS_CSV_PATH; unmatched places "
            "are silently excluded from candidates (recommender-config-audit.md P0-2)",
            coverage * 100,
            COVERAGE_WARN_RATIO * 100,
        )


def log_v4_direct_image_coverage(direct_count: int, locations_count: int) -> None:
    # No ratio threshold here: the photo set genuinely covers only ~91% of the
    # catalog, so only "nothing joined at all" is a misconfiguration signal.
    coverage = direct_count / locations_count if locations_count else 0.0
    logger.info(
        "v4 direct-image coverage: %s/%s locations have photo embeddings (%.1f%%)",
        direct_count,
        locations_count,
        coverage * 100,
    )
    if direct_count == 0:
        logger.warning(
            "v4 direct-image coverage is 0 — DIRECT_IMAGE_* artifacts are configured "
            "but none of them joined the catalog (wrong path, or place_ids from a "
            "different catalog); the photo channel contributes nothing and scoring "
            "silently degrades to text-only (recommender-config-audit.md P0-2)"
        )


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
        log_v4_embedding_coverage(
            recommender.candidate_count, recommender.locations_count
        )
        if (
            settings.direct_image_embeddings_npy_path
            and settings.direct_image_metadata_path
        ):
            log_v4_direct_image_coverage(
                recommender.direct_candidate_count, recommender.locations_count
            )
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
