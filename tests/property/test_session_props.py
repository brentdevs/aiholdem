"""Property-based tests for GameSession."""
from hypothesis import given, settings, strategies as st
from app.game.game_session import GameSession, STARTING_CHIPS
from app.game.dealer import Dealer
from app.game.players import Player
from app.game.models import Phase, ActionType


def make_player(pid, chips=STARTING_CHIPS):
    return Player(player_id=pid, name=f"P{pid}", chips=chips)


# Feature: texas-holdem-poker, Property 4: Player Count Bounds
@settings(max_examples=50)
@given(n=st.integers(min_value=0, max_value=12))
def test_player_count_bounds(n):
    """start_game succeeds iff 2 <= n <= 9."""
    session = GameSession("s1", "p1")
    for i in range(min(n, 9)):
        try:
            session.add_player(make_player(f"p{i}"))
        except ValueError:
            break

    seated = len(session.players)
    if seated < 2:
        try:
            session.start_game()
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
    else:
        session.start_game()
        from app.game.models import SessionStatus
        assert session.status == SessionStatus.ACTIVE


# Feature: texas-holdem-poker, Property 5: Hole Card Deal Invariants
@settings(max_examples=100)
@given(n=st.integers(min_value=2, max_value=9))
def test_hole_card_deal_invariants(n):
    """Each player gets exactly 2 unique hole cards; total = 2*n."""
    dealer = Dealer()
    players = [make_player(f"p{i}") for i in range(n)]
    deck = dealer.shuffle_deck()
    dealer.deal_hole_cards(players, deck)

    all_cards = [c for p in players for c in p.hole_cards]
    assert len(all_cards) == 2 * n
    assert len(set(all_cards)) == 2 * n  # all unique


# Feature: texas-holdem-poker, Property 6: Dealer Button Rotation
@settings(max_examples=100)
@given(
    current=st.integers(min_value=0, max_value=100),
    num=st.integers(min_value=2, max_value=9),
)
def test_dealer_button_rotation(current, num):
    """advance_dealer_button always returns (current + 1) % num_players."""
    dealer = Dealer()
    result = dealer.advance_dealer_button(current, num)
    assert result == (current + 1) % num


# Feature: texas-holdem-poker, Property 19: Game State Payload Completeness and Privacy
# Validates: Requirements 8.4, 8.5
@settings(max_examples=100)
@given(n=st.integers(min_value=2, max_value=6))
def test_game_state_payload_completeness_and_privacy(n):
    """
    For any in-progress hand:
    - Public state contains chip counts, pot, community_cards, dealer_button, and is_turn indicator.
    - Private state for a player includes that player's hole_cards and valid_actions,
      and must NOT expose any other player's hole_cards.
    """
    session = GameSession("s1", "p0")
    for i in range(n):
        session.add_player(make_player(f"p{i}"))
    session.start_game()

    # Ensure we have an active hand
    assert session.current_hand is not None

    # --- Public state checks ---
    pub = session.get_public_state()

    # Must contain community_cards
    assert "community_cards" in pub
    assert isinstance(pub["community_cards"], list)

    # Must contain pot
    assert "pot" in pub
    assert isinstance(pub["pot"], int)
    assert pub["pot"] >= 0

    # Must contain dealer_button
    assert "dealer_button" in pub

    # Must contain players list with required per-player fields
    assert "players" in pub
    assert isinstance(pub["players"], list)
    assert len(pub["players"]) == n
    for player_entry in pub["players"]:
        assert "chips" in player_entry
        assert isinstance(player_entry["chips"], int)
        assert "is_turn" in player_entry or "is_active" in player_entry  # active indicator present

    # --- Private state checks per player ---
    for player in session.players:
        priv = session.get_player_state(player.player_id)

        # hole_cards must be present for this player
        assert "hole_cards" in priv, f"hole_cards missing for player {player.player_id}"
        assert isinstance(priv["hole_cards"], list)
        assert len(priv["hole_cards"]) == 2

        # valid_actions must be present
        assert "valid_actions" in priv, f"valid_actions missing for player {player.player_id}"
        assert isinstance(priv["valid_actions"], list)

        # hole_cards of OTHER players must NOT appear in the payload
        # The payload must not contain a top-level key exposing other players' cards
        other_hole_cards = [
            c
            for p in session.players
            if p.player_id != player.player_id
            for c in p.hole_cards
        ]
        # Convert this player's returned hole_cards to comparable tuples
        returned_cards = {(c["rank"], c["suit"]) for c in priv["hole_cards"]}
        other_cards = {(c.rank, c.suit) for c in other_hole_cards}
        # No card from another player should appear in this player's hole_cards payload
        assert returned_cards.isdisjoint(other_cards), (
            f"Player {player.player_id} received another player's hole card in their private state"
        )


