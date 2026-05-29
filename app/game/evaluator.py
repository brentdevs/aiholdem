from __future__ import annotations

from itertools import combinations

from app.game.models import Card, HandRank, HandResult


class Evaluator:
    def best_hand(self, cards: list[Card]) -> HandResult:
        """Return the best 5-card HandResult from 7 input cards."""
        best: HandResult | None = None
        for combo in combinations(cards, 5):
            result = self._evaluate_five(list(combo))
            if best is None or self._compare_results(result, best) > 0:
                best = result
        assert best is not None
        return best

    def compare(self, a: HandResult, b: HandResult) -> int:
        """Return -1, 0, or 1 comparing a to b."""
        return self._compare_results(a, b)

    def rank_players(self, players: list, community: list[Card]) -> list[list]:
        """Return players grouped by hand strength, best group first."""
        evaluated = [(p, self.best_hand(p.hole_cards + community)) for p in players]
        evaluated.sort(key=lambda x: (x[1].rank, x[1].tiebreakers), reverse=True)

        groups: list[list] = []
        for player, result in evaluated:
            if (
                groups
                and self._compare_results(
                    result, self.best_hand(groups[-1][0].hole_cards + community)
                )
                == 0
            ):
                groups[-1].append(player)
            else:
                groups.append([player])
        return groups

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compare_results(self, a: HandResult, b: HandResult) -> int:
        if a.rank != b.rank:
            return 1 if a.rank > b.rank else -1
        for av, bv in zip(a.tiebreakers, b.tiebreakers):
            if av != bv:
                return 1 if av > bv else -1
        return 0

    def _evaluate_five(self, cards: list[Card]) -> HandResult:
        ranks = sorted([c.rank for c in cards], reverse=True)
        suits = [c.suit for c in cards]
        is_flush = len(set(suits)) == 1

        # Check for straight (including ace-low A-2-3-4-5)
        straight_high = self._straight_high(ranks)

        if is_flush and straight_high == 14 and set(ranks) == {10, 11, 12, 13, 14}:
            return HandResult(rank=HandRank.ROYAL_FLUSH, tiebreakers=[], cards=cards)

        if is_flush and straight_high is not None:
            return HandResult(
                rank=HandRank.STRAIGHT_FLUSH, tiebreakers=[straight_high], cards=cards
            )

        rank_counts = self._rank_counts(ranks)
        counts = sorted(rank_counts.items(), key=lambda x: (x[1], x[0]), reverse=True)

        if counts[0][1] == 4:
            quad_rank = counts[0][0]
            kicker = counts[1][0]
            return HandResult(
                rank=HandRank.FOUR_OF_A_KIND, tiebreakers=[quad_rank, kicker], cards=cards
            )

        if counts[0][1] == 3 and counts[1][1] == 2:
            trip_rank = counts[0][0]
            pair_rank = counts[1][0]
            return HandResult(
                rank=HandRank.FULL_HOUSE, tiebreakers=[trip_rank, pair_rank], cards=cards
            )

        if is_flush:
            return HandResult(rank=HandRank.FLUSH, tiebreakers=ranks, cards=cards)

        if straight_high is not None:
            return HandResult(rank=HandRank.STRAIGHT, tiebreakers=[straight_high], cards=cards)

        if counts[0][1] == 3:
            trip_rank = counts[0][0]
            kickers = sorted([r for r, c in counts if c == 1], reverse=True)
            return HandResult(
                rank=HandRank.THREE_OF_A_KIND, tiebreakers=[trip_rank] + kickers, cards=cards
            )

        if counts[0][1] == 2 and counts[1][1] == 2:
            high_pair = max(counts[0][0], counts[1][0])
            low_pair = min(counts[0][0], counts[1][0])
            kicker = counts[2][0]
            return HandResult(
                rank=HandRank.TWO_PAIR, tiebreakers=[high_pair, low_pair, kicker], cards=cards
            )

        if counts[0][1] == 2:
            pair_rank = counts[0][0]
            kickers = sorted([r for r, c in counts if c == 1], reverse=True)
            return HandResult(
                rank=HandRank.ONE_PAIR, tiebreakers=[pair_rank] + kickers, cards=cards
            )

        return HandResult(rank=HandRank.HIGH_CARD, tiebreakers=ranks, cards=cards)

    def _rank_counts(self, ranks: list[int]) -> dict[int, int]:
        counts: dict[int, int] = {}
        for r in ranks:
            counts[r] = counts.get(r, 0) + 1
        return counts

    def _straight_high(self, ranks: list[int]) -> int | None:
        """Return the high card of a straight, or None. Handles ace-low."""
        unique = sorted(set(ranks), reverse=True)
        if len(unique) < 5:
            return None
        # Normal straight: 5 consecutive
        if unique[0] - unique[4] == 4:
            return unique[0]
        # Ace-low straight: A-2-3-4-5
        if set(unique) == {14, 2, 3, 4, 5}:
            return 5
        return None
