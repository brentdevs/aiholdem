"""Unit tests for the Evaluator — one known example per hand type, kicker and tie cases."""

import pytest

from app.game.evaluator import Evaluator
from app.game.models import Card, HandRank


@pytest.fixture
def ev():
    return Evaluator()


def c(rank, suit):
    return Card(rank=rank, suit=suit)


# ---------------------------------------------------------------------------
# best_hand — one known example per hand type
# ---------------------------------------------------------------------------


def test_royal_flush(ev):
    cards = [c(14, "S"), c(13, "S"), c(12, "S"), c(11, "S"), c(10, "S"), c(2, "H"), c(3, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.ROYAL_FLUSH


def test_straight_flush(ev):
    cards = [c(9, "H"), c(8, "H"), c(7, "H"), c(6, "H"), c(5, "H"), c(2, "S"), c(3, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.STRAIGHT_FLUSH
    assert result.tiebreakers == [9]


def test_four_of_a_kind(ev):
    cards = [c(7, "S"), c(7, "H"), c(7, "D"), c(7, "C"), c(14, "S"), c(2, "H"), c(3, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.FOUR_OF_A_KIND
    assert result.tiebreakers[0] == 7


def test_full_house(ev):
    cards = [c(10, "S"), c(10, "H"), c(10, "D"), c(6, "C"), c(6, "S"), c(2, "H"), c(3, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.FULL_HOUSE
    assert result.tiebreakers == [10, 6]


def test_flush(ev):
    cards = [c(14, "S"), c(10, "S"), c(7, "S"), c(4, "S"), c(2, "S"), c(3, "H"), c(5, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.FLUSH


def test_straight(ev):
    cards = [c(9, "S"), c(8, "H"), c(7, "D"), c(6, "C"), c(5, "S"), c(2, "H"), c(3, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.STRAIGHT
    assert result.tiebreakers == [9]


def test_ace_low_straight(ev):
    cards = [c(14, "S"), c(2, "H"), c(3, "D"), c(4, "C"), c(5, "S"), c(9, "H"), c(10, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.STRAIGHT
    assert result.tiebreakers == [5]


def test_three_of_a_kind(ev):
    cards = [c(8, "S"), c(8, "H"), c(8, "D"), c(14, "C"), c(10, "S"), c(2, "H"), c(3, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.THREE_OF_A_KIND
    assert result.tiebreakers[0] == 8


def test_two_pair(ev):
    cards = [c(9, "S"), c(9, "H"), c(6, "D"), c(6, "C"), c(14, "S"), c(2, "H"), c(3, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.TWO_PAIR
    assert result.tiebreakers[:2] == [9, 6]


def test_one_pair(ev):
    cards = [c(5, "S"), c(5, "H"), c(14, "D"), c(10, "C"), c(7, "S"), c(2, "H"), c(3, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.ONE_PAIR
    assert result.tiebreakers[0] == 5


def test_high_card(ev):
    cards = [c(14, "S"), c(10, "H"), c(7, "D"), c(5, "C"), c(3, "S"), c(2, "H"), c(9, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.HIGH_CARD
    assert result.tiebreakers[0] == 14


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def test_compare_higher_rank_wins(ev):
    cards_flush = [c(14, "S"), c(10, "S"), c(7, "S"), c(4, "S"), c(2, "S"), c(3, "H"), c(5, "D")]
    cards_straight = [c(9, "S"), c(8, "H"), c(7, "D"), c(6, "C"), c(5, "S"), c(2, "H"), c(3, "D")]
    flush_result = ev.best_hand(cards_flush)
    straight_result = ev.best_hand(cards_straight)
    assert ev.compare(flush_result, straight_result) == 1
    assert ev.compare(straight_result, flush_result) == -1


def test_compare_kicker_breaks_tie(ev):
    # Both one pair of 5s, but different kickers
    cards_a = [c(5, "S"), c(5, "H"), c(14, "D"), c(10, "C"), c(7, "S"), c(2, "H"), c(3, "D")]
    cards_b = [c(5, "D"), c(5, "C"), c(13, "S"), c(10, "H"), c(7, "D"), c(2, "S"), c(3, "H")]
    result_a = ev.best_hand(cards_a)
    result_b = ev.best_hand(cards_b)
    assert ev.compare(result_a, result_b) == 1  # Ace kicker beats King kicker


def test_compare_identical_hands_tie(ev):
    cards_a = [c(14, "S"), c(10, "H"), c(7, "D"), c(5, "C"), c(3, "S"), c(2, "H"), c(9, "D")]
    cards_b = [c(14, "H"), c(10, "D"), c(7, "C"), c(5, "S"), c(3, "H"), c(2, "D"), c(9, "C")]
    result_a = ev.best_hand(cards_a)
    result_b = ev.best_hand(cards_b)
    assert ev.compare(result_a, result_b) == 0


# ---------------------------------------------------------------------------
# best_hand selects best 5 from 7
# ---------------------------------------------------------------------------


def test_best_hand_picks_flush_over_pair(ev):
    # 5 spades available + a pair — should pick flush
    cards = [c(14, "S"), c(10, "S"), c(7, "S"), c(4, "S"), c(2, "S"), c(2, "H"), c(2, "D")]
    result = ev.best_hand(cards)
    assert result.rank == HandRank.FLUSH
