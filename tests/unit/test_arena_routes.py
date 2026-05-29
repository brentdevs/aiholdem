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
    assert {"backend", "model", "display_name", "provider_url"} <= set(first)


def test_faq_route_renders_active_model_links(client):
    from app.arena.arena_manager import ARENA_PLAYER_CONFIGS

    response = client.get("/faq")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "The current arena lineup:" in html
    assert ARENA_PLAYER_CONFIGS[0].display_name in html
    assert ARENA_PLAYER_CONFIGS[0].provider_url in html
