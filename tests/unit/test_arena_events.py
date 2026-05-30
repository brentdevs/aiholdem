"""Unit tests for arena socket events.

Requirements: 8.2, 9.1, 9.3, 9.6
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_arena_state():
    return {
        "session_id": "arena",
        "status": "active",
        "players": [
            {
                "player_id": "p1",
                "name": "AI 1",
                "hole_cards": [{"rank": 14, "suit": "S"}, {"rank": 13, "suit": "H"}],
            }
        ],
        "community_cards": [],
        "pot": 0,
        "phase": "pre_flop",
        "live_move_logs": [],
    }


# ---------------------------------------------------------------------------
# 9.1 — join_arena emits arena_state to the joining socket
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_join_arena_emits_state():
    """Validates: Requirements 9.1"""
    from app import sio
    from app.arena.arena_manager import arena_manager

    arena_state = _make_mock_arena_state()

    with patch.object(arena_manager, "get_or_create_session"), patch.object(
        arena_manager, "get_arena_state", return_value=arena_state
    ), patch.object(arena_manager, "on_viewer_join"), patch.object(
        sio, "emit", new_callable=AsyncMock
    ) as mock_emit, patch.object(
        sio, "enter_room", new_callable=AsyncMock
    ):
        arena_manager.broadcast_viewer_count = AsyncMock()

        import app.events

        await app.events.on_join_arena("test-sid", {})

        mock_emit.assert_any_call("arena_state", arena_state, to="test-sid")


# ---------------------------------------------------------------------------
# 8.4 / 9.3 — join_arena increments viewer count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_join_arena_calls_on_viewer_join():
    """Validates: Requirements 8.4, 9.3"""
    from app import sio
    from app.arena.arena_manager import arena_manager

    with patch.object(arena_manager, "get_or_create_session"), patch.object(
        arena_manager, "get_arena_state", return_value=_make_mock_arena_state()
    ), patch.object(arena_manager, "on_viewer_join") as mock_join, patch.object(
        sio, "emit", new_callable=AsyncMock
    ), patch.object(
        sio, "enter_room", new_callable=AsyncMock
    ):
        arena_manager.broadcast_viewer_count = AsyncMock()

        import app.events

        await app.events.on_join_arena("test-sid", {})

        mock_join.assert_called_once_with("test-sid")


# ---------------------------------------------------------------------------
# 8.1 / 8.4 — disconnect decrements viewer count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_calls_on_viewer_leave():
    """Validates: Requirements 8.1, 8.4"""
    from app import sio
    from app.arena.arena_manager import arena_manager

    arena_manager._viewer_sids = {"test-sid"}

    with patch.object(arena_manager, "on_viewer_leave") as mock_leave, patch.object(
        sio, "emit", new_callable=AsyncMock
    ):
        arena_manager.broadcast_viewer_count = AsyncMock()

        import app.events

        await app.events.on_disconnect("test-sid")

        mock_leave.assert_called_once_with("test-sid")

    arena_manager._viewer_sids = set()


# ---------------------------------------------------------------------------
# 9.6 — join_arena resumes a paused arena
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_join_arena_resumes_paused_arena():
    """Validates: Requirements 9.6, 8.2"""
    from app import sio
    from app.arena.arena_manager import arena_manager

    with patch.object(arena_manager, "get_or_create_session"), patch.object(
        arena_manager, "get_arena_state", return_value=_make_mock_arena_state()
    ), patch.object(arena_manager, "on_viewer_join") as mock_join, patch.object(
        sio, "emit", new_callable=AsyncMock
    ), patch.object(
        sio, "enter_room", new_callable=AsyncMock
    ):
        arena_manager.broadcast_viewer_count = AsyncMock()

        import app.events

        await app.events.on_join_arena("test-sid", {})

        mock_join.assert_called_once_with("test-sid")