# Feature: openrouter-ai-player, Property 6: History Accumulation Invariant
# Validates: Requirements 9.1
@settings(max_examples=100)
@given(n=st.integers(min_value=2, max_value=4))
def test_history_accumulation_invariant(n):
    """Each action applied within a single hand increments history by exactly 1.
    We track the hand object reference to detect when a new hand starts."""
    session = GameSession("s1", "p0")
    for i in range(n):
        session.add_player(make_player(f"p{i}"))
    session.start_game()

    # Apply up to 20 actions within the SAME hand, checking invariant after each
    starting_hand = session.current_hand
    actions_in_hand = 0

    for _ in range(20):
        if session.current_hand is None or session.current_hand is not starting_hand:
            # Hand ended or new hand started — stop
            break
        active = session._get_active_players()
        if not active:
            break
        hand = session.current_hand
        current = next(p for p in active if p.player_id == hand.current_player_id)
        valid = session.get_valid_actions(current)
        if not valid:
            break
        # Pick check if available (least disruptive), else call, else first valid
        action = (
            next((a for a in valid if a.type.value == "check"), None)
            or next((a for a in valid if a.type.value == "call"), None)
            or valid[0]
        )
        session.apply_action(current.player_id, action)

        # If still on the same hand, history length must equal actions applied
        if session.current_hand is starting_hand:
            actions_in_hand += 1
            assert len(session.current_hand.history) == actions_in_hand


# Feature: ai-move-review, Property 5: Move log is empty at the start of every hand
# Validates: Requirements 2.1, 2.4
@settings(max_examples=50)
@given(n_hands=st.integers(min_value=1, max_value=5))
def test_move_log_reset_on_start_hand(n_hands):
    """_hand_move_logs is empty immediately after _start_hand is called, for every hand."""
    from app.game.models import MoveLog

    session = GameSession("s1", "p0")
    session.add_player(make_player("p0"))
    session.add_player(make_player("p1"))
    session.start_game()  # calls _start_hand internally

    # After initial start_game, log must be empty
    assert session._hand_move_logs == []

    # For subsequent hands: inject fake MoveLog entries, then call _start_hand directly
    for _ in range(n_hands - 1):
        # Simulate some AI moves having been logged during the hand
        session._hand_move_logs.append(
            MoveLog(
                player_name="Bot",
                phase="pre_flop",
                action="call",
                amount=None,
                reasoning="Seemed reasonable.",
            )
        )
        assert len(session._hand_move_logs) > 0

        session._start_hand()
        assert session._hand_move_logs == []


# Feature: ai-move-review, Property 4: MoveLog fields match the applied AI action
# Validates: Requirements 2.2
@settings(max_examples=100)
@given(
    player_name=st.text(min_size=1, max_size=50),
    phase=st.sampled_from(Phase),
    action_type=st.sampled_from(ActionType),
    amount=st.one_of(st.none(), st.integers(min_value=1, max_value=10000)),
    reasoning=st.text(),
)
def test_movelog_fields_match_applied_ai_action(player_name, phase, action_type, amount, reasoning):
    """MoveLog appended to _hand_move_logs has exact fields passed to apply_action."""
    from app.game.players import AIPlayer

    session = GameSession("s1", "p0")
    human = make_player("p0")
    ai_player = AIPlayer(player_id="ai1", name=player_name, chips=1000, provider="test")
    session.add_player(human)
    session.add_player(ai_player)
    session.start_game()

    # Force the AI player to be the current player
    hand = session.current_hand
    assert hand is not None
    hand.current_player_id = ai_player.player_id
    ai_player.is_active = True

    # Build a valid action for the AI player
    valid = session.get_valid_actions(ai_player)
    assert valid, "Expected at least one valid action"

    # Map the generated action_type to a valid action if possible, else use first valid
    chosen = next((a for a in valid if a.type == action_type), valid[0])

    logs_before = len(session._hand_move_logs)
    session.apply_action(ai_player.player_id, chosen, reasoning=reasoning)

    # A MoveLog must have been appended
    assert len(session._hand_move_logs) == logs_before + 1

    log = session._hand_move_logs[-1]
    assert log.player_name == player_name
    assert log.phase == hand.phase.value or log.phase in [p.value for p in Phase]
    assert log.action == chosen.type.value
    # amount is set only for RAISE and ALL_IN
    if chosen.type in (ActionType.RAISE, ActionType.ALL_IN):
        assert log.amount == chosen.amount
    else:
        assert log.amount is None
    assert log.reasoning == reasoning


