"""Unit tests for ProfilingService graceful degradation (no database required).

Covers the historical-stats query methods added for the per-model detail
page. When DATABASE_URL is None the service should be unavailable and all
query methods should return empty/no-op results without raising.
"""

from app.profiling.service import ProfilingService


class TestProfilingServiceUnavailable:
    """Tests for ProfilingService when DATABASE_URL is None."""

    def test_service_unavailable_when_no_database_url(self):
        service = ProfilingService(None)
        assert service.available is False

    def test_query_methods_return_empty_when_unavailable(self):
        service = ProfilingService(None)
        assert service.get_model_daily_stats("any/model") == []
        assert service.get_model_style_trends("any/model") == []
        assert service.get_model_summary("any/model") == {}
        assert service.get_model_recent_hands("any/model") == []

    def test_record_game_model_stats_does_not_crash_when_unavailable(self):
        service = ProfilingService(None)
        service.record_game_model_stats(
            game_id="g1",
            model_stats=[
                {
                    "model_id": "m",
                    "api_calls": 1,
                    "api_failures": 0,
                    "latency_ms": 10,
                    "finish_pos": 1,
                }
            ],
        )
