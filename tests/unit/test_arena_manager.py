"""Unit tests for ArenaManager core behavior.

Requirements: 3.1, 3.2, 3.4, 4.1, 4.3, 4.4, 8.1, 8.2, 8.3, 8.4
"""
import pytest
from unittest.mock import patch, MagicMock

from app.arena.arena_manager import ArenaManager, ARENA_PLAYER_MODELS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_arena_manager() -> ArenaManager:
    """Return a fresh ArenaManager with start_game mocked to avoid real game logic."""
    manager = ArenaManager()
    return manager


# ---------------------------------------------------------------------------
# Session creation (Requirements 3.1, 3.2, 3.3)
# ---------------------------------------------------------------------------

def test_get_or_create_session_creates_once():
    """Calling get_or_create_session twice returns the same session object."""
    manager = make_arena_manager()
    with patch.object(manager, "_create_session", wraps=manager._create_session) as mock_create:
        # Patch start_game to avoid real game logic
        with patch("app.arena.arena_manager.GameSession.start_game"):
            session1 = manager.get_or_create_session()
            session2 = manager.get_or_create_session()
    assert session1 is session2
    assert mock_create.call_count == 1


def test_session_id_is_arena():
    """The created session has session_id == 'arena'."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()
    assert session.session_id == "arena"


# ---------------------------------------------------------------------------
# Arena player configuration (Requirements 4.1, 4.3, 4.4)
# ---------------------------------------------------------------------------

def test_arena_players_match_models():
    """Session players match ARENA_PLAYER_MODELS."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()
    assert len(session.players) == len(ARENA_PLAYER_MODELS)
    player_models = [p.model for p in session.players]
    assert player_models == ARENA_PLAYER_MODELS


def test_all_players_are_ai():
    """All arena players have a 'provider' attribute (they are AI players)."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()
    for player in session.players:
        assert hasattr(player, "provider"), f"Player {player.player_id} missing 'provider' attribute"


def test_arena_state_reveals_hole_cards_during_live_hand():
    """Arena spectator state includes all AI hole cards before showdown."""
    manager = make_arena_manager()
    session = manager.get_or_create_session()

    state = manager.get_arena_state()

    assert len(state["players"]) == len(session.players)
    for player_entry in state["players"]:
        assert len(player_entry["hole_cards"]) == 2


# ---------------------------------------------------------------------------
# Viewer count tracking (Requirements 8.4)
# ---------------------------------------------------------------------------

def test_viewer_count_increments_on_join():
    """Joining 3 different sockets results in viewer_count == 3."""
    manager = make_arena_manager()
    manager.on_viewer_join("sid1")
    manager.on_viewer_join("sid2")
    manager.on_viewer_join("sid3")
    assert manager.viewer_count == 3


def test_viewer_count_decrements_on_leave():
    """Joining 3 sockets then leaving 1 results in viewer_count == 2."""
    manager = make_arena_manager()
    manager.on_viewer_join("sid1")
    manager.on_viewer_join("sid2")
    manager.on_viewer_join("sid3")
    manager.on_viewer_leave("sid1")
    assert manager.viewer_count == 2


def test_viewer_count_no_duplicate():
    """Joining the same socket ID twice only counts as one viewer."""
    manager = make_arena_manager()
    manager.on_viewer_join("sid1")
    manager.on_viewer_join("sid1")
    assert manager.viewer_count == 1


# ---------------------------------------------------------------------------
# Pause / resume behavior (Requirements 8.1, 8.2, 8.3)
# ---------------------------------------------------------------------------

def test_paused_on_last_viewer_leave():
    """When the last viewer leaves, paused becomes True."""
    manager = make_arena_manager()
    manager.on_viewer_join("sid1")
    assert manager.paused is False
    manager.on_viewer_leave("sid1")
    assert manager.paused is True


def test_resumed_on_first_viewer_join():
    """When a viewer joins a paused arena, paused becomes False."""
    manager = make_arena_manager()
    # Start in paused state (default)
    assert manager.paused is True
    manager.on_viewer_join("sid1")
    assert manager.paused is False


def test_state_preserved_during_pause():
    """Session state is unchanged after pausing (no hands or actions lost)."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    # Snapshot state before pause
    state_before = session.get_public_state()

    # Simulate a viewer joining then leaving (triggers pause)
    manager.on_viewer_join("sid1")
    manager.on_viewer_leave("sid1")

    assert manager.paused is True

    # State should be identical after pause
    state_after = session.get_public_state()
    assert state_before == state_after


