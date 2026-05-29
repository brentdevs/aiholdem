from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.game.players import Player


class HandRank(IntEnum):
    HIGH_CARD = 1
    ONE_PAIR = 2
    TWO_PAIR = 3
    THREE_OF_A_KIND = 4
    STRAIGHT = 5
    FLUSH = 6
    FULL_HOUSE = 7
    FOUR_OF_A_KIND = 8
    STRAIGHT_FLUSH = 9
    ROYAL_FLUSH = 10


class ActionType(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    RAISE = "raise"
    ALL_IN = "all_in"


class Phase(str, Enum):
    PRE_FLOP = "pre_flop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


class SessionStatus(str, Enum):
    LOBBY = "lobby"
    ACTIVE = "active"
    COMPLETE = "complete"
    INACTIVE = "inactive"


@dataclass(frozen=True)
class Card:
    rank: int  # 2–14 (14 = Ace)
    suit: str  # 'S', 'H', 'D', 'C'

    def __post_init__(self) -> None:
        if not (2 <= self.rank <= 14):
            raise ValueError(f"Invalid rank: {self.rank}")
        if self.suit not in ("S", "H", "D", "C"):
            raise ValueError(f"Invalid suit: {self.suit}")

    def __repr__(self) -> str:
        rank_names = {11: "J", 12: "Q", 13: "K", 14: "A"}
        r = rank_names.get(self.rank, str(self.rank))
        return f"{r}{self.suit}"


@dataclass
class HandResult:
    rank: HandRank
    tiebreakers: list[int]  # descending card values for kicker comparison
    cards: list[Card]  # the best 5 cards selected


@dataclass
class Action:
    type: ActionType
    amount: int = 0


@dataclass
class Pot:
    amount: int
    eligible_players: list[str]  # player_ids


@dataclass
class Hand:
    deck: list[Card]
    community_cards: list[Card]
    pots: list[Pot]
    phase: Phase
    active_players: list  # list[Player] — avoid circular import at runtime
    current_player_id: str  # player_id of the player whose turn it is
    min_raise: int
    current_bet: int
    # Hand history: list of (phase_abbr, player_name, action_code, amount_or_none)
    # phase_abbr: "PF"|"F"|"T"|"R", action_code: "F"|"X"|"C"|"R"|"A"
    history: list[tuple[str, str, str, int | None]] = field(default_factory=list)


@dataclass
class MoveLog:
    player_name: str
    phase: str  # e.g. "pre_flop"
    action: str  # e.g. "raise"
    amount: int | None
    reasoning: str
