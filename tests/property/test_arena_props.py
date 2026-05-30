"""Property-based tests for ArenaManager.

Requirements: 3.1, 3.4, 4.3, 4.4, 8.4, 9.3
"""

from unittest.mock import AsyncMock, MagicMock, patch

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.arena.arena_manager import ARENA_PLAYER_MODELS, ARENA_SESSION_ID, ArenaManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_manager() -> ArenaManager:
    return ArenaManager()


# ---------------------------------------------------------------------------
# Property 2: Arena session singleton invariant
# Validates: Requirements 3.1, 3.4
# ---------------------------------------------------------------------------


# Feature: ai-spectator-arena, Property 2: Arena session singleton invariant
@settings(max_examples=100)
@given(n=st.integers(min_value=1, max_value=20))
def test_singleton_invariant(n):
    manager = make_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        sessions = [manager.get_or_create_session() for _ in range(n)]

    first = sessions[0]
    for s in sessions[1:]:
        assert s is first
    assert first.session_id == ARENA_SESSION_ID


# ---------------------------------------------------------------------------
# Property 3: Arena players match ARENA_PLAYER_MODELS
# Validates: Requirements 4.3, 4.4
# ---------------------------------------------------------------------------


# Feature: ai-spectator-arena, Property 3: Arena players match ARENA_PLAYER_MODELS
@settings(max_examples=50)
@given(reset_count=st.integers(min_value=1, max_value=5))
def test_arena_players_match_models(reset_count):
    manager = make_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = None
        for _ in range(reset_count):
            session = manager._create_session()

    assert session is not None
    assert len(session.players) == len(ARENA_PLAYER_MODELS)
    player_models = [p.model for p in session.players]
    assert player_models == ARENA_PLAYER_MODELS


# ---------------------------------------------------------------------------
# Property 4: All arena players are AI players
# Validates: Requirements 4.4
# ---------------------------------------------------------------------------


# Feature: ai-spectator-arena, Property 4: All arena players are AI players
@settings(max_examples=50)
@given(reset_count=st.integers(min_value=0, max_value=3))
def test_all_players_are_ai(reset_count):
    manager = make_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        session = manager._create_session()
        for _ in range(reset_count):
            session = manager._create_session()

    for player in session.players:
        assert hasattr(player, "provider")


# ---------------------------------------------------------------------------
# Property 8: Viewer count tracks joins and leaves accurately
# Validates: Requirements 8.4
# ---------------------------------------------------------------------------


# Feature: ai-spectator-arena, Property 8: Viewer count tracks joins and leaves accurately
@settings(max_examples=100)
@given(
    join_sids=st.lists(
        st.text(
            min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))
        ),
        min_size=1,
        max_size=10,
        unique=True,
    ),
    leave_count=st.integers(min_value=0, max_value=10),
)
def test_viewer_count_accuracy(join_sids, leave_count):
    assume(leave_count <= len(join_sids))

    manager = make_manager()
    for sid in join_sids:
        manager.on_viewer_join(sid)

    assert manager.viewer_count == len(join_sids)

    for sid in join_sids[:leave_count]:
        manager.on_viewer_leave(sid)

    expected = len(join_sids) - leave_count
    assert manager.viewer_count == expected


# ---------------------------------------------------------------------------
# Property 10: arena_state emitted on every state change
# Validates: Requirements 9.2
# ---------------------------------------------------------------------------


# Feature: ai-spectator-arena, Property 10: arena_state emitted on every state change
@settings(max_examples=100)
@given(action_count=st.integers(min_value=1, max_value=5))
def test_arena_state_emitted_on_action(action_count):
    import asyncio

    from app import sio

    manager = make_manager()

    mock_session = MagicMock()
    mock_session.get_public_state.return_value = {
        "session_id": "arena",
        "status": "active",
        "players": [],
        "community_cards": [],
        "pot": 0,
        "phase": "pre_flop",
    }
    mock_session._hand_move_logs = []
    manager.session = mock_session

    emit_calls = []

    async def _run():
        with patch.object(sio, "emit", new_callable=AsyncMock) as mock_emit:
            mock_emit.side_effect = lambda event, data, **kwargs: emit_calls.append(event)
            for _ in range(action_count):
                await manager.broadcast_state()

    asyncio.run(_run())

    arena_state_calls = [e for e in emit_calls if e == "arena_state"]
    assert len(arena_state_calls) == action_count
