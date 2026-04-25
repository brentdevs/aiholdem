"""Unit tests for PotManager — blind posting, side pots, distribution."""
import pytest
from app.game.pot_manager import PotManager
from app.game.players import Player


@pytest.fixture
def pm():
    return PotManager()


def make_player(pid, chips):
    return Player(player_id=pid, name=f"Player{pid}", chips=chips)


# ---------------------------------------------------------------------------
# Blind posting
# ---------------------------------------------------------------------------

def test_post_blind_deducts_chips(pm):
    p = make_player("p1", 1000)
    pm.post_blind(p, 10)
    assert p.chips == 990
    assert pm.contributions["p1"] == 10


def test_post_blind_caps_at_stack(pm):
    p = make_player("p1", 5)
    pm.post_blind(p, 10)
    assert p.chips == 0
    assert pm.contributions["p1"] == 5


# ---------------------------------------------------------------------------
# Side pot creation
# ---------------------------------------------------------------------------

def test_side_pot_single_all_in(pm):
    p1 = make_player("p1", 1000)
    p2 = make_player("p2", 1000)
    p3 = make_player("p3", 50)

    pm.place_bet(p1, 100)
    pm.place_bet(p2, 100)
    pm.place_bet(p3, 50)

    pots = pm.calculate_side_pots([p1, p2, p3])
    # Main pot: 50*3 = 150 (all three eligible)
    # Side pot: 50*2 = 100 (p1, p2 eligible)
    assert len(pots) == 2
    assert pots[0].amount == 150
    assert set(pots[0].eligible_players) == {"p1", "p2", "p3"}
    assert pots[1].amount == 100
    assert set(pots[1].eligible_players) == {"p1", "p2"}


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------

def test_distribute_single_winner(pm):
    # Players start at 1000; place_bet deducts chips
    p1 = make_player("p1", 1000)
    p2 = make_player("p2", 1000)

    pm.place_bet(p1, 100)  # p1 now has 900
    pm.place_bet(p2, 100)  # p2 now has 900

    # p1 wins the 200-chip pot
    ranked_groups = [[p1], [p2]]
    winnings = pm.distribute(ranked_groups, [p1, p2])

    assert winnings["p1"] == 200
    assert p1.chips == 1100  # 900 + 200


def test_distribute_split_pot(pm):
    p1 = make_player("p1", 1000)
    p2 = make_player("p2", 1000)

    pm.place_bet(p1, 100)  # p1 now has 900
    pm.place_bet(p2, 100)  # p2 now has 900

    # Tie — each gets their 100 back
    ranked_groups = [[p1, p2]]
    winnings = pm.distribute(ranked_groups, [p1, p2])

    assert winnings["p1"] == 100
    assert winnings["p2"] == 100
    assert p1.chips == 1000  # 900 + 100
    assert p2.chips == 1000


def test_distribute_side_pot_winner(pm):
    p1 = make_player("p1", 1000)
    p2 = make_player("p2", 1000)
    p3 = make_player("p3", 50)

    pm.place_bet(p1, 50)   # p1: 950
    pm.place_bet(p2, 50)   # p2: 950
    pm.place_bet(p3, 50)   # p3: 0

    # All contributed 50 — single pot of 150, all eligible
    # p1 has best hand
    ranked_groups = [[p1], [p2], [p3]]
    winnings = pm.distribute(ranked_groups, [p1, p2, p3])

    assert winnings["p1"] == 150
    assert p1.chips == 1100  # 950 + 150
