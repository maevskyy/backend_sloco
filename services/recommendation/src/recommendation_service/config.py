from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]
LogLevel = Literal["debug", "info", "warning", "error", "critical"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: AppEnv = Field(default="development", alias="APP_ENV")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT", gt=0)
    log_level: LogLevel = Field(default="info", alias="LOG_LEVEL")
    service_name: str = Field(
        default="recommendation-service",
        alias="SERVICE_NAME",
        min_length=1,
    )
    embeddings_npy_path: Path = Field(
        default=Path("artifacts/location_embeddings_20260531T173837Z.npy"),
        alias="EMBEDDINGS_NPY_PATH",
    )
    embedding_metadata_path: Path = Field(
        default=Path("artifacts/location_embeddings_20260531T173837Z_metadata.csv"),
        alias="EMBEDDING_METADATA_PATH",
    )
    embedding_run_id: str = Field(
        default="20260531T173837Z",
        alias="EMBEDDING_RUN_ID",
        min_length=1,
    )
    recommend_default_limit: int = Field(
        default=50,
        alias="RECOMMEND_DEFAULT_LIMIT",
        ge=0,
        le=200,
    )
    recommend_max_limit: int = Field(
        default=200,
        alias="RECOMMEND_MAX_LIMIT",
        ge=1,
        le=1000,
    )
    favorites_weight: float = Field(default=1.0, alias="FAVORITES_WEIGHT", gt=0)
    want_to_go_weight: float = Field(default=0.55, alias="WANT_TO_GO_WEIGHT", gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
