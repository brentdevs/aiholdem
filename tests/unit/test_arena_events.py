"""Unit tests for arena socket events.

Requirements: 8.2, 9.1, 9.3, 9.6
"""
import pytest
from unittest.mock import MagicMock, patch

from app import socketio
from tests.shared_app import _app


def _make_mock_session():
    """Build a mock arena session with a realistic get_public_state return value."""
    mock_session = MagicMock()
    mock_session.get_public_state.return_value = {
        "session_id": "arena",
        "status": "active",
        "players": [],
        "community_cards": [],
        "pot": 0,
        "phase": "pre_flop",
    }
    return mock_session


# ---------------------------------------------------------------------------
# 9.1 — join_arena emits arena_state to the joining socket
# ---------------------------------------------------------------------------

def test_join_arena_emits_state():
    """Emitting join_arena causes the server to emit arena_state back.

    Validates: Requirements 9.1
    """
    mock_session = _make_mock_session()

    with patch("app.events.arena_manager") as mock_manager:
        mock_manager.get_or_create_session.return_value = mock_session
        mock_manager.on_viewer_join.return_value = None
        mock_manager.broadcast_viewer_count.return_value = None

        client = socketio.test_client(_app)
        try:
            client.get_received()  # clear any startup events
            client.emit("join_arena", {})
            received = client.get_received()
        finally:
            client.disconnect()

    event_names = [e["name"] for e in received]
    assert "arena_state" in event_names, f"Expected 'arena_state' in {event_names}"


# ---------------------------------------------------------------------------
# 8.4 / 9.3 — join_arena increments viewer count
# ---------------------------------------------------------------------------

def test_join_arena_increments_viewer_count():
    """Joining the arena increments the viewer count via on_viewer_join.

    Validates: Requirements 8.4, 9.3
    """
    mock_session = _make_mock_session()

    with patch("app.events.arena_manager") as mock_manager:
        mock_manager.get_or_create_session.return_value = mock_session
        mock_manager.on_viewer_join.return_value = None
        mock_manager.broadcast_viewer_count.return_value = None

        client = socketio.test_client(_app)
        try:
            client.get_received()
            client.emit("join_arena", {})
            client.get_received()
            mock_manager.on_viewer_join.assert_called_once()
        finally:
            client.disconnect()


# ---------------------------------------------------------------------------
# 8.1 / 8.4 — disconnect decrements viewer count
# ---------------------------------------------------------------------------

def test_disconnect_decrements_viewer_count():
    """Disconnecting after joining calls on_viewer_leave to decrement viewer count.

    Validates: Requirements 8.1, 8.4
    """
    mock_session = _make_mock_session()

    with patch("app.events.arena_manager") as mock_manager:
        mock_manager.get_or_create_session.return_value = mock_session
        mock_manager.on_viewer_join.return_value = None
        mock_manager.broadcast_viewer_count.return_value = None
        mock_manager.on_viewer_leave.return_value = None
        mock_manager._viewer_sids = set()

        client = socketio.test_client(_app)
        client.get_received()

        client.emit("join_arena", {})
        client.get_received()

        # Capture the sid that was registered
        join_call_args = mock_manager.on_viewer_join.call_args
        registered_sid = join_call_args[0][0]

        # Make _viewer_sids contain the sid so disconnect handler calls on_viewer_leave
        mock_manager._viewer_sids = {registered_sid}

        client.disconnect()

        mock_manager.on_viewer_leave.assert_called_once_with(registered_sid)


# ---------------------------------------------------------------------------
# 9.6 — join_arena resumes a paused arena
# ---------------------------------------------------------------------------

def test_join_arena_resumes_paused_arena():
    """When the arena is paused and a viewer joins, on_viewer_join is called (which sets paused=False).

    Validates: Requirements 9.6, 8.2
    """
    mock_session = _make_mock_session()

    with patch("app.events.arena_manager") as mock_manager:
        mock_manager.paused = True
        mock_manager.get_or_create_session.return_value = mock_session
        mock_manager.on_viewer_join.return_value = None
        mock_manager.broadcast_viewer_count.return_value = None

        client = socketio.test_client(_app)
        try:
            client.get_received()
            client.emit("join_arena", {})
            client.get_received()
            # on_viewer_join is the mechanism that sets paused=False
            mock_manager.on_viewer_join.assert_called_once()
        finally:
            client.disconnect()
