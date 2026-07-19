"""Tests for lobby-aware historical model statistics."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app import create_app
from app.profiling.service import ProfilingService, _compute_model_af


@pytest.fixture
def app():
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.mark.asyncio
async def test_model_stats_page_supports_slash_model_ids(app):
    async with app.test_client() as client:
        response = await client.get("/models/google/gemini-2.5-flash?lobby_id=arena")

    assert response.status_code == 200
    html = (await response.get_data()).decode()
    assert "google/gemini-2.5-flash" in html
    assert "model_stats" not in html
    assert "chart.js" in html.lower()
    assert 'value="arena"' in html
    assert 'const initialLobbyId = "arena"' in html
    assert "<h2>Daily activity and placement</h2>" in html
    assert "<h2>Poker style by day</h2>" in html
    assert 'class="profile-panel" aria-label="Player profile"' in html
    assert "hands sampled" in html
    assert "ordinalPlacement(row.position)" in html
    assert "backgroundColor: 'transparent'" in html
    assert "<th>Game</th>" not in html
    assert "Final stack" not in html
    assert "Net profit" not in html


@pytest.mark.asyncio
async def test_model_stats_page_rejects_unknown_lobby(app):
    async with app.test_client() as client:
        response = await client.get("/models/google/gemini?lobby_id=missing")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_model_stats_page_defaults_to_default_lobby(app):
    async with app.test_client() as client:
        response = await client.get("/models/google/gemini-2.5-flash")

    assert response.status_code == 200
    html = (await response.get_data()).decode()
    assert 'const initialLobbyId = "arena"' in html


@pytest.mark.asyncio
async def test_model_stats_page_links_active_model_to_provider(app):
    from app.arena.arena_manager import LOBBY_CONFIGS

    lobby = LOBBY_CONFIGS[0]
    player = lobby.players[0]
    async with app.test_client() as client:
        response = await client.get(f"/models/{player.model_id}?lobby_id={lobby.lobby_id}")

    assert response.status_code == 200
    html = (await response.get_data()).decode()
    assert f'href="{player.provider_url}"' in html
    assert 'target="_blank"' in html
    assert "activeProviderLinks" in html


@pytest.mark.asyncio
async def test_model_stats_page_leaves_inactive_model_id_as_plain_text(app):
    async with app.test_client() as client:
        response = await client.get("/models/historical/providerless-model?lobby_id=arena")

    assert response.status_code == 200
    html = (await response.get_data()).decode()
    assert 'id="model-provider-link"' in html
    assert 'href="#"' in html
    assert 'id="model-id-text"' in html


@pytest.mark.asyncio
async def test_model_summary_passes_lobby_scope(app):
    service = MagicMock()
    service.available = True
    service.get_model_summary.return_value = {
        "model_id": "google/gemini",
        "display_name": "Gemini",
        "games_played": 4,
    }
    app.leaderboard_service = service

    async with app.test_client() as client:
        response = await client.get("/api/models/google/gemini/summary?lobby_id=arena")

    assert response.status_code == 200
    service.get_model_summary.assert_called_once_with("google/gemini", lobby_id="arena")


@pytest.mark.asyncio
async def test_model_summary_rejects_all_lobby_scope(app):
    service = MagicMock()
    service.available = True
    service.get_model_summary.return_value = {"model_id": "google/gemini"}
    app.leaderboard_service = service

    async with app.test_client() as client:
        response = await client.get("/api/models/google/gemini/summary?lobby_id=all")

    assert response.status_code == 404
    service.get_model_summary.assert_not_called()


@pytest.mark.asyncio
async def test_model_history_validates_days(app):
    async with app.test_client() as client:
        not_integer = await client.get("/api/models/google/gemini/history?lobby_id=arena&days=nope")
        out_of_range = await client.get("/api/models/google/gemini/history?lobby_id=arena&days=366")

    assert not_integer.status_code == 400
    assert out_of_range.status_code == 400


@pytest.mark.asyncio
async def test_model_history_passes_scope_and_days(app):
    service = MagicMock()
    service.available = True
    service.get_model_history.return_value = {"daily": [], "placements": []}
    app.profiling_service = service

    async with app.test_client() as client:
        response = await client.get("/api/models/google/gemini/history?lobby_id=arena&days=90")

    assert response.status_code == 200
    service.get_model_history.assert_called_once_with("google/gemini", lobby_id="arena", days=90)


@pytest.mark.asyncio
async def test_model_api_rejects_unknown_lobby_before_query(app):
    service = MagicMock()
    service.available = True
    app.profiling_service = service

    async with app.test_client() as client:
        response = await client.get("/api/models/google/gemini/games?lobby_id=missing")

    assert response.status_code == 404
    service.get_model_games.assert_not_called()


@pytest.mark.asyncio
async def test_model_api_returns_503_when_profiling_unavailable(app):
    async with app.test_client() as client:
        response = await client.get("/api/models/google/gemini/style?lobby_id=arena")

    assert response.status_code == 503


def test_compute_model_af_aggregates_all_actions():
    rows = [
        (
            [
                ("flop", "Gemini", "R", 20),
                ("turn", "Gemini", "C", 30),
            ],
            "Gemini",
        ),
        (
            [
                ("flop", "Gemini", "R", 20),
                ("river", "Other", "C", 20),
            ],
            "Gemini",
        ),
    ]

    assert _compute_model_af(rows) == 2.0


class _RecordingCursor:
    def __init__(self):
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.executions.append((query, params))


class _RecordingConnection:
    def __init__(self):
        self.cursor_instance = _RecordingCursor()
        self.committed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


class _RecordingPool:
    def __init__(self, connection):
        self.connection = connection

    def getconn(self):
        return self.connection

    def putconn(self, _connection):
        pass


def test_record_game_end_persists_model_metrics():
    connection = _RecordingConnection()
    service = ProfilingService(None)
    service._available = True
    service._pool = _RecordingPool(connection)

    service.record_game_end(
        "game-1",
        [
            {
                "player_id": "arena_openrouter_google_gemini",
                "finish_position": 1,
                "final_stack": 4000,
                "buy_in": 1000,
                "net_profit": 3000,
                "model_id": "google/gemini",
                "display_name": "Gemini",
                "api_calls": 14,
                "api_failures": 2,
                "latency_sum_ms": 2800,
            }
        ],
    )

    metric_writes = [
        params
        for query, params in connection.cursor_instance.executions
        if "INSERT INTO model_game_metrics" in query
    ]
    assert metric_writes == [
        (
            "game-1",
            "arena_openrouter_google_gemini",
            "google/gemini",
            "Gemini",
            14,
            2,
            2800,
        )
    ]
    assert connection.committed is True


def test_rows_as_dicts_serializes_timestamps():
    cursor = MagicMock()
    cursor.description = [("ended_at",), ("games",)]
    cursor.fetchall.return_value = [(datetime(2026, 7, 19, tzinfo=timezone.utc), 3)]

    assert ProfilingService._rows_as_dicts(cursor) == [
        {"ended_at": "2026-07-19T00:00:00+00:00", "games": 3}
    ]
