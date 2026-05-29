import pytest

from app.game.evaluator import Evaluator


@pytest.fixture
def evaluator() -> Evaluator:
    """Return a fresh Evaluator instance."""
    return Evaluator()
