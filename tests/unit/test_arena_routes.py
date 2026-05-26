"""Unit tests for the /arena route."""
import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_arena_route_returns_200(client):
    response = client.get("/arena")
    assert response.status_code == 200


def test_models_route_returns_structured_model_config(client):
    response = client.get("/models")

    assert response.status_code == 200
    data = response.get_json()
    assert data["models"]
    assert data["arena_players"]
    assert set(data["backends"]) == {"openrouter", "ollama"}
    first = data["models"][0]
    assert {"backend", "model", "display_name"} <= set(first)
