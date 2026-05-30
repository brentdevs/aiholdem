"""Unit tests for ArenaManager core behavior.

Requirements: 3.1, 3.2, 3.4, 4.1, 4.3, 4.4, 8.1, 8.2, 8.3, 8.4
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.arena.arena_manager import ARENA_PLAYER_MODELS, ArenaManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_arena_manager() -> ArenaManager:
    return ArenaManager()


# ---------------------------------------------------------------------------
# Session creation (Requirements 3.1, 3.2, 3.3)
# ---------------------------------------------------------------------------


def test_get_or_create_session_creates_once():
    manager = make_arena_manager()
    with patch.object(manager, "_create_session", wraps=manager._create_session) as mock_create:
        with patch("app.arena.arena_manager.GameSession.start_game"):
            session1 = manager.get_or_create_session()
            session2 = manager.get_or_create_session()
    assert session1 is session2
    assert mock_create.call_count == 1


def test_session_id_is_arena():
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()
    assert session.session_id == "arena"


# ---------------------------------------------------------------------------
# Arena player configuration (Requirements 4.1, 4.3, 4.4)
# ---------------------------------------------------------------------------


def test_arena_players_match_models():
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()
    assert len(session.players) == len(ARENA_PLAYER_MODELS)
    player_models = [p.model for p in session.players]
    assert player_models == ARENA_PLAYER_MODELS


def test_all_players_are_ai():
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()
    for player in session.players:
        assert hasattr(player, "provider")


def test_arena_state_reveals_hole_cards_during_live_hand():
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
    manager = make_arena_manager()
    manager.on_viewer_join("sid1")
    manager.on_viewer_join("sid2")
    manager.on_viewer_join("sid3")
    assert manager.viewer_count == 3


def test_viewer_count_decrements_on_leave():
    manager = make_arena_manager()
    manager.on_viewer_join("sid1")
    manager.on_viewer_join("sid2")
    manager.on_viewer_join("sid3")
    manager.on_viewer_leave("sid1")
    assert manager.viewer_count == 2


def test_viewer_count_no_duplicate():
    manager = make_arena_manager()
    manager.on_viewer_join("sid1")
    manager.on_viewer_join("sid1")
    assert manager.viewer_count == 1


# ---------------------------------------------------------------------------
# Pause / resume behavior (Requirements 8.1, 8.2, 8.3)
# ---------------------------------------------------------------------------


def test_paused_on_last_viewer_leave():
    manager = make_arena_manager()
    manager.on_viewer_join("sid1")
    assert manager.paused is False
    manager.on_viewer_leave("sid1")
    assert manager.paused is True


def test_resumed_on_first_viewer_join():
    manager = make_arena_manager()
    assert manager.paused is True
    manager.on_viewer_join("sid1")
    assert manager.paused is False


def test_state_preserved_during_pause():
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()
    state_before = session.get_public_state()
    manager.on_viewer_join("sid1")
    manager.on_viewer_leave("sid1")
    assert manager.paused is True
    state_after = session.get_public_state()
    assert state_before == state_after


# ---------------------------------------------------------------------------
# Inter-hand pause and reset logic (Requirements 6.1, 6.5, 6.6, 7.1, 7.2, 7.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inter_hand_pause_calls_next_hand_when_viewers():
    from app import sio

    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    manager.on_viewer_join("sid1")
    session.showdown_pending = True

    with patch("asyncio.sleep", new_callable=AsyncMock), patch.object(
        session, "next_hand"
    ) as mock_next_hand, patch.object(
        manager, "broadcast_state", new_callable=AsyncMock
    ), patch.object(
        manager, "_dispatch_ai_turn", new_callable=AsyncMock
    ), patch.object(
        sio, "emit", new_callable=AsyncMock
    ):
        await manager._inter_hand_pause()

    mock_next_hand.assert_called_once()


@pytest.mark.asyncio
async def test_inter_hand_pause_pauses_when_no_viewers():
    from app import sio

    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    assert manager.viewer_count == 0
    session.showdown_pending = True

    with patch("asyncio.sleep", new_callable=AsyncMock), patch.object(
        session, "next_hand"
    ) as mock_next_hand, patch.object(sio, "emit", new_callable=AsyncMock):
        await manager._inter_hand_pause()

    mock_next_hand.assert_not_called()
    assert manager.paused is True


@pytest.mark.asyncio
async def test_showdown_dispatch_schedules_one_inter_hand_pause():
    from app import sio

    manager = make_arena_manager()
    session = manager.get_or_create_session()
    session.showdown_pending = True
    manager.paused = False

    with patch.object(
        manager, "_inter_hand_pause", new_callable=AsyncMock
    ) as mock_pause, patch.object(sio, "emit", new_callable=AsyncMock):
        await manager._dispatch_ai_turn(session)

    mock_pause.assert_called_once()
    assert manager._loop_running is False
    assert manager._inter_hand_pause_running is True


@pytest.mark.asyncio
async def test_complete_dispatch_schedules_one_reset():
    from app import sio
    from app.game.models import SessionStatus

    manager = make_arena_manager()
    session = manager.get_or_create_session()
    session.status = SessionStatus.COMPLETE
    manager.paused = False

    with patch.object(
        manager, "_reset_after_complete", new_callable=AsyncMock
    ) as mock_reset, patch.object(sio, "emit", new_callable=AsyncMock):
        await manager._dispatch_ai_turn(session)

    mock_reset.assert_called_once()
    assert manager._loop_running is False
    assert manager._reset_running is True


def test_viewer_join_during_transition_does_not_start_ai_loop():
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        manager.get_or_create_session()
    manager._inter_hand_pause_running = True

    with patch.object(manager, "start_ai_loop") as mock_start:
        manager.on_viewer_join("sid1")

    mock_start.assert_not_called()
    assert manager.paused is False
    assert manager._loop_running is False


@pytest.mark.asyncio
async def test_paused_ai_turn_marks_loop_idle():
    manager = make_arena_manager()
    session = manager.get_or_create_session()
    ai_player = session.players[0]
    manager._loop_running = True
    manager.paused = True

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await manager._run_ai_turn(session, ai_player)

    assert manager._loop_running is False


@pytest.mark.asyncio
async def test_reset_creates_new_session():
    from app import sio

    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        old_session = manager.get_or_create_session()

    manager.on_viewer_join("sid1")

    with patch("asyncio.sleep", new_callable=AsyncMock), patch.object(
        manager, "broadcast_state", new_callable=AsyncMock
    ), patch.object(manager, "start_ai_loop"), patch(
        "app.arena.arena_manager.GameSession.start_game"
    ), patch.object(
        sio, "emit", new_callable=AsyncMock
    ):
        await manager._reset_after_complete()

    assert manager.session is not old_session
    assert manager.session is not None


@pytest.mark.asyncio
async def test_reset_does_not_start_loop_when_no_viewers():
    from app import sio

    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        manager.get_or_create_session()

    assert manager.viewer_count == 0

    with patch("asyncio.sleep", new_callable=AsyncMock), patch.object(
        manager, "broadcast_state", new_callable=AsyncMock
    ), patch.object(manager, "start_ai_loop") as mock_start, patch(
        "app.arena.arena_manager.GameSession.start_game"
    ), patch.object(
        sio, "emit", new_callable=AsyncMock
    ):
        await manager._reset_after_complete()

    mock_start.assert_not_called()
    assert manager.paused is True


# ---------------------------------------------------------------------------
# Elimination tracking (Requirements 3.1)
# ---------------------------------------------------------------------------


def test_track_eliminations_records_eliminated_players():
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    session.players[0].is_eliminated = True
    session.players[1].is_eliminated = True
    manager._track_eliminations()
    assert session.players[0].player_id in manager._elimination_order
    assert session.players[1].player_id in manager._elimination_order
    assert len(manager._elimination_order) == 2


def test_track_eliminations_no_duplicates():
    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    session.players[0].is_eliminated = True
    manager._track_eliminations()
    manager._track_eliminations()
    assert manager._elimination_order.count(session.players[0].player_id) == 1


def test_track_eliminations_preserves_order():
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


@pytest.mark.asyncio
async def test_reset_after_complete_records_game_results():
    from app import sio

    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager.get_or_create_session()

    for p in session.players[:-1]:
        p.is_eliminated = True
        manager._elimination_order.append(p.player_id)

    session.players[0].game_api_calls = 5
    session.players[0].game_api_failures = 1
    session.players[0].game_total_latency_ms = 1000

    mock_service = MagicMock()
    mock_service.available = True
    manager.leaderboard_service = mock_service

    manager.on_viewer_join("sid1")

    with patch("asyncio.sleep", new_callable=AsyncMock), patch.object(
        manager, "broadcast_state", new_callable=AsyncMock
    ), patch.object(manager, "start_ai_loop"), patch(
        "app.arena.arena_manager.GameSession.start_game"
    ), patch.object(
        sio, "emit", new_callable=AsyncMock
    ):
        await manager._reset_after_complete()

    mock_service.record_game_results.assert_called_once()
    results = mock_service.record_game_results.call_args[0][0]
    assert len(results) == len(session.players)

    winner_result = next(r for r in results if r.model_id == session.players[-1].model)
    assert winner_result.placing == 1

    first_elim_result = next(r for r in results if r.model_id == session.players[0].model)
    assert first_elim_result.placing == len(session.players)
    assert first_elim_result.api_calls == 5
    assert first_elim_result.api_failures == 1
    assert first_elim_result.total_latency_ms == 1000


@pytest.mark.asyncio
async def test_reset_after_complete_skips_recording_when_no_service():
    from app import sio

    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        manager.get_or_create_session()

    assert manager.leaderboard_service is None

    with patch("asyncio.sleep", new_callable=AsyncMock), patch.object(
        manager, "broadcast_state", new_callable=AsyncMock
    ), patch("app.arena.arena_manager.GameSession.start_game"), patch.object(
        sio, "emit", new_callable=AsyncMock
    ):
        await manager._reset_after_complete()


@pytest.mark.asyncio
async def test_reset_after_complete_skips_recording_when_service_unavailable():
    from app import sio

    manager = make_arena_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        manager.get_or_create_session()

    mock_service = MagicMock()
    mock_service.available = False
    manager.leaderboard_service = mock_service

    with patch("asyncio.sleep", new_callable=AsyncMock), patch.object(
        manager, "broadcast_state", new_callable=AsyncMock
    ), patch("app.arena.arena_manager.GameSession.start_game"), patch.object(
        sio, "emit", new_callable=AsyncMock
    ):
        await manager._reset_after_complete()

    mock_service.record_game_results.assert_not_called()


def test_create_session_resets_elimination_order():
    manager = make_arena_manager()
    manager._elimination_order = ["old_player_1", "old_player_2"]
    with patch("app.arena.arena_manager.GameSession.start_game"):
        manager._create_session()
    assert manager._elimination_order == []
