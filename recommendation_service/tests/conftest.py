from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recommendation_service.config import get_settings
from recommendation_service.main import create_app


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    fixtures_dir = Path(__file__).parent / "fixtures"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("EMBEDDINGS_NPY_PATH", str(fixtures_dir / "tiny_embeddings.npy"))
    monkeypatch.setenv(
        "EMBEDDING_METADATA_PATH",
        str(fixtures_dir / "tiny_metadata.csv"),
    )
    monkeypatch.setenv("EMBEDDING_RUN_ID", "test-run")
    monkeypatch.setenv("RECOMMEND_DEFAULT_LIMIT", "3")
    monkeypatch.setenv("RECOMMEND_MAX_LIMIT", "5")
    get_settings.cache_clear()

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()
