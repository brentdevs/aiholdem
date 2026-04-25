"""Property-based tests for AI Player Profiling stat flag computation."""
from __future__ import annotations

from hypothesis import given, settings, assume, strategies as st
from hypothesis.strategies import composite

from app.profiling.hand_recorder import compute_stat_flags, PlayerFlags

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHASES = ["pre-flop", "flop", "turn", "river"]
ACTION_CODES = ["F", "X", "C", "R", "A"]
ALL_POSITIONS = ["BTN", "SB", "BB", "UTG", "UTG+1", "MP", "MP+1", "HJ", "CO"]
STEAL_POSITIONS = {"CO", "BTN", "SB", "SB/BTN"}


# ---------------------------------------------------------------------------
# Custom Hypothesis strategy: hand_history_strategy
# ---------------------------------------------------------------------------


@composite
def hand_history_strategy(draw):
    """Generate a realistic poker hand action sequence with player names,
    position assignments, and BB player.

    Returns:
        tuple of (actions, player_names, positions_map, bb_player)
        - actions: list of (phase, player_name, action_code, amount) tuples
        - player_names: list of player name strings
        - positions_map: dict mapping player_name -> position string
        - bb_player: name of the big blind player
    """
    num_players = draw(st.integers(min_value=2, max_value=9))
    player_names = [f"Player{i}" for i in range(num_players)]

    # Assign positions based on number of players (mirroring game_session logic)
    if num_players == 2:
        # Heads-up: dealer is SB/BTN, other is BB
        positions_map = {player_names[0]: "SB/BTN", player_names[1]: "BB"}
    else:
        pos_labels = ["BTN", "SB", "BB"] + ["UTG", "UTG+1", "MP", "MP+1", "HJ", "CO"]
        positions_map = {}
        for i, name in enumerate(player_names):
            if i < len(pos_labels):
                positions_map[name] = pos_labels[i]
            else:
                positions_map[name] = f"Seat{i}"

    bb_player = [n for n, p in positions_map.items() if p == "BB"][0]

    # Build a realistic action sequence phase by phase
    actions: list[tuple[str, str, str, int | None]] = []
    alive = set(player_names)  # players still in the hand (not folded)
    all_in_players: set[str] = set()  # players who went all-in

    for phase in PHASES:
        if len(alive - all_in_players) < 2 and len(all_in_players) == 0:
            break  # hand over, everyone folded except one
        if len(alive) < 2:
            break

        # Determine acting order for this phase
        if phase == "pre-flop":
            # Pre-flop: action starts after BB, wraps around
            # Order: UTG... -> BTN -> SB -> BB
            acting_order = _preflop_order(player_names, positions_map, alive)
        else:
            # Post-flop: SB first, then clockwise
            acting_order = _postflop_order(player_names, positions_map, alive)

        # Remove all-in players from acting order (they can't act)
        acting_order = [p for p in acting_order if p not in all_in_players]

        if len(acting_order) == 0:
            continue

        raise_count = 0
        max_raises = draw(st.integers(min_value=1, max_value=4))
        players_acted_this_round: set[str] = set()
        last_raiser: str | None = None

        # Each player acts once, unless there's a raise (then players after
        # the raiser need to act again). We simplify by doing one pass.
        i = 0
        iterations = 0
        max_iterations = len(acting_order) * 5  # safety cap
        while i < len(acting_order) and iterations < max_iterations:
            iterations += 1
            pname = acting_order[i]

            if pname not in alive or pname in all_in_players:
                i += 1
                continue

            # Determine valid actions for this player
            available = _available_actions(
                phase, pname, positions_map, raise_count, max_raises,
                players_acted_this_round, last_raiser, bb_player
            )

            action_code = draw(st.sampled_from(available))
            amount: int | None = None

            if action_code == "R":
                amount = draw(st.integers(min_value=10, max_value=500))
                raise_count += 1
                last_raiser = pname
            elif action_code == "A":
                amount = draw(st.integers(min_value=10, max_value=1000))
                raise_count += 1
                last_raiser = pname
                all_in_players.add(pname)
            elif action_code == "C":
                amount = draw(st.integers(min_value=10, max_value=500))

            actions.append((phase, pname, action_code, amount))
            players_acted_this_round.add(pname)

            if action_code == "F":
                alive.discard(pname)
                if len(alive) < 2:
                    break

            i += 1

            # If someone raised and we've gone through everyone, wrap around
            # so players before the raiser can respond (simplified: just extend)
            if (action_code in ("R", "A") and
                    i >= len(acting_order) and
                    raise_count <= max_raises):
                # Add remaining alive non-all-in players who haven't acted
                # since the last raise
                extra = [p for p in acting_order
                         if p in alive and p not in all_in_players
                         and p != pname]
                acting_order.extend(extra)

        if len(alive) < 2:
            break

    return actions, player_names, positions_map, bb_player


