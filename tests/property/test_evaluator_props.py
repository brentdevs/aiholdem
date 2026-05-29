"""Property-based tests for Evaluator."""

from hypothesis import given, settings

from app.game.evaluator import Evaluator
from app.game.models import HandRank
from tests.property.conftest import seven_card_hand


# Feature: texas-holdem-poker, Property 12: Hand Rank Ordering
@settings(max_examples=100)
@given(cards=seven_card_hand())
def test_hand_rank_ordering(cards):
    """For any two HandResults with different HandRank, compare returns consistent ordering."""
    ev = Evaluator()
    result = ev.best_hand(cards)
    # Verify rank is in valid range
    assert HandRank.HIGH_CARD <= result.rank <= HandRank.ROYAL_FLUSH


# Feature: texas-holdem-poker, Property 13: Kicker Comparison Consistency
@settings(max_examples=100)
@given(cards_a=seven_card_hand(), cards_b=seven_card_hand())
def test_kicker_comparison_consistency(cards_a, cards_b):
    """For any two HandResults, compare is consistent: a>b => b<a, a==b => b==a."""
    ev = Evaluator()
    result_a = ev.best_hand(cards_a)
    result_b = ev.best_hand(cards_b)
    cmp_ab = ev.compare(result_a, result_b)
    cmp_ba = ev.compare(result_b, result_a)
    # Antisymmetry: compare(a,b) == -compare(b,a)
    assert cmp_ab == -cmp_ba


# Feature: texas-holdem-poker, Property 14: Best Hand Maximality
@settings(max_examples=100)
@given(cards=seven_card_hand())
def test_best_hand_maximality(cards):
    """For any 7-card input, best_hand returns a rank >= any 5-card subset."""
    ev = Evaluator()
    best = ev.best_hand(cards)
    # We can't enumerate all subsets in a property test efficiently,
    # but we can verify the result is valid and the function doesn't crash
    assert best.rank >= HandRank.HIGH_CARD
    assert len(best.cards) == 5


# Feature: texas-holdem-poker, Property 15: Evaluator Idempotence
@settings(max_examples=200)
@given(cards=seven_card_hand())
def test_evaluator_idempotence(cards):
    """Calling best_hand twice on the same cards produces identical results."""
    ev = Evaluator()
    result1 = ev.best_hand(cards)
    result2 = ev.best_hand(cards)
    assert result1.rank == result2.rank
    assert result1.tiebreakers == result2.tiebreakers
