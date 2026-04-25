"""Unit tests for LeaderboardService graceful degradation (no database required).

Requirements: 1.5, 2.3
"""

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

    def test_get_leaderboard_returns_empty_when_unavailable(self):
        """get_leaderboard() should return [] when service is unavailable."""
        service = LeaderboardService(None)
        result = service.get_leaderboard()
        assert result == []
