"""Unit tests for LeaderboardService graceful degradation (no database required).

Requirements: 1.5, 2.3
"""

from unittest.mock import MagicMock

from app.leaderboard.service import LeaderboardService


class TestLeaderboardServiceUnavailable:
    """Tests for LeaderboardService when DATABASE_URL is None."""

    def test_service_unavailable_when_no_database_url(self):
        """LeaderboardService(None) should set available=False."""
        service = LeaderboardService(None)
        assert service.available is False

    def test_service_unavailable_methods_dont_crash(self):
        """Calling methods on an unavailable service should not raise."""
        service = LeaderboardService(None)
        service.init_db()
        service.record_game_results([])
        service.sync_retired_status([])
        service.get_leaderboard()
        service.get_model_summary("any/model")

    def test_get_leaderboard_returns_empty_when_unavailable(self):
        """get_leaderboard() should return [] when service is unavailable."""
        service = LeaderboardService(None)
        result = service.get_leaderboard()
        assert result == []

    def test_get_model_summary_returns_empty_when_unavailable(self):
        service = LeaderboardService(None)
        assert service.get_model_summary("any/model") == {}


def test_all_tables_keeps_same_model_separate_by_lobby():
    columns = [
        "lobby_id",
        "model_id",
        "display_name",
        "games_played",
        "wins",
        "win_pct",
        "avg_placing",
        "avg_latency_ms",
        "failure_rate_pct",
        "retired",
    ]
    cursor = MagicMock()
    cursor.description = [(column,) for column in columns]
    cursor.fetchall.return_value = [
        ("main-arena", "shared/model", "Shared", 10, 4, 40, 1.8, 800, 2, False),
        ("second-table", "shared/model", "Shared", 8, 2, 25, 2.1, 950, 5, False),
    ]
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    connection_pool = MagicMock()
    connection_pool.getconn.return_value = connection

    service = LeaderboardService(None)
    service._available = True
    service._pool = connection_pool

    rows = service.get_leaderboard()

    assert [row["lobby_id"] for row in rows] == ["main-arena", "second-table"]
    assert [row["model_id"] for row in rows] == ["shared/model", "shared/model"]
    query = cursor.execute.call_args.args[0]
    assert "GROUP BY model_id" not in query
