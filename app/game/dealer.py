from __future__ import annotations

import random

from app.game.models import Card


class Dealer:
    def shuffle_deck(self) -> list[Card]:
        """Build a full 52-card deck and return it shuffled."""
        deck = [
            Card(rank=rank, suit=suit)
            for suit in ("S", "H", "D", "C")
            for rank in range(2, 15)
        ]
        random.shuffle(deck)
        return deck

    def deal_hole_cards(self, players: list, deck: list[Card]) -> None:
        """Deal 2 hole cards to each player in standard poker dealing order."""
        # First card to each player, then second card to each player
        for _ in range(2):
            for player in players:
                player.hole_cards.append(deck.pop())

    def deal_community_cards(self, n: int, deck: list[Card], community: list[Card]) -> None:
        """Pop n cards from the deck and append them to the community list."""
        for _ in range(n):
            community.append(deck.pop())

    def advance_dealer_button(self, current_index: int, num_players: int) -> int:
        """Advance the dealer button to the next position."""
        return (current_index + 1) % num_players
