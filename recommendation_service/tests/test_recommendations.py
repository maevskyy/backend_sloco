from fastapi.testclient import TestClient


def test_recommendations_are_ranked_by_similarity(client: TestClient) -> None:
    response = client.post(
        "/v1/recommendations/personalized",
        json={"favourites_place_ids": ["place_1"], "limit": 3, "debug": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["algorithm_version"] == "embedding_recommender_v1"
    assert data["embedding_run_id"] == "test-run"
    assert data["input_summary"]["valid_input_count"] == 1
    assert data["input_summary"]["candidate_count"] == 5
    assert [item["place_id"] for item in data["recommendations"]] == [
        "place_5",
        "place_2",
        "place_4",
    ]
    assert data["recommendations"][0]["score"] >= data["recommendations"][1]["score"]
    assert data["recommendations"][0]["similarity"] is not None


def test_exclude_input_places_can_be_disabled(client: TestClient) -> None:
    response = client.post(
        "/v1/recommendations/personalized",
        json={
            "favourites_place_ids": ["place_1"],
            "exclude_input_places": False,
            "limit": 1,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["input_summary"]["candidate_count"] == 6
    assert data["recommendations"][0]["place_id"] == "place_1"


def test_unknown_ids_are_reported(client: TestClient) -> None:
    response = client.post(
        "/v1/recommendations/personalized",
        json={
            "favourites_place_ids": ["missing_place", "place_1"],
            "want_to_go_place_ids": ["other_missing"],
            "limit": 2,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["input_summary"]["invalid_place_ids"] == [
        "missing_place",
        "other_missing",
    ]
    assert data["input_summary"]["valid_input_count"] == 1


def test_empty_input_returns_no_recommendations(client: TestClient) -> None:
    response = client.post("/v1/recommendations/personalized", json={})

    assert response.status_code == 200
    data = response.json()
    assert data["input_summary"]["valid_input_count"] == 0
    assert data["recommendations"] == []


def test_recommendations_are_deterministic(client: TestClient) -> None:
    payload = {
        "favourites_place_ids": ["place_1"],
        "want_to_go_place_ids": ["place_4"],
        "limit": 4,
    }

    first = client.post("/v1/recommendations/personalized", json=payload)
    second = client.post("/v1/recommendations/personalized", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


def test_limit_is_capped_by_settings(client: TestClient) -> None:
    response = client.post(
        "/v1/recommendations/personalized",
        json={"favourites_place_ids": ["place_1"], "limit": 100},
    )

    assert response.status_code == 200
    assert len(response.json()["recommendations"]) == 5


def test_debug_false_hides_similarity(client: TestClient) -> None:
    response = client.post(
        "/v1/recommendations/personalized",
        json={"favourites_place_ids": ["place_1"], "limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["recommendations"][0]["similarity"] is None