def _preflop_order(
    player_names: list[str],
    positions_map: dict[str, str],
    alive: set[str],
) -> list[str]:
    """Return pre-flop acting order: UTG first, BB last."""
    # Position priority for pre-flop (UTG acts first, BB last)
    preflop_priority = [
        "UTG", "UTG+1", "MP", "MP+1", "HJ", "CO", "BTN", "SB", "SB/BTN", "BB"
    ]

    def sort_key(name: str) -> int:
        pos = positions_map.get(name, "")
        if pos in preflop_priority:
            return preflop_priority.index(pos)
        return 100  # unknown positions go last

    return sorted([p for p in player_names if p in alive], key=sort_key)


def _postflop_order(
    player_names: list[str],
    positions_map: dict[str, str],
    alive: set[str],
) -> list[str]:
    """Return post-flop acting order: SB first, BTN last."""
    postflop_priority = [
        "SB", "SB/BTN", "BB", "UTG", "UTG+1", "MP", "MP+1", "HJ", "CO", "BTN"
    ]

    def sort_key(name: str) -> int:
        pos = positions_map.get(name, "")
        if pos in postflop_priority:
            return postflop_priority.index(pos)
        return 100

    return sorted([p for p in player_names if p in alive], key=sort_key)


def _available_actions(
    phase: str,
    pname: str,
    positions_map: dict[str, str],
    raise_count: int,
    max_raises: int,
    players_acted: set[str],
    last_raiser: str | None,
    bb_player: str,
) -> list[str]:
    """Return list of valid action codes for a player in the current context."""
    can_raise = raise_count < max_raises

    if phase == "pre-flop":
        if pname == bb_player and last_raiser is None and pname not in players_acted:
            # BB's first action with no raise: can check or raise
            options = ["X"]
            if can_raise:
                options.extend(["R", "A"])
            return options

        if last_raiser is not None and pname != last_raiser:
            # Facing a raise: fold, call, or re-raise
            options = ["F", "C"]
            if can_raise:
                options.extend(["R", "A"])
            return options

        # Default pre-flop: fold, call, or raise
        options = ["F", "C"]
        if can_raise:
            options.extend(["R", "A"])
        return options
    else:
        # Post-flop
        if last_raiser is not None and pname != last_raiser:
            # Facing a bet: fold, call, or raise
            options = ["F", "C"]
            if can_raise:
                options.extend(["R", "A"])
            return options

        # No bet yet: check or bet
        options = ["X"]
        if can_raise:
            options.extend(["R", "A"])
        return options


# ---------------------------------------------------------------------------
# Reference oracle: independently compute expected flags
# ---------------------------------------------------------------------------


