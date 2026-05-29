from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime

from app.game.dealer import Dealer
from app.game.evaluator import Evaluator
from app.game.models import Action, ActionType, Hand, MoveLog, Phase, SessionStatus
from app.game.players import Player
from app.game.pot_manager import PotManager
from app.profiling.hand_recorder import ErrorRecord, HandRecord, compute_stat_flags

logger = logging.getLogger(__name__)

SMALL_BLIND = 10
BIG_BLIND = 20
STARTING_CHIPS = 1000

# Blind schedule: (small_blind, big_blind) per level.
# Level advances every HANDS_PER_BLIND_LEVEL hands.
HANDS_PER_BLIND_LEVEL = 10
BLIND_SCHEDULE: list[tuple[int, int]] = [
    (10, 20),  # level 0  — hands 1-10
    (20, 40),  # level 1  — hands 11-20
    (40, 80),  # level 2  — hands 21-30
    (75, 150),  # level 3  — hands 31-40
    (150, 300),  # level 4  — hands 41-50
    (300, 600),  # level 5  — hands 51-60
    (500, 1000),  # level 6  — hands 61+  (stays here)
]


def blinds_for_hand(hand_number: int) -> tuple[int, int]:
    """Return (small_blind, big_blind) for the given hand number (1-indexed)."""
    level = min((hand_number - 1) // HANDS_PER_BLIND_LEVEL, len(BLIND_SCHEDULE) - 1)
    return BLIND_SCHEDULE[level]


# Maps for compact hand history encoding
_PHASE_ABBR: dict[Phase, str] = {
    Phase.PRE_FLOP: "pre-flop",
    Phase.FLOP: "flop",
    Phase.TURN: "turn",
    Phase.RIVER: "river",
}
_ACTION_CODE: dict[ActionType, str] = {
    ActionType.FOLD: "F",
    ActionType.CHECK: "X",
    ActionType.CALL: "C",
    ActionType.RAISE: "R",
    ActionType.ALL_IN: "A",
}


class GameSession:
    def __init__(self, session_id: str, host_player_id: str) -> None:
        self.session_id = session_id
        self.host_player_id = host_player_id
        self.players: list[Player] = []
        self.status: SessionStatus = SessionStatus.LOBBY
        self.dealer_button_index: int = 0
        self.current_hand: Hand | None = None
        self.last_activity: datetime = datetime.now()
        self._pot_manager: PotManager = PotManager()
        self._dealer: Dealer = Dealer()
        self._evaluator: Evaluator = Evaluator()
        # Track contributions at the start of each betting round for round-relative bet tracking
        self._round_start_contributions: dict[str, int] = {}
        # Track which players have acted in the current betting round
        self._players_acted: set[str] = set()
        # AI move logs for the current hand
        self._hand_move_logs: list[MoveLog] = []
        # Hand counter — increments each time a new hand starts
        self.hand_number: int = 0
        # Profiling service reference (set externally by ArenaManager / event handlers)
        self.profiling_service = None
        # Unique game ID for profiling (avoids collisions across restarts)
        self.profiling_game_id: str = uuid.uuid4().hex[:12]
        # Showdown state: hole cards revealed, waiting for host to start next hand
        self.showdown_pending: bool = False
        self._showdown_hole_cards: dict[str, list] = {}  # player_id -> list of cards
        self._showdown_community_cards: list = []
        self._showdown_results: list[dict] = []  # [{player_id, name, hand_rank, chips_won}]
        self._showdown_hand_history: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_player(self, player: Player) -> None:
        if self.status != SessionStatus.LOBBY:
            raise ValueError("Cannot add player: game is not in LOBBY status")
        if len(self.players) >= 9:
            raise ValueError("Cannot add player: session is full (max 9 players)")
        self.players.append(player)
        self.last_activity = datetime.now()
        logger.info(
            "Player added session=%s player=%s name=%r",
            self.session_id,
            player.player_id,
            player.name,
        )

    def remove_player(self, player_id: str) -> None:
        if self.status != SessionStatus.LOBBY:
            raise ValueError("Cannot remove player: game is not in LOBBY status")
        self.players = [p for p in self.players if p.player_id != player_id]
        self.last_activity = datetime.now()
        logger.info("Player removed session=%s player=%s", self.session_id, player_id)

    def start_game(self) -> None:
        if self.status != SessionStatus.LOBBY:
            raise ValueError("Cannot start game: game is not in LOBBY status")
        if len(self.players) < 2:
            raise ValueError("Cannot start game: at least 2 players required")
        self.status = SessionStatus.ACTIVE
        logger.info("Game started session=%s players=%d", self.session_id, len(self.players))
        self._start_hand()

    def apply_action(self, player_id: str, action: Action, reasoning: str = "") -> None:
        if self.status != SessionStatus.ACTIVE or self.current_hand is None:
            raise ValueError("Cannot apply action: game is not active")

        hand = self.current_hand
        active = self._get_active_players()
        if not active:
            raise ValueError("No active players")

        if hand.current_player_id != player_id:
            raise ValueError(f"It is not player {player_id}'s turn")

        current_player = next((p for p in active if p.player_id == player_id), None)
        if current_player is None:
            raise ValueError(f"Player {player_id} is not active")

        valid = self.get_valid_actions(current_player)
        valid_types = [a.type for a in valid]
        if action.type not in valid_types:
            raise ValueError(f"Action {action.type} is not valid for player {player_id}")

        # Validate bet amounts against the player's actual chip stack
        if action.type == ActionType.RAISE:
            round_contrib = self._pot_manager.contributions.get(
                player_id, 0
            ) - self._round_start_contributions.get(player_id, 0)
            chips_needed = action.amount - round_contrib
            if chips_needed > current_player.chips:
                raise ValueError(
                    f"Raise amount {action.amount} requires {chips_needed} chips "
                    f"but player only has {current_player.chips}"
                )
            if action.amount <= hand.current_bet:
                raise ValueError(
                    f"Raise amount {action.amount} must exceed current bet {hand.current_bet}"
                )
        elif action.type == ActionType.ALL_IN:
            if action.amount != current_player.chips:
                logger.warning(
                    "ALL_IN amount corrected session=%s player=%s submitted=%s chips=%s",
                    self.session_id,
                    player_id,
                    action.amount,
                    current_player.chips,
                )
                action.amount = current_player.chips
        elif action.type == ActionType.CALL:
            round_contrib = self._pot_manager.contributions.get(
                player_id, 0
            ) - self._round_start_contributions.get(player_id, 0)
            max_call = min(hand.current_bet - round_contrib, current_player.chips)
            if action.amount > max_call:
                raise ValueError(f"Call amount {action.amount} exceeds maximum {max_call}")

        logger.debug(
            "Action applied session=%s player=%s action=%s amount=%s",
            self.session_id,
            player_id,
            action.type.value,
            action.amount or "",
        )
        self._apply_action_to_player(current_player, action, hand)
        self.last_activity = datetime.now()

        # Record compact history entry
        phase_abbr = _PHASE_ABBR.get(hand.phase, "?")
        action_code = _ACTION_CODE.get(action.type, "?")
        amount = action.amount if action.type in (ActionType.RAISE, ActionType.ALL_IN) else None
        hand.history.append((phase_abbr, current_player.name, action_code, amount))

        # Append MoveLog for AI players
        if hasattr(current_player, "provider"):
            amount_for_log = (
                action.amount if action.type in (ActionType.RAISE, ActionType.ALL_IN) else None
            )
            self._hand_move_logs.append(
                MoveLog(
                    player_name=current_player.name,
                    phase=hand.phase.value,
                    action=action.type.value,
                    amount=amount_for_log,
                    reasoning=reasoning,
                )
            )

        self._advance_phase_if_needed()

    def get_valid_actions(self, player: Player) -> list[Action]:
        if self.current_hand is None:
            return []

        hand = self.current_hand
        # Round-relative contribution: what this player has put in this round
        round_contrib = self._pot_manager.contributions.get(
            player.player_id, 0
        ) - self._round_start_contributions.get(player.player_id, 0)
        call_amount = max(0, hand.current_bet - round_contrib)

        actions: list[Action] = []

        # FOLD always valid if in hand
        actions.append(Action(type=ActionType.FOLD))

        # CHECK valid if no bet to call
        if call_amount == 0:
            actions.append(Action(type=ActionType.CHECK))

        # CALL valid if there's a bet to call and player has chips
        if call_amount > 0 and player.chips > 0:
            actions.append(Action(type=ActionType.CALL, amount=min(call_amount, player.chips)))

        # RAISE valid if player has enough chips beyond the call
        if player.chips > call_amount + hand.min_raise:
            actions.append(Action(type=ActionType.RAISE, amount=hand.current_bet + hand.min_raise))

        # ALL_IN always valid if player has chips
        if player.chips > 0:
            actions.append(Action(type=ActionType.ALL_IN, amount=player.chips))

        return actions

    def get_public_state(self) -> dict:
        hand = self.current_hand
        pot = (
            sum(self._pot_manager.contributions.values()) if self._pot_manager.contributions else 0
        )

        player_list = []
        active = self._get_active_players()
        hand = self.current_hand
        current_player_id = None
        if hand is not None and active:
            current_player_id = hand.current_player_id

        for p in self.players:
            player_entry = {
                "player_id": p.player_id,
                "name": p.name,
                "chips": p.chips,
                "current_bet": p.current_bet,
                "is_active": p.is_active,
                "is_eliminated": p.is_eliminated,
                "is_turn": p.player_id == current_player_id,
                "is_ai": hasattr(p, "provider"),
                "model": getattr(p, "model", None),
            }
            # Reveal all hole cards during showdown
            if self.showdown_pending and p.player_id in self._showdown_hole_cards:
                player_entry["hole_cards"] = [
                    {"rank": c.rank, "suit": c.suit} for c in self._showdown_hole_cards[p.player_id]
                ]
            player_list.append(player_entry)

        state: dict = {
            "session_id": self.session_id,
            "status": self.status.value,
            "players": player_list,
            "dealer_button": self._dealer_button_full_index(),
            "showdown_pending": self.showdown_pending,
            "hand_number": self.hand_number,
            "blinds": blinds_for_hand(max(self.hand_number, 1)),
        }

        if hand is not None:
            state["phase"] = hand.phase.value
            state["community_cards"] = [
                {"rank": c.rank, "suit": c.suit} for c in hand.community_cards
            ]
            state["pot"] = pot
            state["min_raise"] = hand.min_raise
            state["current_bet"] = hand.current_bet
            state["hand_history"] = hand.history
        elif self.showdown_pending:
            state["phase"] = "showdown"
            state["community_cards"] = [
                {"rank": c.rank, "suit": c.suit} for c in self._showdown_community_cards
            ]
            state["pot"] = pot
            state["min_raise"] = blinds_for_hand(max(self.hand_number, 1))[1]
            state["current_bet"] = 0
            state["hand_history"] = self._showdown_hand_history
            state["showdown_results"] = self._showdown_results
            state["ai_move_review"] = [
                {
                    "player_name": log.player_name,
                    "phase": log.phase,
                    "action": log.action,
                    "amount": log.amount,
                    "reasoning": log.reasoning,
                }
                for log in self._hand_move_logs
            ]
        else:
            state["phase"] = None
            state["community_cards"] = []
            state["pot"] = pot
            state["min_raise"] = blinds_for_hand(max(self.hand_number, 1))[1]
            state["current_bet"] = 0

        return state

    def get_player_state(self, player_id: str) -> dict:
        state = self.get_public_state()
        player = next((p for p in self.players if p.player_id == player_id), None)
        if player is not None:
            state["hole_cards"] = [{"rank": c.rank, "suit": c.suit} for c in player.hole_cards]
            state["valid_actions"] = [
                {"type": a.type.value, "amount": a.amount} for a in self.get_valid_actions(player)
            ]
            state["position"] = self._get_position_label(player_id)
        return state

    def next_hand(self) -> None:
        """Called by the host after reviewing the showdown to start the next hand."""
        if not self.showdown_pending:
            raise ValueError("No showdown pending")
        self.showdown_pending = False
        self._showdown_hole_cards = {}
        self._showdown_community_cards = []
        self._showdown_results = []
        self._showdown_hand_history = []
        non_eliminated = self._get_non_eliminated_players()
        if len(non_eliminated) <= 1:
            self._end_session()
        else:
            self._start_hand()

    # ------------------------------------------------------------------
    # Private methods
    # ------------------------------------------------------------------

    def _start_hand(self) -> None:
        self._hand_move_logs = []
        self.hand_number += 1
        non_eliminated = self._get_non_eliminated_players()
        if len(non_eliminated) < 2:
            self._end_session()
            return

        logger.info(
            "Hand started session=%s players=%s",
            self.session_id,
            [p.player_id for p in non_eliminated],
        )

        # Randomize the initial dealer, then rotate normally on later hands.
        if self.hand_number == 1:
            self.dealer_button_index = random.randrange(len(non_eliminated))
        else:
            self.dealer_button_index = self._dealer.advance_dealer_button(
                self.dealer_button_index, len(non_eliminated)
            )

        # Reset pot manager
        self._pot_manager = PotManager()
        self._round_start_contributions = {}
        self._players_acted = set()

        # Reset player states
        for p in self.players:
            p.hole_cards = []
            p.current_bet = 0
            if not p.is_eliminated:
                p.is_active = True

        # Shuffle and deal
        deck = self._dealer.shuffle_deck()
        self._dealer.deal_hole_cards(non_eliminated, deck)

        # Post blinds using modular indexing over non-eliminated players
        num = len(non_eliminated)
        sb_index = (self.dealer_button_index + 1) % num
        bb_index = (self.dealer_button_index + 2) % num

        sb_player = non_eliminated[sb_index]
        bb_player = non_eliminated[bb_index]

        small_blind, big_blind = blinds_for_hand(self.hand_number)
        logger.debug(
            "Hand %d blinds session=%s sb=%d bb=%d",
            self.hand_number,
            self.session_id,
            small_blind,
            big_blind,
        )

        self._pot_manager.post_blind(sb_player, small_blind)
        self._pot_manager.post_blind(bb_player, big_blind)

        # Set player current_bet to their round contributions
        sb_player.current_bet = self._pot_manager.contributions.get(sb_player.player_id, 0)
        bb_player.current_bet = self._pot_manager.contributions.get(bb_player.player_id, 0)

        min_raise = big_blind
        current_bet = self._pot_manager.current_bet

        # First player to act pre-flop is after big blind
        first_actor_id = non_eliminated[(bb_index + 1) % num].player_id

        self.current_hand = Hand(
            deck=deck,
            community_cards=[],
            pots=[],
            phase=Phase.PRE_FLOP,
            active_players=list(non_eliminated),
            current_player_id=first_actor_id,
            min_raise=min_raise,
            current_bet=current_bet,
        )

    def _advance_phase_if_needed(self) -> None:
        hand = self.current_hand
        if hand is None:
            return

        active = self._get_active_players()

        # If only 1 active player remains, end the hand
        if len(active) <= 1:
            self._advance_phase()
            return

        # Check if betting round is complete:
        # All active players have acted AND matched current_bet (round-relative) or are all-in
        all_acted = all(p.player_id in self._players_acted or p.chips == 0 for p in active)
        bets_matched = all(
            (
                self._pot_manager.contributions.get(p.player_id, 0)
                - self._round_start_contributions.get(p.player_id, 0)
            )
            >= hand.current_bet
            or p.chips == 0
            for p in active
        )

        if all_acted and bets_matched:
            self._advance_phase()
        else:
            # Advance to the next active player after the current one
            hand.current_player_id = self._next_active_player_id(hand.current_player_id, active)

    def _advance_phase(self) -> None:
        hand = self.current_hand
        if hand is None:
            return

        active = self._get_active_players()

        # If only 1 active player remains, end the hand immediately
        if len(active) <= 1:
            self._end_hand()
            return

        # Reset bets for new phase
        self._reset_bets(hand)

        if hand.phase == Phase.PRE_FLOP:
            self._dealer.deal_community_cards(3, hand.deck, hand.community_cards)
            hand.phase = Phase.FLOP
        elif hand.phase == Phase.FLOP:
            self._dealer.deal_community_cards(1, hand.deck, hand.community_cards)
            hand.phase = Phase.TURN
        elif hand.phase == Phase.TURN:
            self._dealer.deal_community_cards(1, hand.deck, hand.community_cards)
            hand.phase = Phase.RIVER
        elif hand.phase == Phase.RIVER:
            hand.phase = Phase.SHOWDOWN
            self._end_hand()
            return

        logger.debug("Phase advanced session=%s phase=%s", self.session_id, hand.phase.value)

        # If all remaining active players are all-in, run out the board without asking anyone to act
        if all(p.chips == 0 for p in self._get_active_players()):
            logger.debug("All players all-in session=%s, running out board", self.session_id)
            self._advance_phase()
            return

        # Set current player to first active player with chips after dealer button
        non_eliminated = self._get_non_eliminated_players()
        num_ne = len(non_eliminated)
        active_after_reset = self._get_active_players()
        for offset in range(1, num_ne + 1):
            candidate = non_eliminated[(self.dealer_button_index + offset) % num_ne]
            if candidate in active_after_reset and candidate.chips > 0:
                hand.current_player_id = candidate.player_id
                break
        else:
            # Fallback: no player with chips found — run out the board
            self._advance_phase()

    def _end_hand(self) -> None:
        hand = self.current_hand
        if hand is None:
            return

        active = self._get_active_players()
        all_players = self._get_non_eliminated_players()

        if len(active) == 1:
            ranked_groups = [active]
        else:
            ranked_groups = self._evaluator.rank_players(active, hand.community_cards)

        chips_before = {p.player_id: p.chips for p in all_players}
        self._pot_manager.distribute(ranked_groups, all_players)

        # Snapshot hole cards and community cards for showdown reveal
        self._showdown_hole_cards = {
            p.player_id: list(p.hole_cards) for p in all_players if p.hole_cards
        }
        self._showdown_community_cards = list(hand.community_cards)
        self._showdown_hand_history: list = list(hand.history)

        # Build winner results for the log
        self._showdown_results = []
        winners = ranked_groups[0] if ranked_groups else []
        for p in winners:
            chips_won = p.chips - chips_before.get(p.player_id, p.chips)
            if len(active) > 1 and hand.community_cards:
                hand_result = self._evaluator.best_hand(p.hole_cards + hand.community_cards)
                hand_rank_label = hand_result.rank.name.replace("_", " ").title()
            else:
                hand_rank_label = None  # fold win — no showdown
            self._showdown_results.append(
                {
                    "player_id": p.player_id,
                    "name": p.name,
                    "hand_rank": hand_rank_label,
                    "chips_won": chips_won,
                }
            )
            logger.info(
                "Hand won session=%s player=%s name=%r hand=%s chips_won=%d",
                self.session_id,
                p.player_id,
                p.name,
                hand_rank_label,
                chips_won,
            )

        # Record hand to profiling service
        self._record_hand_to_profiling(hand, all_players, chips_before, winners)

        # Eliminate players with 0 chips
        for p in self.players:
            if p.chips == 0:
                p.is_eliminated = True
                logger.info(
                    "Player eliminated session=%s player=%s name=%r",
                    self.session_id,
                    p.player_id,
                    p.name,
                )

        non_eliminated = self._get_non_eliminated_players()
        if len(non_eliminated) <= 1:
            self._end_session()
        else:
            # Pause for showdown review — host must call next_hand() to continue
            self.showdown_pending = True
            self.current_hand = None
            logger.info("Showdown pending session=%s", self.session_id)

    def _end_session(self) -> None:
        self.status = SessionStatus.COMPLETE
        self.current_hand = None
        winner = self._get_non_eliminated_players()
        logger.info(
            "Session complete session=%s winner=%s",
            self.session_id,
            winner[0].player_id if winner else "none",
        )

    def _record_hand_to_profiling(
        self,
        hand: Hand,
        all_players: list[Player],
        chips_before: dict[str, int],
        winners: list[Player],
    ) -> None:
        """Build a HandRecord from the hand's state and record it via profiling_service.

        Wrapped in try/except to avoid disrupting gameplay.
        """
        if self.profiling_service is None or not self.profiling_service.available:
            return

        try:
            winner_ids = {p.player_id for p in winners}
            active_at_showdown = {p.player_id for p in all_players if p.is_active}

            # Determine dealer, SB, BB from non-eliminated players and dealer_button_index
            non_eliminated = all_players
            num = len(non_eliminated)
            if num == 0:
                return

            btn_idx = self.dealer_button_index % num
            sb_idx = (btn_idx + 1) % num
            bb_idx = (btn_idx + 2) % num

            dealer_player = non_eliminated[btn_idx]
            sb_player = non_eliminated[sb_idx]
            bb_player = non_eliminated[bb_idx]

            # Community cards as a string representation
            community_str = " ".join(repr(c) for c in hand.community_cards)

            # Final pot size
            pot = sum(self._pot_manager.contributions.values())

            # Blind amounts for this hand
            blind_amounts = blinds_for_hand(self.hand_number)

            # Build player flags for each participant
            player_flags = []
            for p in all_players:
                position = self._get_position_label(p.player_id)
                went_to_showdown = p.player_id in active_at_showdown and len(active_at_showdown) > 1
                won_hand = p.player_id in winner_ids

                flags = compute_stat_flags(
                    actions=hand.history,
                    player_name=p.name,
                    position=position,
                    went_to_showdown=went_to_showdown,
                    won_hand=won_hand,
                    bb_player_name=bb_player.name,
                )
                # Fill in caller-provided fields
                flags.player_id = p.player_id
                flags.hole_cards = " ".join(repr(c) for c in p.hole_cards) if p.hole_cards else ""
                flags.starting_stack = chips_before.get(p.player_id, 0)
                flags.net_result = p.chips - chips_before.get(p.player_id, 0)

                player_flags.append(flags)

            # Collect error records from AI move logs (malformed responses)
            errors = []

            hand_record = HandRecord(
                hand_id=f"{self.profiling_game_id}_hand_{self.hand_number}",
                game_id=self.profiling_game_id,
                hand_number=self.hand_number,
                dealer=dealer_player.name,
                small_blind_player=sb_player.name,
                big_blind_player=bb_player.name,
                blind_amounts=blind_amounts,
                community_cards=community_str,
                pot=pot,
                actions=list(hand.history),
                player_flags=player_flags,
                errors=errors,
            )

            self.profiling_service.record_hand(hand_record)
        except Exception as exc:
            logger.error(
                "Failed to record hand to profiling session=%s hand=%d: %s",
                self.session_id,
                self.hand_number,
                exc,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_active_players(self) -> list[Player]:
        """Players who are not eliminated and still active in the current hand."""
        return [p for p in self.players if not p.is_eliminated and p.is_active]

    def _next_active_player_id(self, current_id: str, active: list[Player]) -> str:
        """Return the player_id of the next active player after current_id who still has chips.

        Skips all-in players (chips == 0) since they have no decision to make.
        Uses self.players (full seat order) to find the correct next seat.
        """
        # Only players with chips remaining need to act
        actionable_ids = {p.player_id for p in active if p.chips > 0}
        all_ids = [p.player_id for p in self.players]
        if not all_ids:
            return current_id
        try:
            start = all_ids.index(current_id)
        except ValueError:
            actionable = [p for p in active if p.chips > 0]
            return actionable[0].player_id if actionable else current_id
        # Walk forward from the seat after current_id, skipping all-in players
        for offset in range(1, len(all_ids) + 1):
            candidate = all_ids[(start + offset) % len(all_ids)]
            if candidate in actionable_ids:
                return candidate
        # All remaining active players are all-in — fall back to first active
        active_ids = {p.player_id for p in active}
        for offset in range(1, len(all_ids) + 1):
            candidate = all_ids[(start + offset) % len(all_ids)]
            if candidate in active_ids:
                return candidate
        return active[0].player_id

    def _get_non_eliminated_players(self) -> list[Player]:
        """Players who have not been eliminated."""
        return [p for p in self.players if not p.is_eliminated]

    def _dealer_button_full_index(self) -> int:
        """Translate dealer_button_index (index into non-eliminated list) to
        the index of that player in the full self.players list."""
        non_eliminated = self._get_non_eliminated_players()
        if not non_eliminated:
            return 0
        dealer_player = non_eliminated[self.dealer_button_index % len(non_eliminated)]
        try:
            return self.players.index(dealer_player)
        except ValueError:
            return 0

    def _get_position_label(self, player_id: str) -> str:
        """Return a poker position label (BTN, SB, BB, UTG, MP, CO, etc.) for the given player."""
        non_eliminated = self._get_non_eliminated_players()
        n = len(non_eliminated)
        if n == 0:
            return "unknown"

        # Find this player's index in the non-eliminated list
        try:
            seat = next(i for i, p in enumerate(non_eliminated) if p.player_id == player_id)
        except StopIteration:
            return "unknown"

        # Positions relative to dealer button (modular)
        btn = self.dealer_button_index % n
        offset = (seat - btn) % n  # 0 = BTN, 1 = SB, 2 = BB, ...

        if n == 2:
            # Heads-up: dealer is SB, other is BB
            return "SB/BTN" if offset == 0 else "BB"

        position_map = {0: "BTN", 1: "SB", 2: "BB"}
        if offset in position_map:
            return position_map[offset]

        # Remaining seats: UTG, UTG+1, MP, MP+1, CO (up to n-1 seats after BB)
        late_labels = ["UTG", "UTG+1", "MP", "MP+1", "HJ", "CO"]
        late_offset = offset - 3  # 0-indexed from UTG
        if late_offset < len(late_labels):
            return late_labels[late_offset]
        return f"Seat {seat + 1}"

    def _reset_bets(self, hand: Hand) -> None:
        """Reset per-round bet tracking for a new betting phase."""
        hand.current_bet = 0
        hand.min_raise = blinds_for_hand(max(self.hand_number, 1))[1]
        # Snapshot current cumulative contributions as the round baseline
        self._round_start_contributions = dict(self._pot_manager.contributions)
        self._pot_manager.current_bet = 0
        # Reset acted tracking for new round
        self._players_acted = set()
        # Reset player current_bet display
        for p in self.players:
            p.current_bet = 0

    def _apply_action_to_player(self, player: Player, action: Action, hand: Hand) -> None:
        """Apply a validated action to the player and update state."""
        self._players_acted.add(player.player_id)

        if action.type == ActionType.FOLD:
            player.is_active = False

        elif action.type == ActionType.CHECK:
            # No chips change
            pass

        elif action.type == ActionType.CALL:
            round_contrib = self._pot_manager.contributions.get(
                player.player_id, 0
            ) - self._round_start_contributions.get(player.player_id, 0)
            call_amount = max(0, hand.current_bet - round_contrib)
            call_amount = min(call_amount, player.chips)
            if call_amount > 0:
                self._pot_manager.place_bet(player, call_amount)
            player.current_bet = self._pot_manager.contributions.get(
                player.player_id, 0
            ) - self._round_start_contributions.get(player.player_id, 0)

        elif action.type == ActionType.RAISE:
            round_contrib = self._pot_manager.contributions.get(
                player.player_id, 0
            ) - self._round_start_contributions.get(player.player_id, 0)
            # action.amount is the total new bet level for this round
            if action.amount > 0:
                total_round_bet = action.amount
            else:
                total_round_bet = hand.current_bet + hand.min_raise

            raise_increment = total_round_bet - hand.current_bet
            chips_to_put_in = min(total_round_bet - round_contrib, player.chips)

            if chips_to_put_in > 0:
                self._pot_manager.place_bet(player, chips_to_put_in)

            player.current_bet = self._pot_manager.contributions.get(
                player.player_id, 0
            ) - self._round_start_contributions.get(player.player_id, 0)

            # Update hand state
            new_round_contrib = player.current_bet
            if new_round_contrib > hand.current_bet:
                hand.min_raise = max(hand.min_raise, new_round_contrib - hand.current_bet)
                hand.current_bet = new_round_contrib
                self._pot_manager.current_bet = hand.current_bet

        elif action.type == ActionType.ALL_IN:
            all_in_amount = player.chips
            if all_in_amount > 0:
                self._pot_manager.place_bet(player, all_in_amount)

            new_round_contrib = self._pot_manager.contributions.get(
                player.player_id, 0
            ) - self._round_start_contributions.get(player.player_id, 0)
            player.current_bet = new_round_contrib

            if new_round_contrib > hand.current_bet:
                raise_increment = new_round_contrib - hand.current_bet
                hand.min_raise = max(hand.min_raise, raise_increment)
                hand.current_bet = new_round_contrib
                self._pot_manager.current_bet = hand.current_bet
