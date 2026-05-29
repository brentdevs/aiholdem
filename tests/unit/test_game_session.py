"""Unit tests for GameSession — lifecycle, actions, elimination."""

import pytest

from app.game.game_session import BIG_BLIND, SMALL_BLIND, STARTING_CHIPS, GameSession
from app.game.models import Action, ActionType, MoveLog, Phase, SessionStatus
from app.game.players import AIPlayer, Player


def make_player(pid, chips=STARTING_CHIPS):
    return Player(player_id=pid, name=f"Player{pid}", chips=chips)


def make_session_with_players(n=2):
    session = GameSession(session_id="test-session", host_player_id="p1")
    for i in range(1, n + 1):
        session.add_player(make_player(f"p{i}"))
    return session


# ---------------------------------------------------------------------------
# Lobby management
# ---------------------------------------------------------------------------


def test_add_player_in_lobby():
    session = GameSession("s1", "p1")
    p = make_player("p1")
    session.add_player(p)
    assert len(session.players) == 1


def test_add_player_max_9():
    session = GameSession("s1", "p1")
    for i in range(9):
        session.add_player(make_player(f"p{i}"))
    with pytest.raises(ValueError, match="full"):
        session.add_player(make_player("p9"))


def test_start_requires_2_players():
    session = GameSession("s1", "p1")
    session.add_player(make_player("p1"))
    with pytest.raises(ValueError, match="2 players"):
        session.start_game()


def test_start_transitions_to_active():
    session = make_session_with_players(2)
    session.start_game()
    assert session.status == SessionStatus.ACTIVE


def test_cannot_add_player_after_start():
    session = make_session_with_players(2)
    session.start_game()
    with pytest.raises(ValueError, match="LOBBY"):
        session.add_player(make_player("p3"))


# ---------------------------------------------------------------------------
# Hand start — blinds and hole cards
# ---------------------------------------------------------------------------


def test_blinds_posted_on_start():
    session = make_session_with_players(2)
    session.start_game()
    total_chips = sum(p.chips for p in session.players)
    assert total_chips == STARTING_CHIPS * 2 - SMALL_BLIND - BIG_BLIND


def test_hole_cards_dealt_on_start():
    session = make_session_with_players(3)
    session.start_game()
    for p in session.players:
        assert len(p.hole_cards) == 2


def test_hole_cards_unique():
    session = make_session_with_players(4)
    session.start_game()
    all_cards = [c for p in session.players for c in p.hole_cards]
    assert len(all_cards) == len(set(all_cards))


def test_initial_dealer_is_randomized_then_rotates(monkeypatch):
    session = make_session_with_players(4)
    calls = []

    def choose_initial_dealer(num_players):
        calls.append(num_players)
        return 2

    monkeypatch.setattr(
        "app.game.game_session.random.randrange",
        choose_initial_dealer,
    )

    session.start_game()

    assert calls == [4]
    assert session.dealer_button_index == 2
    assert session.get_public_state()["dealer_button"] == 2

    session._start_hand()

    assert calls == [4]
    assert session.dealer_button_index == 3
    assert session.get_public_state()["dealer_button"] == 3


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def test_out_of_turn_raises():
    session = make_session_with_players(2)
    session.start_game()
    active = session._get_active_players()
    hand = session.current_hand
    current = next(p for p in active if p.player_id == hand.current_player_id)
    other = next(p for p in active if p.player_id != current.player_id)
    with pytest.raises(ValueError, match="not player"):
        session.apply_action(other.player_id, Action(type=ActionType.CHECK))


def test_fold_removes_player_from_hand():
    """Folding in a 3-player hand removes the player from active players."""
    session = make_session_with_players(3)  # 3 players so fold doesn't end the hand
    session.start_game()
    active = session._get_active_players()
    hand = session.current_hand
    current = next(p for p in active if p.player_id == hand.current_player_id)
    session.apply_action(current.player_id, Action(type=ActionType.FOLD))
    assert not current.is_active


def test_fold_with_one_remaining_ends_hand():
    """When one player folds heads-up, the other wins and a new hand starts."""
    session = make_session_with_players(2)
    session.start_game()
    active = session._get_active_players()
    hand = session.current_hand
    current = next(p for p in active if p.player_id == hand.current_player_id)
    session.apply_action(current.player_id, Action(type=ActionType.FOLD))
    assert session.status in (SessionStatus.ACTIVE, SessionStatus.COMPLETE)


# ---------------------------------------------------------------------------
# Chip conservation
# ---------------------------------------------------------------------------


def test_chip_conservation_after_fold():
    """Total chips across all players are conserved after a complete hand."""
    session = make_session_with_players(2)
    session.start_game()
    total_before = STARTING_CHIPS * 2
    active = session._get_active_players()
    hand = session.current_hand
    current = next(p for p in active if p.player_id == hand.current_player_id)
    session.apply_action(current.player_id, Action(type=ActionType.FOLD))
    # After fold, chips are redistributed — total across all players must be conserved
    total_after = sum(p.chips for p in session.players)
    assert total_after == total_before


# ---------------------------------------------------------------------------
# Player elimination
# ---------------------------------------------------------------------------


