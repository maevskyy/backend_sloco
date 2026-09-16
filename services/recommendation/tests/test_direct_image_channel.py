"""Direct-image (photo) channel wiring for the v4 engine (TASKS_7).

The channel is artifact-driven: with no ``DIRECT_IMAGE_*`` paths the engine scores
text-only exactly as before, and with them it must actually join the catalog — a
silent 0-row join is the P0-2 failure mode, so it WARNs.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from recommendation_service.algorithms.registry import AlgorithmRegistry
from recommendation_service.config import get_settings
from recommendation_service.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
LOGGER_NAME = "recommendation_service.main"
DIRECT_ENV = ("DIRECT_IMAGE_EMBEDDINGS_NPY_PATH", "DIRECT_IMAGE_METADATA_PATH")


@pytest.fixture(autouse=True)
def throwaway_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Booting the app appends to a process-wide algorithm registry that /v1/meta
    # reads; give this module its own so the v4 label cannot leak into other tests.
    monkeypatch.setattr(
        "recommendation_service.main.registry", AlgorithmRegistry(), raising=True
    )


@contextmanager
def build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    preset: str = "text_direct",
    direct_metadata: Path | None = None,
) -> Iterator[TestClient]:
    """Boot on the tiny fixtures; ``direct_metadata`` turns the photo channel on."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("RECOMMENDER_ALGORITHM", "location_recommender_v4")
    monkeypatch.setenv("RECOMMENDER_WEIGHTS_PRESET", preset)
    monkeypatch.setenv("LOCATIONS_CSV_PATH", str(FIXTURES / "tiny_locations.csv"))
    monkeypatch.setenv("EMBEDDINGS_NPY_PATH", str(FIXTURES / "tiny_embeddings.npy"))
    monkeypatch.setenv("EMBEDDING_METADATA_PATH", str(FIXTURES / "tiny_metadata.csv"))
    monkeypatch.setenv("EMBEDDING_RUN_ID", "test-run")
    monkeypatch.setenv("RECOMMEND_DEFAULT_LIMIT", "5")
    monkeypatch.setenv("RECOMMEND_MAX_LIMIT", "10")
    if direct_metadata is None:
        for name in DIRECT_ENV:
            monkeypatch.delenv(name, raising=False)
    else:
        npy = FIXTURES / "tiny_direct_embeddings.npy"
        monkeypatch.setenv("DIRECT_IMAGE_EMBEDDINGS_NPY_PATH", str(npy))
        monkeypatch.setenv("DIRECT_IMAGE_METADATA_PATH", str(direct_metadata))
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        yield client

    get_settings.cache_clear()


def ranked(client: TestClient) -> list[tuple[str, float]]:
    response = client.post(
        "/v1/recommendations/personalized",
        json={
            "favourites_place_ids": ["place_1", "place_2", "place_3"],
            "want_to_go_place_ids": [],
            "debug": True,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["recommendations"], "fixture should always yield candidates"
    return [(item["place_id"], item["score"]) for item in data["recommendations"]]


def warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_channel_off_by_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with build_client(monkeypatch, preset="text_only") as client:
            assert ranked(client)
    assert "direct-image coverage" not in caplog.text
    assert not warnings(caplog)


def test_channel_on_logs_coverage_and_changes_ranking(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    metadata = FIXTURES / "tiny_direct_metadata.parquet"
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with build_client(monkeypatch, direct_metadata=metadata) as client:
            with_photos = ranked(client)
    assert "direct-image coverage: 4/6" in caplog.text
    assert not warnings(caplog)

    # Same weights, no artifacts: any difference is the photo signal itself.
    with build_client(monkeypatch) as client:
        without_photos = ranked(client)
    assert with_photos != without_photos


def test_metadata_matching_nothing_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    # Right shape, wrong catalog — the join produces zero rows instead of failing.
    foreign = tmp_path / "foreign_direct_metadata.parquet"
    pd.DataFrame(
        {
            "place_key": ["other_1", "other_2", "other_3", "other_4"],
            "place_id": ["other_1", "other_2", "other_3", "other_4"],
            "direct_place_embedding_row": range(4),
            "has_direct_image_embedding": True,
        }
    ).to_parquet(foreign, index=False)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with build_client(monkeypatch, direct_metadata=foreign) as client:
            assert ranked(client)
    assert "direct-image coverage: 0/6" in caplog.text
    assert any("coverage is 0" in record.getMessage() for record in warnings(caplog))
