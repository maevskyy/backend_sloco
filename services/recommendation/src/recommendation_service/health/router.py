from fastapi import APIRouter

from recommendation_service.algorithms.registry import registry
from recommendation_service.config import get_settings
from recommendation_service.health.schemas import (
    AlgorithmMeta,
    HealthResponse,
    MetaResponse,
)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        environment=settings.app_env,
    )


@router.get("/health/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        environment=settings.app_env,
    )


@router.get("/meta", response_model=MetaResponse)
async def meta() -> MetaResponse:
    settings = get_settings()
    return MetaResponse(
        service=settings.service_name,
        environment=settings.app_env,
        version="0.1.0",
        algorithms=[
            AlgorithmMeta(name=item.name, version=item.version)
            for item in registry.list_algorithms()
        ],
    )