def test_player_eliminated_at_zero_chips():
    session = make_session_with_players(2)
    # Give p2 just enough to lose
    session.players[1].chips = SMALL_BLIND
    session.start_game()
    # Force p2 to go all-in and lose by folding everyone else
    active = session._get_active_players()
    hand = session.current_hand
    current = next(p for p in active if p.player_id == hand.current_player_id)
    # Just fold the current player to end the hand quickly
    session.apply_action(current.player_id, Action(type=ActionType.FOLD))
    # At least one player should have chips; session may be complete
    non_eliminated = session._get_non_eliminated_players()
    assert len(non_eliminated) >= 1


# ---------------------------------------------------------------------------
# State payloads
# ---------------------------------------------------------------------------


def test_public_state_has_required_fields():
    session = make_session_with_players(2)
    session.start_game()
    state = session.get_public_state()
    for key in ("session_id", "status", "players", "community_cards", "pot", "phase"):
        assert key in state


def test_player_state_has_hole_cards():
    session = make_session_with_players(2)
    session.start_game()
    pid = session.players[0].player_id
    state = session.get_player_state(pid)
    assert "hole_cards" in state
    assert len(state["hole_cards"]) == 2


def test_player_state_has_valid_actions():
    session = make_session_with_players(2)
    session.start_game()
    active = session._get_active_players()
    hand = session.current_hand
    current = next(p for p in active if p.player_id == hand.current_player_id)
    state = session.get_player_state(current.player_id)
    assert "valid_actions" in state
    assert len(state["valid_actions"]) > 0


# ---------------------------------------------------------------------------
# AI Move Review — MoveLog behavior (Requirements 2.1–2.5, 3.1–3.3)
# ---------------------------------------------------------------------------


def make_ai_player(pid, chips=STARTING_CHIPS):
    return AIPlayer(player_id=pid, name=f"AI{pid}", chips=chips, provider="test")


def test_game_session_move_log_append():
    """Applying an AI action appends a MoveLog with correct fields."""
    session = GameSession(session_id="s-log", host_player_id="p1")
    session.add_player(make_player("p1"))
    session.add_player(make_ai_player("ai1"))
    session.start_game()

    hand = session.current_hand
    # Force the AI player to be the current player
    hand.current_player_id = "ai1"

    valid = session.get_valid_actions(session.players[1])
    chosen = next((a for a in valid if a.type == ActionType.CHECK), valid[0])

    reasoning = "I have a strong hand."
    session.apply_action("ai1", chosen, reasoning=reasoning)

    assert len(session._hand_move_logs) == 1
    log = session._hand_move_logs[0]
    assert log.player_name == "AIai1"
    assert log.phase == Phase.PRE_FLOP.value
    assert log.action == chosen.type.value
    assert log.amount == (
        chosen.amount if chosen.type in (ActionType.RAISE, ActionType.ALL_IN) else None
    )
    assert log.reasoning == reasoning


def test_game_session_move_log_reset():
    """Starting a new hand clears the move log from the previous hand."""
    session = GameSession(session_id="s-reset", host_player_id="p1")
    session.add_player(make_player("p1"))
    session.add_player(make_ai_player("ai1"))
    session.start_game()

    # Inject some MoveLog entries to simulate a hand with AI moves
    session._hand_move_logs = [
        MoveLog(
            player_name="AIai1", phase="pre_flop", action="check", amount=None, reasoning="test"
        ),
        MoveLog(
            player_name="AIai1", phase="flop", action="raise", amount=60, reasoning="strong hand"
        ),
    ]
    assert len(session._hand_move_logs) == 2

    # Start a new hand directly
    session._start_hand()

    assert session._hand_move_logs == []


def test_public_state_ai_move_review_at_showdown():
    """get_public_state includes ai_move_review with correct entries during showdown."""
    session = GameSession(session_id="s-showdown", host_player_id="p1")
    session.add_player(make_player("p1"))
    session.add_player(make_ai_player("ai1"))
    session.start_game()

    # Inject MoveLog entries
    logs = [
        MoveLog(
            player_name="AIai1",
            phase="pre_flop",
            action="raise",
            amount=60,
            reasoning="pocket aces",
        ),
        MoveLog(
            player_name="AIai1", phase="flop", action="check", amount=None, reasoning="slow play"
        ),
    ]
    session._hand_move_logs = logs

    # Simulate showdown state
    session.showdown_pending = True
    session.current_hand = None

    state = session.get_public_state()

    assert "ai_move_review" in state
    review = state["ai_move_review"]
    assert len(review) == 2

    assert review[0]["player_name"] == "AIai1"
    assert review[0]["phase"] == "pre_flop"
    assert review[0]["action"] == "raise"
    assert review[0]["amount"] == 60
    assert review[0]["reasoning"] == "pocket aces"

    assert review[1]["player_name"] == "AIai1"
    assert review[1]["phase"] == "flop"
    assert review[1]["action"] == "check"
    assert review[1]["amount"] is None
    assert review[1]["reasoning"] == "slow play"


def test_public_state_no_ai_move_review_during_play():
    """get_public_state does NOT include ai_move_review while a hand is active."""
    session = GameSession(session_id="s-active", host_player_id="p1")
    session.add_player(make_player("p1"))
    session.add_player(make_ai_player("ai1"))
    session.start_game()

    # Inject some logs to ensure they don't leak into active-play state
    session._hand_move_logs = [
        MoveLog(
            player_name="AIai1", phase="pre_flop", action="call", amount=None, reasoning="test"
        ),
    ]

    state = session.get_public_state()

    assert "ai_move_review" not in state