# ---------------------------------------------------------------------------
# Inter-hand pause and reset logic (Requirements 6.1, 6.5, 6.6, 7.1, 7.2, 7.5)
# ---------------------------------------------------------------------------

def test_inter_hand_pause_calls_next_hand_when_viewers():
    """_inter_hand_pause calls next_hand() and dispatches AI turn when viewers present."""
    import app as app_module
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    # Set up: viewer present, session in showdown_pending state
    manager.on_viewer_join("sid1")
    session.showdown_pending = True

    mock_socketio = MagicMock()
    with patch("app.arena.arena_manager.eventlet.sleep"), \
         patch.object(session, "next_hand") as mock_next_hand, \
         patch.object(manager, "broadcast_state") as mock_broadcast, \
         patch.object(manager, "_dispatch_ai_turn"), \
         patch.object(app_module, "socketio", mock_socketio):
        manager._inter_hand_pause()

    mock_next_hand.assert_called_once()
    mock_broadcast.assert_called()


def test_inter_hand_pause_pauses_when_no_viewers():
    """_inter_hand_pause sets paused=True and does NOT call next_hand when viewer_count==0."""
    import app as app_module
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    # No viewers
    assert manager.viewer_count == 0
    session.showdown_pending = True

    mock_socketio = MagicMock()
    with patch("app.arena.arena_manager.eventlet.sleep"), \
         patch.object(session, "next_hand") as mock_next_hand, \
         patch.object(app_module, "socketio", mock_socketio):
        manager._inter_hand_pause()

    mock_next_hand.assert_not_called()
    assert manager.paused is True


def test_showdown_dispatch_schedules_one_inter_hand_pause():
    """Repeated showdown dispatches do not create duplicate pause tasks."""
    import app as app_module
    manager = make_arena_manager()
    session = manager.get_or_create_session()
    session.showdown_pending = True
    manager.paused = False

    mock_socketio = MagicMock()
    with patch.object(app_module, "socketio", mock_socketio):
        manager._dispatch_ai_turn(session)
        manager._dispatch_ai_turn(session)

    mock_socketio.start_background_task.assert_called_once_with(manager._inter_hand_pause)
    assert manager._loop_running is False
    assert manager._inter_hand_pause_running is True


def test_complete_dispatch_schedules_one_reset():
    """Repeated complete-session dispatches do not create duplicate reset tasks."""
    import app as app_module
    from app.game.models import SessionStatus

    manager = make_arena_manager()
    session = manager.get_or_create_session()
    session.status = SessionStatus.COMPLETE
    manager.paused = False

    mock_socketio = MagicMock()
    with patch.object(app_module, "socketio", mock_socketio):
        manager._dispatch_ai_turn(session)
        manager._dispatch_ai_turn(session)

    mock_socketio.start_background_task.assert_called_once_with(manager._reset_after_complete)
    assert manager._loop_running is False
    assert manager._reset_running is True


def test_viewer_join_during_transition_does_not_start_ai_loop():
    """A reconnect during showdown pause does not start a competing AI loop."""
    import app as app_module

    manager = make_arena_manager()
    manager.get_or_create_session()
    manager._inter_hand_pause_running = True

    mock_socketio = MagicMock()
    with patch.object(app_module, "socketio", mock_socketio):
        manager.on_viewer_join("sid1")

    mock_socketio.start_background_task.assert_not_called()
    assert manager.paused is False
    assert manager._loop_running is False


def test_paused_ai_turn_marks_loop_idle():
    """If the arena pauses while a turn task is alive, it can resume later."""
    manager = make_arena_manager()
    session = manager.get_or_create_session()
    ai_player = session.players[0]
    manager._loop_running = True
    manager.paused = True

    with patch("app.arena.arena_manager.eventlet.sleep"):
        manager._run_ai_turn(session, ai_player)

    assert manager._loop_running is False


def test_reset_creates_new_session():
    """_reset_after_complete replaces self.session with a new session."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        old_session = manager.get_or_create_session()

    manager.on_viewer_join("sid1")

    with patch("app.arena.arena_manager.eventlet.sleep"), \
         patch.object(manager, "broadcast_state"), \
         patch.object(manager, "_start_ai_loop") as mock_start_loop, \
         patch("app.arena.arena_manager.GameSession.start_game"):
        manager._reset_after_complete()

    assert manager.session is not old_session
    assert manager.session is not None
    mock_start_loop.assert_called_once()


def test_reset_does_not_start_loop_when_no_viewers():
    """_reset_after_complete sets paused=True and does NOT start AI loop when viewer_count==0."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        manager.get_or_create_session()

    # No viewers
    assert manager.viewer_count == 0

    with patch("app.arena.arena_manager.eventlet.sleep"), \
         patch.object(manager, "broadcast_state"), \
         patch.object(manager, "_start_ai_loop") as mock_start_loop, \
         patch("app.arena.arena_manager.GameSession.start_game"):
        manager._reset_after_complete()

    mock_start_loop.assert_not_called()
    assert manager.paused is True


