from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlayerFlags:
    player_id: str
    vpip: bool
    pfr: bool
    three_bet: bool | None
    fold_to_three_bet: bool | None
    cbet: bool | None
    fold_to_cbet: bool | None
    is_steal_attempt: bool
    went_to_showdown: bool
    won_hand: bool
    position: str
    hole_cards: str
    starting_stack: int
    net_result: int


@dataclass
class ErrorRecord:
    player_id: str
    error_type: str
    raw_response: str


@dataclass
class HandRecord:
    hand_id: str
    game_id: str
    hand_number: int
    dealer: str
    small_blind_player: str
    big_blind_player: str
    blind_amounts: tuple[int, int]
    community_cards: str
    pot: int
    actions: list[tuple[str, str, str, int | None]]
    player_flags: list[PlayerFlags] = field(default_factory=list)
    errors: list[ErrorRecord] = field(default_factory=list)


# Positions eligible for steal attempts
_STEAL_POSITIONS = {"CO", "BTN", "SB", "SB/BTN"}


def compute_stat_flags(
    actions: list[tuple[str, str, str, int | None]],
    player_name: str,
    position: str,
    went_to_showdown: bool,
    won_hand: bool,
    bb_player_name: str,
) -> PlayerFlags:
    """Pure function: compute boolean stat flags from a hand's action history.

    Action format: list of (phase, player_name, action_code, amount) tuples.
    Action codes: F=fold, X=check, C=call, R=raise, A=all-in.
    Phase labels: "pre-flop", "flop", "turn", "river".
    """

    # --- Pre-flop analysis ---
    vpip = False
    pfr = False
    three_bet: bool | None = None
    fold_to_three_bet: bool | None = None

    # Track raise count in pre-flop to detect 3-bets
    preflop_raise_count = 0
    # Whether a raise existed before the player's first pre-flop action
    saw_raise_before_first_action = False
    player_has_acted_preflop = False
    # Track who made the last pre-flop raise (for cbet detection)
    preflop_raiser: str | None = None
    # Track the raise count at the time the player last raised (for fold_to_3bet)
    player_last_raise_count: int | None = None
    player_faced_three_bet = False

    for phase, pname, action, _amount in actions:
        if phase != "pre-flop":
            break

        is_target = pname == player_name

        if is_target and not player_has_acted_preflop:
            saw_raise_before_first_action = preflop_raise_count > 0
            player_has_acted_preflop = True

        if action in ("R", "A"):
            preflop_raise_count += 1
            preflop_raiser = pname

            if is_target:
                vpip = True
                pfr = True
                player_last_raise_count = preflop_raise_count
                # 3-bet: player re-raised after a prior raise existed
                if preflop_raise_count >= 2:
                    three_bet = True
                else:
                    three_bet = False
            else:
                # Another player raised — check if target faces a 3-bet
                if (
                    player_last_raise_count is not None
                    and preflop_raise_count > player_last_raise_count
                ):
                    player_faced_three_bet = True

        elif action == "C" and is_target:
            vpip = True

        elif action == "F" and is_target:
            if player_faced_three_bet:
                fold_to_three_bet = True
            break

    # three_bet nullability: NULL if no prior raise existed before the player's
    # first pre-flop action (i.e., the player had no opportunity to 3-bet)
    if not player_has_acted_preflop:
        three_bet = None
    elif not saw_raise_before_first_action:
        three_bet = None
    elif saw_raise_before_first_action and three_bet is None:
        # Player had the opportunity to 3-bet but didn't raise
        three_bet = False

    # fold_to_three_bet: NULL if player didn't face a 3-bet
    if player_faced_three_bet and fold_to_three_bet is None:
        fold_to_three_bet = False
    elif not player_faced_three_bet:
        fold_to_three_bet = None

    # --- Flop analysis (cbet / fold_to_cbet) ---
    cbet: bool | None = None
    fold_to_cbet: bool | None = None

    # Check if flop actions exist
    flop_actions = [
        (phase, pname, action, amt) for phase, pname, action, amt in actions if phase == "flop"
    ]

    if not flop_actions:
        # Hand ended before flop — cbet and fold_to_cbet are NULL
        cbet = None
        fold_to_cbet = None
    else:
        # cbet: only relevant if the player was the pre-flop raiser
        if preflop_raiser == player_name:
            cbet = False
            for _phase, pname, action, _amt in flop_actions:
                if pname == player_name:
                    if action in ("R", "A"):
                        cbet = True
                    break  # Only care about the PFR's first flop action
        else:
            cbet = None

        # fold_to_cbet: did the player fold to a cbet on the flop?
        # A cbet is when the pre-flop raiser bets/raises on the flop
        if preflop_raiser is not None and preflop_raiser != player_name:
            cbet_happened = False
            player_folded_to_cbet = False
            for _phase, pname, action, _amt in flop_actions:
                if pname == preflop_raiser and action in ("R", "A"):
                    cbet_happened = True
                elif cbet_happened and pname == player_name:
                    if action == "F":
                        player_folded_to_cbet = True
                    break
            if cbet_happened:
                fold_to_cbet = player_folded_to_cbet
            else:
                fold_to_cbet = None
        else:
            fold_to_cbet = None

    # --- Steal attempt ---
    is_steal_attempt = False
    if position in _STEAL_POSITIONS:
        # Check if there were any voluntary actions (R, C, A) before the player in pre-flop
        any_prior_voluntary = False
        for phase, pname, action, _amt in actions:
            if phase != "pre-flop":
                break
            if pname == player_name:
                # Player's first action — if it's a raise and no prior voluntary, it's a steal
                if action in ("R", "A") and not any_prior_voluntary:
                    is_steal_attempt = True
                break
            if action in ("R", "C", "A"):
                any_prior_voluntary = True

    return PlayerFlags(
        player_id="",  # Caller fills this in
        vpip=vpip,
        pfr=pfr,
        three_bet=three_bet,
        fold_to_three_bet=fold_to_three_bet,
        cbet=cbet,
        fold_to_cbet=fold_to_cbet,
        is_steal_attempt=is_steal_attempt,
        went_to_showdown=went_to_showdown,
        won_hand=won_hand,
        position=position,
        hole_cards="",  # Caller fills this in
        starting_stack=0,  # Caller fills this in
        net_result=0,  # Caller fills this in
    )
