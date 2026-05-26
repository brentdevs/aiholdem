"""Property-based tests for ArenaManager.

Requirements: 3.1, 3.4, 4.3, 4.4, 8.4, 9.3
"""
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume, strategies as st

from app.arena.arena_manager import ArenaManager, ARENA_PLAYER_MODELS, ARENA_SESSION_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_manager() -> ArenaManager:
    """Return a fresh ArenaManager with start_game mocked to avoid real game logic."""
    return ArenaManager()


# ---------------------------------------------------------------------------
# Property 2: Arena session singleton invariant
# Validates: Requirements 3.1, 3.4
# ---------------------------------------------------------------------------

# Feature: ai-spectator-arena, Property 2: Arena session singleton invariant
@settings(max_examples=100)
@given(n=st.integers(min_value=1, max_value=20))
def test_singleton_invariant(n):
    """For any N calls to get_or_create_session, the same session is returned
    and session_id == ARENA_SESSION_ID.

    **Validates: Requirements 3.1, 3.4**
    """
    manager = make_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        sessions = [manager.get_or_create_session() for _ in range(n)]

    # All calls return the same object
    first = sessions[0]
    for s in sessions[1:]:
        assert s is first, "get_or_create_session must return the same session object"

    # Session ID is always ARENA_SESSION_ID
    assert first.session_id == ARENA_SESSION_ID


# ---------------------------------------------------------------------------
# Property 3: Arena players match ARENA_PLAYER_MODELS
# Validates: Requirements 4.3, 4.4
# ---------------------------------------------------------------------------

# Feature: ai-spectator-arena, Property 3: Arena players match ARENA_PLAYER_MODELS
@settings(max_examples=50)
@given(reset_count=st.integers(min_value=1, max_value=5))
def test_arena_players_match_models(reset_count):
    """For any number of _create_session calls, the last session's players
    match ARENA_PLAYER_MODELS exactly (count and model order).

    **Validates: Requirements 4.3, 4.4**
    """
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
    """For any reset count, every player in the arena session has a 'provider'
    attribute (i.e. is an OpenRouterPlayer / AIPlayer).

    **Validates: Requirements 4.4**
    """
    manager = make_manager()
    with patch("app.arena.arena_manager.GameSession.start_game"):
        # Always create at least one session
        session = manager._create_session()
        for _ in range(reset_count):
            session = manager._create_session()

    for player in session.players:
        assert hasattr(player, "provider"), (
            f"Player {player.player_id} is missing 'provider' — must be an AI player"
        )


# ---------------------------------------------------------------------------
# Property 8: Viewer count tracks joins and leaves accurately
# Validates: Requirements 8.4
# ---------------------------------------------------------------------------

# Feature: ai-spectator-arena, Property 8: Viewer count tracks joins and leaves accurately
@settings(max_examples=100)
@given(
    join_sids=st.lists(
        st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
        min_size=1,
        max_size=10,
        unique=True,
    ),
    leave_count=st.integers(min_value=0, max_value=10),
)
def test_viewer_count_accuracy(join_sids, leave_count):
    """For any N joins from distinct socket IDs followed by M leaves (M ≤ N),
    viewer_count == N - M.

    **Validates: Requirements 8.4**
    """
    assume(leave_count <= len(join_sids))

    manager = make_manager()

    # Join all sids
    for sid in join_sids:
        manager.on_viewer_join(sid)

    assert manager.viewer_count == len(join_sids)

    # Leave the first leave_count sids
    for sid in join_sids[:leave_count]:
        manager.on_viewer_leave(sid)

    expected = len(join_sids) - leave_count
    assert manager.viewer_count == expected, (
        f"Expected viewer_count={expected} after {len(join_sids)} joins and "
        f"{leave_count} leaves, got {manager.viewer_count}"
    )


# ---------------------------------------------------------------------------
# Property 11: arena_viewer_count emitted on every count change
# Validates: Requirements 9.3
# ---------------------------------------------------------------------------

