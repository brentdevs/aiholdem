from hypothesis import strategies as st
from hypothesis.strategies import composite

from app.game.models import Action, ActionType, Card

SUITS = ["S", "H", "D", "C"]
RANKS = list(range(2, 15))


@composite
def card_strategy(draw) -> Card:
    """Generate a single valid Card."""
    rank = draw(st.sampled_from(RANKS))
    suit = draw(st.sampled_from(SUITS))
    return Card(rank=rank, suit=suit)


@composite
def unique_card_list(draw, min_size=5, max_size=7) -> list[Card]:
    """Generate a list of unique Cards (no duplicate rank+suit combos)."""
    full_deck = [Card(rank=r, suit=s) for r in RANKS for s in SUITS]
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    return draw(
        st.lists(
            st.sampled_from(full_deck),
            min_size=size,
            max_size=size,
            unique=True,
        )
    )


@composite
def seven_card_hand(draw) -> list[Card]:
    """Generate exactly 7 unique cards (for evaluator tests)."""
    return draw(unique_card_list(min_size=7, max_size=7))


@composite
def action_strategy(draw) -> Action:
    """Generate a random Action."""
    action_type = draw(st.sampled_from(list(ActionType)))
    amount = draw(st.integers(min_value=0, max_value=10000))
    return Action(type=action_type, amount=amount)