def _oracle_compute_flags(
    actions: list[tuple[str, str, str, int | None]],
    player_name: str,
    position: str,
    bb_player_name: str,
) -> dict:
    """Reference oracle that walks the action sequence to compute expected
    stat flags independently from the production code.

    Returns a dict with keys: vpip, pfr, three_bet, cbet, is_steal_attempt.
    """
    # --- VPIP ---
    # True iff the player voluntarily put chips in pre-flop (R, C, or A).
    # BB posting is not voluntary. BB who just checks is NOT vpip.
    # BB who raises IS vpip.
    vpip = False
    for phase, pname, action, _amt in actions:
        if phase != "pre-flop":
            break
        if pname == player_name and action in ("R", "C", "A"):
            vpip = True
            break

    # --- PFR ---
    # True iff the player raised (R or A) during pre-flop
    pfr = False
    for phase, pname, action, _amt in actions:
        if phase != "pre-flop":
            break
        if pname == player_name and action in ("R", "A"):
            pfr = True
            break

    # --- Three-bet ---
    # True iff the player re-raised after a prior pre-flop raise.
    # None if no prior raise existed before the player's first pre-flop action.
    preflop_raise_count = 0
    saw_raise_before_first_action = False
    player_has_acted = False
    three_bet: bool | None = None

    for phase, pname, action, _amt in actions:
        if phase != "pre-flop":
            break

        is_target = pname == player_name

        if is_target and not player_has_acted:
            saw_raise_before_first_action = preflop_raise_count > 0
            player_has_acted = True

        if action in ("R", "A"):
            preflop_raise_count += 1
            if is_target:
                if preflop_raise_count >= 2:
                    three_bet = True
                else:
                    three_bet = False

    if not player_has_acted:
        three_bet = None
    elif not saw_raise_before_first_action:
        three_bet = None
    elif saw_raise_before_first_action and three_bet is None:
        # Player had opportunity but didn't raise
        three_bet = False

    # --- CBet ---
    # True iff the pre-flop raiser bet on the flop.
    # None if player wasn't PFR or hand ended before flop.
    preflop_raiser: str | None = None
    for phase, pname, action, _amt in actions:
        if phase != "pre-flop":
            break
        if action in ("R", "A"):
            preflop_raiser = pname

    flop_actions = [(ph, pn, ac, am) for ph, pn, ac, am in actions if ph == "flop"]

    if not flop_actions:
        cbet = None
    elif preflop_raiser != player_name:
        cbet = None
    else:
        # Player was PFR — check their first flop action
        cbet = False
        for _ph, pn, ac, _am in flop_actions:
            if pn == player_name:
                if ac in ("R", "A"):
                    cbet = True
                break

    # --- Steal attempt ---
    # True iff player raised from CO/BTN/SB with no prior callers or raisers
    is_steal_attempt = False
    steal_positions = {"CO", "BTN", "SB", "SB/BTN"}
    if position in steal_positions:
        any_prior_voluntary = False
        for phase, pname, action, _amt in actions:
            if phase != "pre-flop":
                break
            if pname == player_name:
                if action in ("R", "A") and not any_prior_voluntary:
                    is_steal_attempt = True
                break
            if action in ("R", "C", "A"):
                any_prior_voluntary = True

    return {
        "vpip": vpip,
        "pfr": pfr,
        "three_bet": three_bet,
        "cbet": cbet,
        "is_steal_attempt": is_steal_attempt,
    }


# ---------------------------------------------------------------------------
# Feature: ai-player-profiling, Property 1: Stat flag computation correctness
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(data=st.data())
def test_stat_flag_computation_correctness(data):
    """For any valid hand action sequence and any participating player,
    compute_stat_flags produces boolean flags consistent with the action
    sequence definitions.

    Validates: Requirements 1.5, 1.6, 1.7, 1.9, 1.12
    """
    # Feature: ai-player-profiling, Property 1: Stat flag computation correctness
    actions, player_names, positions_map, bb_player = data.draw(
        hand_history_strategy()
    )

    # Pick a player to test
    target = data.draw(st.sampled_from(player_names))
    position = positions_map[target]

    # Compute flags using production code
    result: PlayerFlags = compute_stat_flags(
        actions=actions,
        player_name=target,
        position=position,
        went_to_showdown=False,  # not tested in this property
        won_hand=False,          # not tested in this property
        bb_player_name=bb_player,
    )

    # Compute expected flags using reference oracle
    expected = _oracle_compute_flags(actions, target, position, bb_player)

    # Assert each flag matches
    assert result.vpip == expected["vpip"], (
        f"vpip mismatch for {target} (pos={position}): "
        f"got {result.vpip}, expected {expected['vpip']}. "
        f"Actions: {actions}"
    )
    assert result.pfr == expected["pfr"], (
        f"pfr mismatch for {target} (pos={position}): "
        f"got {result.pfr}, expected {expected['pfr']}. "
        f"Actions: {actions}"
    )
    assert result.three_bet == expected["three_bet"], (
        f"three_bet mismatch for {target} (pos={position}): "
        f"got {result.three_bet}, expected {expected['three_bet']}. "
        f"Actions: {actions}"
    )
    assert result.cbet == expected["cbet"], (
        f"cbet mismatch for {target} (pos={position}): "
        f"got {result.cbet}, expected {expected['cbet']}. "
        f"Actions: {actions}"
    )
    assert result.is_steal_attempt == expected["is_steal_attempt"], (
        f"is_steal_attempt mismatch for {target} (pos={position}): "
        f"got {result.is_steal_attempt}, expected {expected['is_steal_attempt']}. "
        f"Actions: {actions}"
    )