# Feature: ai-spectator-arena, Property 11: arena_viewer_count emitted on every count change
@settings(max_examples=100)
@given(
    join_sids=st.lists(
        st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
        min_size=1,
        max_size=5,
        unique=True,
    ),
    leave_count=st.integers(min_value=0, max_value=5),
)
def test_viewer_count_event_emitted(join_sids, leave_count):
    """For any join/leave sequence, arena_viewer_count is emitted each time
    broadcast_viewer_count is called (once per join and once per leave).

    **Validates: Requirements 9.3**
    """
    assume(leave_count <= len(join_sids))

    manager = make_manager()
    emit_calls = []

    mock_socketio = MagicMock()
    mock_socketio.emit.side_effect = lambda event, data, **kwargs: emit_calls.append((event, data))

    with patch("app.socketio", mock_socketio):
        # Simulate join sequence — call broadcast_viewer_count after each join
        for sid in join_sids:
            manager.on_viewer_join(sid)
            manager.broadcast_viewer_count()

        # Simulate leave sequence — call broadcast_viewer_count after each leave
        for sid in join_sids[:leave_count]:
            manager.on_viewer_leave(sid)
            manager.broadcast_viewer_count()

    total_broadcasts = len(join_sids) + leave_count
    arena_viewer_count_calls = [c for c in emit_calls if c[0] == "arena_viewer_count"]

    assert len(arena_viewer_count_calls) == total_broadcasts, (
        f"Expected {total_broadcasts} arena_viewer_count emissions, "
        f"got {len(arena_viewer_count_calls)}"
    )

    # The last emitted count must match the final viewer_count
    if arena_viewer_count_calls:
        last_count = arena_viewer_count_calls[-1][1]["count"]
        assert last_count == manager.viewer_count, (
            f"Last emitted count {last_count} != manager.viewer_count {manager.viewer_count}"
        )


# ---------------------------------------------------------------------------
# Property 1: Spectator join emits current state
# Validates: Requirements 2.3
# ---------------------------------------------------------------------------

# App + socketio created once outside the hypothesis loop to avoid re-init issues.
from app import socketio as _socketio_client
from tests.shared_app import _app as _prop1_app


# Feature: ai-spectator-arena, Property 1: Spectator join emits current state
@settings(max_examples=50)
@given(viewer_count=st.integers(min_value=0, max_value=10))
def test_spectator_join_emits_state(viewer_count):
    """For any arena state, join_arena emits matching arena_state to the joining socket.

    **Validates: Requirements 2.3**
    """
    expected_state = {
        "session_id": "arena",
        "status": "active",
        "players": [],
        "community_cards": [],
        "pot": viewer_count,  # use viewer_count as a distinguishing value
        "phase": "pre_flop",
    }

    mock_session = MagicMock()
    mock_session.get_public_state.return_value = expected_state

    with patch("app.events.arena_manager") as mock_manager:
        mock_manager.get_or_create_session.return_value = mock_session
        mock_manager.get_arena_state.return_value = expected_state
        mock_manager.on_viewer_join.return_value = None
        mock_manager.broadcast_viewer_count.return_value = None

        client = _socketio_client.test_client(_prop1_app)
        try:
            client.get_received()  # clear startup events
            client.emit("join_arena", {})
            received = client.get_received()
        finally:
            client.disconnect()

    arena_state_events = [e for e in received if e["name"] == "arena_state"]
    assert len(arena_state_events) >= 1, (
        f"Expected at least one arena_state event, got: {[e['name'] for e in received]}"
    )

    payload = arena_state_events[0]["args"][0]
    assert payload == expected_state, (
        f"arena_state payload {payload!r} does not match expected {expected_state!r}"
    )


# ---------------------------------------------------------------------------
# Property 10: arena_state emitted on every state change
# Validates: Requirements 9.2
# ---------------------------------------------------------------------------

# Feature: ai-spectator-arena, Property 10: arena_state emitted on every state change
@settings(max_examples=100)
@given(action_count=st.integers(min_value=1, max_value=5))
def test_arena_state_emitted_on_action(action_count):
    """For any N calls to broadcast_state(), arena_state is emitted N times.

    **Validates: Requirements 9.2**
    """
    manager = make_manager()

    # Give the manager a mock session so broadcast_state() doesn't bail early
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
    mock_socketio = MagicMock()
    mock_socketio.emit.side_effect = lambda event, data, **kwargs: emit_calls.append(event)

    with patch("app.socketio", mock_socketio):
        for _ in range(action_count):
            manager.broadcast_state()

    arena_state_calls = [e for e in emit_calls if e == "arena_state"]
    assert len(arena_state_calls) == action_count, (
        f"Expected {action_count} arena_state emissions, got {len(arena_state_calls)}"
    )
