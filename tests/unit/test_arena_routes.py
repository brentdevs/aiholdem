"""Unit tests for the /arena route."""

from html.parser import HTMLParser

import pytest

from app import create_app
from app.ai.model_config import LobbyConfig, ModelConfig
from app.arena.arena_manager import ArenaManager


class LobbyCardLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "open-link" in attrs.get("class", "").split():
            self.urls.append(attrs["href"])


@pytest.fixture
def configured_lobbies(monkeypatch):
    lobbies = [
        LobbyConfig(
            lobby_id=lobby_id,
            name=f"Table {lobby_id}",
            description="Navigation regression table",
            players=[
                ModelConfig("ollama", f"player-{index}-a", f"Player {index} A"),
                ModelConfig("ollama", f"player-{index}-b", f"Player {index} B"),
            ],
        )
        for index, lobby_id in enumerate(
            ["first-table", "arena", "faq", "leaderboard", "models", "api"]
        )
    ]
    monkeypatch.setattr("app.routes.LOBBY_CONFIGS", lobbies)
    monkeypatch.setattr("app.arena.arena_manager.LOBBY_CONFIGS", lobbies)
    monkeypatch.setattr(
        "app.arena.arena_manager.arena_managers",
        {lobby.lobby_id: ArenaManager(lobby) for lobby in lobbies},
    )
    return lobbies


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
async def test_homepage_lists_lobbies(app):
    from app.arena.arena_manager import LOBBY_CONFIGS

    async with app.test_client() as client:
        response = await client.get("/")

        assert response.status_code == 200
        html = (await response.get_data()).decode()
        assert "aiholdem.dev" in html
        assert LOBBY_CONFIGS[0].name in html
        assert f'href="/arena/{LOBBY_CONFIGS[0].lobby_id}"' in html
        assert "https://github.com/brentdevs/aiholdem" in html


@pytest.mark.asyncio
async def test_arena_lobby_route_returns_200(app):
    from app.arena.arena_manager import LOBBY_CONFIGS

    async with app.test_client() as client:
        response = await client.get(f"/arena/{LOBBY_CONFIGS[0].lobby_id}")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_lobby_shortlink_route_returns_200(app):
    from app.arena.arena_manager import LOBBY_CONFIGS

    async with app.test_client() as client:
        response = await client.get(f"/{LOBBY_CONFIGS[0].lobby_id}")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_lobby_shortlink_unknown_returns_404(app):
    async with app.test_client() as client:
        response = await client.get("/not-a-lobby")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_leaderboard_lobby_route_selects_table(app):
    from app.arena.arena_manager import LOBBY_CONFIGS

    async with app.test_client() as client:
        response = await client.get(f"/leaderboard/{LOBBY_CONFIGS[0].lobby_id}")

        assert response.status_code == 200
        html = (await response.get_data()).decode()
        assert f'value="{LOBBY_CONFIGS[0].lobby_id}" selected' in html


@pytest.mark.asyncio
async def test_leaderboard_unknown_lobby_returns_404(app):
    async with app.test_client() as client:
        response = await client.get("/leaderboard/not-a-lobby")

        assert response.status_code == 404


@pytest.mark.asyncio
async def test_leaderboard_renders_model_stats_links(app):
    async with app.test_client() as client:
        response = await client.get("/leaderboard/arena")

    assert response.status_code == 200
    html = (await response.get_data()).decode()
    assert '\'<td class="model-name"><a href="/models/\'' in html
    assert "text-decoration: underline" in html
    assert "<th>Lobby</th>" in html
    assert "encodeURIComponent(entry.lobby_id)" in html


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


@pytest.mark.asyncio
async def test_lobby_cards_open_their_table_despite_fixed_route_collisions(app, configured_lobbies):
    async with app.test_client() as client:
        homepage = await client.get("/")
        links = LobbyCardLinks()
        links.feed((await homepage.get_data()).decode())
        assert links.urls == [f"/arena/{lobby.lobby_id}" for lobby in configured_lobbies]
        for url, lobby in zip(links.urls, configured_lobbies):
            response = await client.get(url)
            assert response.status_code == 200
            assert f"<title>{lobby.name}</title>" in (await response.get_data()).decode()

        default_page = await client.get("/arena")
        assert "<title>Table first-table</title>" in (await default_page.get_data()).decode()
