from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
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
    recommender_algorithm: Literal[
        "embedding_recommender_v1", "location_recommender_v4"
    ] = Field(
        default="embedding_recommender_v1",
        alias="RECOMMENDER_ALGORITHM",
    )
    locations_csv_path: Path = Field(
        default=Path("artifacts/locations_combined_food_ttd.csv"),
        alias="LOCATIONS_CSV_PATH",
    )
    recommender_weights_preset: Literal["text_only", "text_direct"] = Field(
        default="text_only",
        alias="RECOMMENDER_WEIGHTS_PRESET",
    )
    # Direct-image (photo) channel artifacts. Unset -> the channel stays off and the
    # engine scores text-only, whatever the weights preset says.
    direct_image_embeddings_npy_path: Path | None = Field(
        default=None,
        alias="DIRECT_IMAGE_EMBEDDINGS_NPY_PATH",
    )
    direct_image_metadata_path: Path | None = Field(
        default=None,
        alias="DIRECT_IMAGE_METADATA_PATH",
    )
    direct_image_profiles_csv_path: Path | None = Field(
        default=None,
        alias="DIRECT_IMAGE_PROFILES_CSV_PATH",
    )

    @field_validator(
        "direct_image_embeddings_npy_path",
        "direct_image_metadata_path",
        "direct_image_profiles_csv_path",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # docker-compose passes `${VAR:-}`, so "channel off" arrives as an empty
        # string; without this it would become Path(".") and be loaded as an artifact.
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