# ---------------------------------------------------------------------------
# Elimination tracking (Requirements 3.1)
# ---------------------------------------------------------------------------

def test_track_eliminations_records_eliminated_players():
    """_track_eliminations appends newly eliminated player IDs."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    # Simulate eliminating two players
    session.players[0].is_eliminated = True
    session.players[1].is_eliminated = True

    manager._track_eliminations()
    assert session.players[0].player_id in manager._elimination_order
    assert session.players[1].player_id in manager._elimination_order
    assert len(manager._elimination_order) == 2


def test_track_eliminations_no_duplicates():
    """Calling _track_eliminations twice doesn't duplicate entries."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    session.players[0].is_eliminated = True
    manager._track_eliminations()
    manager._track_eliminations()
    assert manager._elimination_order.count(session.players[0].player_id) == 1


def test_track_eliminations_preserves_order():
    """Players eliminated in separate calls appear in chronological order."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    session.players[2].is_eliminated = True
    manager._track_eliminations()

    session.players[0].is_eliminated = True
    manager._track_eliminations()

    assert manager._elimination_order == [
        session.players[2].player_id,
        session.players[0].player_id,
    ]


# ---------------------------------------------------------------------------
# Game result recording (Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 4.2, 4.3, 5.2)
# ---------------------------------------------------------------------------

def test_reset_after_complete_records_game_results():
    """_reset_after_complete calls leaderboard_service.record_game_results with correct data."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    # Simulate a completed game: all but last player eliminated
    for p in session.players[:-1]:
        p.is_eliminated = True
        manager._elimination_order.append(p.player_id)

    # Set some API stats on players
    session.players[0].game_api_calls = 5
    session.players[0].game_api_failures = 1
    session.players[0].game_total_latency_ms = 1000

    # Set up mock leaderboard service
    mock_service = MagicMock()
    mock_service.available = True
    manager.leaderboard_service = mock_service

    manager.on_viewer_join("sid1")

    with patch("app.arena.arena_manager.eventlet.sleep"), \
         patch.object(manager, "broadcast_state"), \
         patch.object(manager, "_start_ai_loop"), \
         patch("app.arena.arena_manager.GameSession.start_game"):
        manager._reset_after_complete()

    mock_service.record_game_results.assert_called_once()
    results = mock_service.record_game_results.call_args[0][0]
    assert len(results) == len(session.players)

    # Winner (last non-eliminated) should have placing=1
    winner_result = next(r for r in results if r.model_id == session.players[-1].model)
    assert winner_result.placing == 1

    # First eliminated should have highest placing number
    first_elim_result = next(r for r in results if r.model_id == session.players[0].model)
    assert first_elim_result.placing == len(session.players)
    assert first_elim_result.api_calls == 5
    assert first_elim_result.api_failures == 1
    assert first_elim_result.total_latency_ms == 1000


def test_reset_after_complete_skips_recording_when_no_service():
    """_reset_after_complete does not crash when leaderboard_service is None."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        manager.get_or_create_session()

    assert manager.leaderboard_service is None

    with patch("app.arena.arena_manager.eventlet.sleep"), \
         patch.object(manager, "broadcast_state"), \
         patch("app.arena.arena_manager.GameSession.start_game"):
        # Should not raise
        manager._reset_after_complete()


def test_reset_after_complete_skips_recording_when_service_unavailable():
    """_reset_after_complete does not call record_game_results when service.available is False."""
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        manager.get_or_create_session()

    mock_service = MagicMock()
    mock_service.available = False
    manager.leaderboard_service = mock_service

    with patch("app.arena.arena_manager.eventlet.sleep"), \
         patch.object(manager, "broadcast_state"), \
         patch("app.arena.arena_manager.GameSession.start_game"):
        manager._reset_after_complete()

    mock_service.record_game_results.assert_not_called()


def test_create_session_resets_elimination_order():
    """_create_session clears the elimination order list."""
    manager = make_arena_manager()
    manager._elimination_order = ["old_player_1", "old_player_2"]

    with patch("app.arena.arena_manager.GameSession.start_game"):
        manager._create_session()

    assert manager._elimination_order == []
