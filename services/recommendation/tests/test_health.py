from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "recommendation-service",
        "environment": "test",
    }


def test_ready_returns_ok(client: TestClient) -> None:
    response = client.get("/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_meta_returns_embedding_algorithm(client: TestClient) -> None:
    response = client.get("/v1/meta")

    assert response.status_code == 200
    assert response.json() == {
        "service": "recommendation-service",
        "environment": "test",
        "version": "0.1.0",
        "algorithms": [{"name": "embedding_recommender_v1", "version": "test-run"}],
    }

