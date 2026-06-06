from typing import Literal

from pydantic import BaseModel, ConfigDict

from recommendation_service.config import AppEnv


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"]
    service: str
    environment: AppEnv


class AlgorithmMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str


class MetaResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    environment: AppEnv
    version: str
    algorithms: list[AlgorithmMeta]

