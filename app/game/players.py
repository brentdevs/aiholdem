from __future__ import annotations

from typing import TYPE_CHECKING

from app.game.models import Action, Card


class Player:
    def __init__(
        self,
        player_id: str,
        name: str,
        chips: int,
    ) -> None:
        self.player_id = player_id
        self.name = name
        self.chips = chips
        self.hole_cards: list[Card] = []
        self.current_bet: int = 0
        self.is_active: bool = True
        self.is_eliminated: bool = False

    def __repr__(self) -> str:
        return f"Player(id={self.player_id!r}, name={self.name!r}, chips={self.chips})"


class AIPlayer(Player):
    def __init__(
        self,
        player_id: str,
        name: str,
        chips: int,
        provider: str,
    ) -> None:
        super().__init__(player_id, name, chips)
        self.provider = provider  # e.g. "openrouter" or "ollama"

    async def decide_action(self, game_state: dict, valid_actions: list[Action]) -> Action:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"AIPlayer(id={self.player_id!r}, name={self.name!r}, chips={self.chips}, provider={self.provider!r})"
