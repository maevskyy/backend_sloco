"""Environment settings of the walk-planner service (pydantic-settings).

Read once by ``create_app()`` (tests pass a ``Settings`` object instead). Every field is one environment
variable, matched case-insensitively; an EMPTY variable counts as unset (``PHOTO_BASE_URL=`` -> no photo
URLs). No ``.env`` file is read: the service gets its environment from the container (deploy/env.example).

  WALK_BUNDLE_DIR          required to serve: a bundle directory, comma-separated directories, or a root of
                           bundles (the newest bundle per city) -- ``walk_planner.bundle.resolve_bundle_dirs``
  PHOTO_BASE_URL           public base URL of the photo files (http(s)://... or /path): photo url = base + "/" + key
                           (unset -> url null)
  WALK_DEFAULT_LANG        ru | en: language of message / error texts when a request does not choose (ru)
  WALK_GEOMETRY_DEFAULT    geojson | polyline6: segment geometry when a request does not choose (geojson)
  LOG_LEVEL                DEBUG | INFO | WARNING | ERROR | CRITICAL (INFO); JSON lines on stdout
  ENVIRONMENT              free text echoed by GET /v1/meta (production)
  WALK_GIT_SHA             revision the image was built from (set by deploy/Dockerfile), echoed by /v1/meta
  WALK_VERIFY_BUNDLE       true: check the size + sha256 of every bundle file at start-up (the default; a
                           mismatch refuses to start). false only for fast local restarts
  WALK_STARTUP_SMOKE_PLAN  true: plan one default walk per city at start-up (straight-line routing, no
                           network) -- a bundle the planner cannot plan with fails the start-up, not a request
  WALK_MAX_BODY_BYTES      request bodies above this size get 413 (1 MiB)
  WALK_MAX_CONCURRENT_PLANS  load guard: plan / schedule / insert requests one worker process runs at once (2);
                           one more is refused at once with 503 busy + Retry-After: 2 (service/app.py WorkGuard)

Routing is configured by its own variables (``WALK_ROUTER_URL``, ``ORS_API_KEY``, ``ORS_BASE_URL``,
``WALK_ROUTE_CACHE_REDIS_URL``, ...), read per request by ``walk_planner.routing.make_provider``. They are
deliberately NOT fields here, so the secret ``ORS_API_KEY`` never enters this object (or /v1/meta).
``WEB_CONCURRENCY`` (worker processes) and ``UVICORN_*`` are read by uvicorn itself.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["LANGS", "GEOMETRIES", "LOG_LEVELS", "Settings"]

LANGS = ("ru", "en")
GEOMETRIES = ("geojson", "polyline6")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class Settings(BaseSettings):
    """The service configuration (see the module docstring for each variable)."""

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore", env_ignore_empty=True,
                                      env_file=None, frozen=True)

    walk_bundle_dir: Optional[str] = Field(None, description="WALK_BUNDLE_DIR: bundle dir, 'a,b', or a root of bundles")
    photo_base_url: Optional[str] = Field(None, description="PHOTO_BASE_URL: public base URL of the photo files")
    walk_default_lang: Literal["ru", "en"] = Field("ru", description="WALK_DEFAULT_LANG: default text language")
    walk_geometry_default: Literal["geojson", "polyline6"] = Field(
        "geojson", description="WALK_GEOMETRY_DEFAULT: default segment geometry format")
    log_level: str = Field("INFO", description="LOG_LEVEL")
    environment: str = Field("production", description="ENVIRONMENT: echoed by /v1/meta")
    walk_git_sha: Optional[str] = Field(None, description="WALK_GIT_SHA: image revision, echoed by /v1/meta")
    walk_verify_bundle: bool = Field(True, description="WALK_VERIFY_BUNDLE: sha256-check the bundle files at start-up")
    walk_startup_smoke_plan: bool = Field(True,
                                          description="WALK_STARTUP_SMOKE_PLAN: plan one walk per city at start-up")
    walk_max_body_bytes: int = Field(1_048_576, ge=1024, le=64 * 1024 * 1024,
                                     description="WALK_MAX_BODY_BYTES: larger request bodies get 413")
    walk_max_concurrent_plans: int = Field(
        2, ge=1, le=64, description="WALK_MAX_CONCURRENT_PLANS: plan / schedule / insert requests per worker at once "
                                    "(more -> 503 busy)")

    @field_validator("walk_bundle_dir", "photo_base_url", "walk_git_sha", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("photo_base_url")
    @classmethod
    def _photo_base(cls, v):
        if v is not None and not v.startswith(("http://", "https://", "/")):
            raise ValueError("PHOTO_BASE_URL must be an http(s):// URL or an absolute path (/walk-media)")
        return v

    @field_validator("walk_default_lang", "walk_geometry_default", mode="before")
    @classmethod
    def _lower(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("log_level", mode="before")
    @classmethod
    def _level(cls, v):
        text = str(v or "INFO").strip().upper()
        if text == "WARN":
            text = "WARNING"
        if text not in LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}")
        return text

    @property
    def log_level_no(self) -> int:
        """LOG_LEVEL as a ``logging`` level number."""
        return logging.getLevelName(self.log_level)

    def public(self) -> dict:
        """The settings /v1/meta shows: no paths beyond the bundle spec, nothing secret (there is nothing
        secret here by construction; routing keys are not settings)."""
        return {
            "bundle_dir": self.walk_bundle_dir,
            "photo_base_url": self.photo_base_url,
            "default_lang": self.walk_default_lang,
            "geometry_default": self.walk_geometry_default,
            "log_level": self.log_level,
            "verify_bundle": self.walk_verify_bundle,
            "startup_smoke_plan": self.walk_startup_smoke_plan,
            "max_body_bytes": self.walk_max_body_bytes,
            "max_concurrent_plans": self.walk_max_concurrent_plans,
        }
