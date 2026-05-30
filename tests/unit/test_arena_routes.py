"""Unit tests for the /arena route."""

import pytest

from app import create_app


@pytest.fixture
def app():
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.mark.asyncio
async def test_arena_route_returns_200(app):
    async with app.test_client() as client:
        response = await client.get("/arena")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_models_route_returns_structured_model_config(app):
    async with app.test_client() as client:
        response = await client.get("/models")

        assert response.status_code == 200
        data = await response.get_json()
        assert data["models"]
        assert data["arena_players"]
        assert set(data["backends"]) == {"openrouter", "ollama"}
        first = data["models"][0]
        assert {"backend", "model", "display_name", "provider_url"} <= set(first)


@pytest.mark.asyncio
async def test_faq_route_renders_active_model_links(app):
    from app.arena.arena_manager import ARENA_PLAYER_CONFIGS

    async with app.test_client() as client:
        response = await client.get("/faq")

        assert response.status_code == 200
        html = (await response.get_data()).decode()
        assert "The current arena lineup:" in html
        assert ARENA_PLAYER_CONFIGS[0].display_name in html
        assert ARENA_PLAYER_CONFIGS[0].provider_url in html
