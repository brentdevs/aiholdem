from __future__ import annotations

from app.game.models import Pot


class PotManager:
    def __init__(self) -> None:
        self.contributions: dict[str, int] = {}
        self.current_bet: int = 0
        self.min_raise: int = 0

    def post_blind(self, player, amount: int) -> None:
        """Deduct blind from player chips (capped at their stack) and record contribution."""
        actual = min(amount, player.chips)
        player.chips -= actual
        self.contributions[player.player_id] = self.contributions.get(player.player_id, 0) + actual
        if self.contributions[player.player_id] > self.current_bet:
            self.current_bet = self.contributions[player.player_id]

    def place_bet(self, player, amount: int) -> None:
        """Record a bet where amount is the TOTAL the player is putting in this round."""
        player.chips -= amount
        self.contributions[player.player_id] = self.contributions.get(player.player_id, 0) + amount
        if self.contributions[player.player_id] > self.current_bet:
            self.current_bet = self.contributions[player.player_id]

    def calculate_side_pots(self, all_players: list) -> list[Pot]:
        """Build Pot list from contributions, respecting all-in caps."""
        # Only consider players who contributed something
        active_contributions = {pid: amt for pid, amt in self.contributions.items() if amt > 0}
        if not active_contributions:
            return []

        # Sort unique contribution levels ascending
        levels = sorted(set(active_contributions.values()))

        pots: list[Pot] = []
        prev_level = 0

        for level in levels:
            # Players eligible for this pot: contributed >= level
            eligible = [pid for pid, amt in active_contributions.items() if amt >= level]
            increment = level - prev_level
            pot_amount = increment * len(eligible)
            if pot_amount > 0:
                pots.append(Pot(amount=pot_amount, eligible_players=eligible))
            prev_level = level

        return pots

    def distribute(self, ranked_groups: list[list], all_players: list) -> dict[str, int]:
        """
        Award each pot to the highest-ranked eligible player(s).
        ranked_groups: list of lists of players, best hand first.
        Returns dict of player_id -> chips_won, and updates player.chips.
        """
        pots = self.calculate_side_pots(all_players)
        winnings: dict[str, int] = {}

        for pot in pots:
            eligible_set = set(pot.eligible_players)
            # Find the first (best) ranked group that has at least one eligible player
            winners: list = []
            for group in ranked_groups:
                candidates = [p for p in group if p.player_id in eligible_set]
                if candidates:
                    winners = candidates
                    break

            if not winners:
                continue

            share = pot.amount // len(winners)
            remainder = pot.amount % len(winners)

            for i, winner in enumerate(winners):
                award = share + (remainder if i == 0 else 0)
                winnings[winner.player_id] = winnings.get(winner.player_id, 0) + award
                winner.chips += award

        return winnings

    def reset(self) -> None:
        """Reset state for a new hand."""
        self.contributions = {}
        self.current_bet = 0
        self.min_raise = 0
