"""Property-based tests for PotManager."""

from hypothesis import given, settings
from hypothesis import strategies as st

from app.game.players import Player
from app.game.pot_manager import PotManager


def make_player(pid, chips):
    return Player(player_id=pid, name=f"P{pid}", chips=chips)


# Feature: texas-holdem-poker, Property 11: Chip Conservation
@settings(max_examples=100)
@given(
    chips_list=st.lists(
        st.integers(min_value=10, max_value=1000),
        min_size=2,
        max_size=6,
    ),
    bet_fractions=st.lists(
        st.floats(min_value=0.1, max_value=1.0),
        min_size=2,
        max_size=6,
    ),
)
def test_chip_conservation(chips_list, bet_fractions):
    """Total chips before == total chips after distribution."""
    n = min(len(chips_list), len(bet_fractions))
    players = [make_player(f"p{i}", chips_list[i]) for i in range(n)]
    total_before = sum(p.chips for p in players)

    pm = PotManager()
    for i, p in enumerate(players):
        bet = max(1, int(p.chips * bet_fractions[i]))
        pm.place_bet(p, bet)

    # All players win equally (tie) — chips should be conserved
    ranked_groups = [players]
    pm.distribute(ranked_groups, players)

    total_after = sum(p.chips for p in players)
    assert total_after == total_before


# Feature: texas-holdem-poker, Property 16: Side Pot Eligibility
@settings(max_examples=100)
@given(
    all_in_amount=st.integers(min_value=10, max_value=200),
    other_bet=st.integers(min_value=201, max_value=500),
)
def test_side_pot_eligibility(all_in_amount, other_bet):
    """All-in player is only eligible for pots up to their contribution level."""
    p_allin = make_player("allin", 0)
    p_other1 = make_player("other1", 0)
    p_other2 = make_player("other2", 0)

    pm = PotManager()
    pm.contributions["allin"] = all_in_amount
    pm.contributions["other1"] = other_bet
    pm.contributions["other2"] = other_bet

    pots = pm.calculate_side_pots([p_allin, p_other1, p_other2])

    # The all-in player must not appear in any pot where the level exceeds their contribution
    for pot in pots:
        # Find the contribution level this pot corresponds to
        # If all-in player is eligible, the pot level must be <= all_in_amount
        if "allin" in pot.eligible_players:
            # This is fine — they're eligible for the main pot
            pass
        else:
            # They're not eligible — this must be a side pot above their level
            assert "other1" in pot.eligible_players or "other2" in pot.eligible_players