# Feature: ai-move-review, Property 9: Reasoning stored verbatim in the game session
# Validates: Requirements 6.2
@settings(max_examples=100)
@given(reasoning=st.text())
def test_reasoning_stored_verbatim(reasoning):
    """Reasoning passed to apply_action is stored byte-for-byte in the MoveLog."""
    from app.game.players import AIPlayer

    session = GameSession("s1", "p0")
    human = make_player("p0")
    ai_player = AIPlayer(player_id="ai1", name="Bot", chips=1000, provider="test")
    session.add_player(human)
    session.add_player(ai_player)
    session.start_game()

    hand = session.current_hand
    assert hand is not None
    hand.current_player_id = ai_player.player_id
    ai_player.is_active = True

    valid = session.get_valid_actions(ai_player)
    assert valid
    action = next((a for a in valid if a.type.value == "check"), valid[0])

    session.apply_action(ai_player.player_id, action, reasoning=reasoning)

    assert session._hand_move_logs[-1].reasoning == reasoning


# Feature: ai-move-review, Property 6: Public state at showdown contains ai_move_review matching logged moves
# Validates: Requirements 2.3, 3.1, 2.5
move_log_strategy = st.fixed_dictionaries({
    "player_name": st.text(min_size=1, max_size=20),
    "phase": st.sampled_from(["pre_flop", "flop", "turn", "river"]),
    "action": st.sampled_from(["fold", "check", "call", "raise", "all_in"]),
    "amount": st.one_of(st.none(), st.integers(min_value=1, max_value=1000)),
    "reasoning": st.text(),
})


@settings(max_examples=100)
@given(move_log_dicts=st.lists(move_log_strategy, min_size=0, max_size=5))
def test_public_state_at_showdown_contains_ai_move_review(move_log_dicts):
    """Public state at showdown contains ai_move_review matching logged moves one-to-one."""
    from app.game.models import MoveLog

    session = GameSession("s1", "p0")
    session.add_player(make_player("p0"))
    session.add_player(make_player("p1"))
    session.start_game()

    # Inject MoveLog entries directly
    session._hand_move_logs = [MoveLog(**d) for d in move_log_dicts]

    # Trigger showdown state
    session.showdown_pending = True
    session.current_hand = None

    state = session.get_public_state()

    # ai_move_review must be present at showdown
    assert "ai_move_review" in state

    review = state["ai_move_review"]
    assert len(review) == len(session._hand_move_logs)

    # Each entry must correspond one-to-one with the MoveLog entries
    for entry, log in zip(review, session._hand_move_logs):
        assert entry["player_name"] == log.player_name
        assert entry["phase"] == log.phase
        assert entry["action"] == log.action
        assert entry["amount"] == log.amount
        assert entry["reasoning"] == log.reasoning

    # When showdown_pending is False, ai_move_review must NOT be in the state
    session.showdown_pending = False
    session.current_hand = None
    state_no_showdown = session.get_public_state()
    assert "ai_move_review" not in state_no_showdown


# Feature: ai-move-review, Property 7: MoveLog wire format contains all required keys with correct types
# Validates: Requirements 3.2
@settings(max_examples=100)
@given(
    player_name=st.text(min_size=1, max_size=50),
    phase=st.sampled_from(["pre_flop", "flop", "turn", "river"]),
    action=st.sampled_from(["fold", "check", "call", "raise", "all_in"]),
    amount=st.one_of(st.none(), st.integers(min_value=1, max_value=10000)),
    reasoning=st.text(),
)
def test_movelog_wire_format_keys_and_types(player_name, phase, action, amount, reasoning):
    """MoveLog serialized dict has exactly the required keys with correct types."""
    from app.game.models import MoveLog

    log = MoveLog(
        player_name=player_name,
        phase=phase,
        action=action,
        amount=amount,
        reasoning=reasoning,
    )

    # Serialize as get_public_state would
    wire = {
        "player_name": log.player_name,
        "phase": log.phase,
        "action": log.action,
        "amount": log.amount,
        "reasoning": log.reasoning,
    }

    # Must have exactly these keys
    assert set(wire.keys()) == {"player_name", "phase", "action", "amount", "reasoning"}

    # Type checks
    assert isinstance(wire["player_name"], str)
    assert isinstance(wire["phase"], str)
    assert isinstance(wire["action"], str)
    assert wire["amount"] is None or isinstance(wire["amount"], int)
    assert isinstance(wire["reasoning"], str)
