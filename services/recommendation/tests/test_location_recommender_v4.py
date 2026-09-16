"""Contract tests for the location_recommender_v4 adapter behind the endpoint.

These run the real ported algorithm on tiny fixtures. They assert the gateway
CONTRACT shape (identical whether a request hits the personalized or the
cold-start fallback path) plus key invariants — not exact rankings.
"""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from recommendation_service.config import get_settings
from recommendation_service.main import create_app

RESPONSE_KEYS = {
    "user_id",
    "request_id",
    "algorithm_version",
    "embedding_run_id",
    "weights_preset",
    "fallback_used",
    "input_summary",
    "recommendations",
}
INPUT_SUMMARY_KEYS = {
    "favourites_count",
    "want_to_go_count",
    "dislike_count",
    "hide_count",
    "valid_input_count",
    "invalid_place_ids",
    "candidate_count",
    "profiles_count",
}
RECOMMENDATION_KEYS = {
    "rank",
    "place_id",
    "position",
    "profile_id",
    "score",
    "similarity",
    "score_components",
}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    fixtures = Path(__file__).parent / "fixtures"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("RECOMMENDER_ALGORITHM", "location_recommender_v4")
    monkeypatch.setenv("RECOMMENDER_WEIGHTS_PRESET", "text_only")
    monkeypatch.setenv("LOCATIONS_CSV_PATH", str(fixtures / "tiny_locations.csv"))
    monkeypatch.setenv("EMBEDDINGS_NPY_PATH", str(fixtures / "tiny_embeddings.npy"))
    monkeypatch.setenv("EMBEDDING_METADATA_PATH", str(fixtures / "tiny_metadata.csv"))
    monkeypatch.setenv("EMBEDDING_RUN_ID", "test-run")
    monkeypatch.setenv("RECOMMEND_DEFAULT_LIMIT", "5")
    monkeypatch.setenv("RECOMMEND_MAX_LIMIT", "10")
    get_settings.cache_clear()

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()


def _post(client: TestClient, **body: object) -> dict:
    response = client.post("/v1/recommendations/personalized", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _assert_contract(data: dict) -> None:
    assert set(data) == RESPONSE_KEYS
    assert set(data["input_summary"]) == INPUT_SUMMARY_KEYS
    assert isinstance(data["recommendations"], list)
    for item in data["recommendations"]:
        assert set(item) == RECOMMENDATION_KEYS
        assert isinstance(item["rank"], int)
        assert isinstance(item["place_id"], str)


def test_cold_start_shape(client: TestClient) -> None:
    data = _post(client, favourites_place_ids=[], want_to_go_place_ids=[])
    _assert_contract(data)
    assert data["embedding_run_id"] == "test-run"


def test_personalized_shape(client: TestClient) -> None:
    data = _post(
        client,
        favourites_place_ids=["place_1", "place_2", "place_3"],
        want_to_go_place_ids=["place_5"],
    )
    _assert_contract(data)
    assert data["algorithm_version"]


def test_excludes_input_places(client: TestClient) -> None:
    data = _post(
        client,
        favourites_place_ids=["place_1", "place_2"],
        want_to_go_place_ids=[],
        exclude_input_places=True,
    )
    returned = {item["place_id"] for item in data["recommendations"]}
    assert "place_1" not in returned
    assert "place_2" not in returned


def test_unknown_ids_reported_invalid(client: TestClient) -> None:
    data = _post(
        client,
        favourites_place_ids=["does_not_exist"],
        want_to_go_place_ids=[],
    )
    assert "does_not_exist" in data["input_summary"]["invalid_place_ids"]


def test_similarity_hidden_unless_debug(client: TestClient) -> None:
    without = _post(client, favourites_place_ids=["place_1"], want_to_go_place_ids=[])
    for item in without["recommendations"]:
        assert item["similarity"] is None

    with_debug = _post(
        client,
        favourites_place_ids=["place_1"],
        want_to_go_place_ids=[],
        debug=True,
    )
    for item in with_debug["recommendations"]:
        assert item["similarity"] is not None


def test_excludes_disliked_and_hidden_places(client: TestClient) -> None:
    data = _post(
        client,
        favourites_place_ids=["place_1", "place_2", "place_3"],
        want_to_go_place_ids=[],
        dislike_place_ids=["place_4"],
        hide_place_ids=["place_5"],
        debug=True,
    )
    returned = {item["place_id"] for item in data["recommendations"]}
    assert "place_4" not in returned
    assert "place_5" not in returned
    assert data["input_summary"]["dislike_count"] == 1
    assert data["input_summary"]["hide_count"] == 1


def test_excluded_seed_is_not_returned_or_marked_invalid(client: TestClient) -> None:
    data = _post(
        client,
        favourites_place_ids=["place_1", "place_2"],
        want_to_go_place_ids=["place_3"],
        dislike_place_ids=["place_2"],
        hide_place_ids=["place_3"],
        debug=True,
    )
    returned = {item["place_id"] for item in data["recommendations"]}
    assert "place_2" not in returned
    assert "place_3" not in returned
    assert "place_2" not in data["input_summary"]["invalid_place_ids"]
    assert "place_3" not in data["input_summary"]["invalid_place_ids"]


def test_serving_receipt_contract(client: TestClient) -> None:
    first = _post(
        client,
        favourites_place_ids=["place_1", "place_2", "place_3"],
        want_to_go_place_ids=[],
    )
    second = _post(
        client,
        favourites_place_ids=["place_1", "place_2", "place_3"],
        want_to_go_place_ids=[],
    )

    # One uuid per serving ("receipt number") — never reused.
    UUID(first["request_id"])
    assert first["request_id"] != second["request_id"]
    assert first["weights_preset"] == "text_only"
    assert isinstance(first["fallback_used"], bool)
    assert first["input_summary"]["profiles_count"] >= 1

    assert first["recommendations"], "fixture seeds must produce candidates"
    for item in first["recommendations"]:
        assert item["position"] == item["rank"] - 1
        # The full breakdown ships WITHOUT debug — the gateway persists it into
        # rec_served_items at serve time.
        components = item["score_components"]
        assert components is not None
        for key in ("similarity", "tag_overlap", "quality_score", "price_match"):
            assert key in components


def test_exclusion_also_applies_to_cold_start_path(client: TestClient) -> None:
    data = _post(
        client,
        favourites_place_ids=["place_1"],
        want_to_go_place_ids=[],
        dislike_place_ids=["place_2"],
        hide_place_ids=["place_4"],
        debug=True,
    )
    returned = {item["place_id"] for item in data["recommendations"]}
    assert "place_2" not in returned
    assert "place_4" not in returned
