"""Property-based tests for OpenRouter AI player components."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from hypothesis import assume, given, settings, strategies as st

from app.ai.openrouter_player import (
    SUPPORTED_MODELS,
    OpenRouterPlayer,
    _build_prompt,
    _derive_display_name,
    _extract_reasoning,
    _parse_action,
    _serialize_history,
)
from app.game.models import Action, ActionType

# ---------------------------------------------------------------------------
# Feature: ai-move-review, Property 1: Prompt always contains reasoning instruction
# Validates: Requirements 1.1
# ---------------------------------------------------------------------------

_game_state_strategy = st.fixed_dictionaries({
    "community_cards": st.just([]),
    "hole_cards": st.just([]),
    "pot": st.integers(min_value=0, max_value=10000),
    "players": st.just([]),
    "position": st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
    "min_raise": st.integers(min_value=0, max_value=500),
})

_valid_actions_strategy = st.lists(
    st.sampled_from([Action(type=ActionType.FOLD), Action(type=ActionType.CHECK), Action(type=ActionType.CALL, amount=20)]),
    min_size=1,
    max_size=3,
)


# Feature: ai-move-review, Property 1: Prompt always contains reasoning instruction
@settings(max_examples=100)
@given(game_state=_game_state_strategy, valid_actions=_valid_actions_strategy)
def test_prompt_always_contains_reasoning_instruction(game_state, valid_actions):
    """_build_prompt always includes the JSON response instruction with reasoning field."""
    prompt = _build_prompt(game_state, valid_actions, [], "p1")
    assert '"reasoning"' in prompt

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

phase_abbrs = st.sampled_from(["pre-flop", "flop", "turn", "river"])
action_codes = st.sampled_from(["F", "X", "C", "R", "A"])
player_names = st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("Lu", "Ll")))
amounts = st.one_of(st.none(), st.integers(min_value=1, max_value=1000))

history_entry = st.tuples(phase_abbrs, player_names, action_codes, amounts)
history_list = st.lists(history_entry, min_size=1, max_size=20)


# ---------------------------------------------------------------------------
# Property 7: History Serialization Format
# Validates: Requirements 9.3, 9.4, 9.7, 9.8
# ---------------------------------------------------------------------------

# Feature: openrouter-ai-player, Property 7: History Serialization Format
@settings(max_examples=200)
@given(history=history_list)
def test_history_serialization_format(history):
    """_serialize_history produces correctly structured output."""
    # Build a label map from all names in the history
    names = list(dict.fromkeys(e[1] for e in history))
    name_to_label = {name: f"P{i+1}" for i, name in enumerate(names)}

    result = _serialize_history(history, name_to_label)
    assert isinstance(result, str)
    assert len(result) > 0

    # Groups separated by " | "
    groups = result.split(" | ")
    distinct_phases = list(dict.fromkeys(e[0] for e in history))  # ordered unique phases
    assert len(groups) == len(distinct_phases)

    # Each group starts with the phase abbreviation followed by ": "
    for group, phase in zip(groups, distinct_phases):
        assert group.startswith(f"{phase}: ")

    # RAISE/ALL_IN entries include amount; others do not have a trailing number
    for phase_abbr, player_name, code, amount in history:
        label = name_to_label.get(player_name, player_name)
        if code in ("R", "A") and amount is not None:
            assert f"{code}{amount}" in result


@settings(max_examples=10)
@given(st.just([]))
def test_serialize_history_empty(history):
    assert _serialize_history(history, {}) == ""


# ---------------------------------------------------------------------------
# Property 5: Prompt Completeness
# Validates: Requirements 2.4, 9.9
# ---------------------------------------------------------------------------

# Feature: openrouter-ai-player, Property 5: Prompt Completeness
@settings(max_examples=100)
@given(
    pot=st.integers(min_value=0, max_value=10000),
    position=st.integers(min_value=0, max_value=8),
    min_raise=st.integers(min_value=0, max_value=200),
)
def test_prompt_completeness(pot, position, min_raise):
    """_build_prompt always includes glossary, pot, position, and valid actions."""
    game_state = {
        "community_cards": [],
        "pot": pot,
        "players": [{"player_id": "p1", "name": "Alice", "chips": 500}],
        "position": position,
        "min_raise": min_raise,
        "hand_history": [],
    }
    valid_actions = [Action(type=ActionType.FOLD), Action(type=ActionType.CHECK)]
    prompt = _build_prompt(game_state, valid_actions, [], "p1")

    assert "Action codes:" in prompt
    assert "F=fold" in prompt
    assert str(pot) in prompt
    assert str(position) in prompt
    assert "fold" in prompt
    assert "check" in prompt


@settings(max_examples=50)
@given(st.integers(min_value=0, max_value=100))
def test_prompt_omits_history_when_empty(pot):
    game_state = {"community_cards": [], "pot": pot, "players": [], "position": 0, "min_raise": 0}
    prompt = _build_prompt(game_state, [Action(type=ActionType.FOLD)], [], "p1")
    assert "History:" not in prompt


@settings(max_examples=50)
@given(history=history_list)
def test_prompt_includes_history_when_present(history):
    game_state = {"community_cards": [], "pot": 0, "players": [], "position": 0, "min_raise": 0}
    prompt = _build_prompt(game_state, [Action(type=ActionType.FOLD)], history, "p1")
    assert "History:" in prompt


# ---------------------------------------------------------------------------
# Property 2: Parse Rejection
# Validates: Requirements 3.3, 8.6
# ---------------------------------------------------------------------------

_KEYWORDS = {"fold", "check", "call", "raise", "all_in", "all-in"}


# Feature: openrouter-ai-player, Property 2: Parse Rejection
@settings(max_examples=200)
@given(text=st.text(min_size=1))
def test_parse_rejection(text):
    """_parse_action returns None for text containing no valid action keyword."""
    text_lower = text.lower()
    has_keyword = any(kw in text_lower for kw in _KEYWORDS)
    assume(not has_keyword)

    valid = [
        Action(type=ActionType.FOLD),
        Action(type=ActionType.CHECK),
        Action(type=ActionType.CALL, amount=20),
        Action(type=ActionType.RAISE, amount=40),
        Action(type=ActionType.ALL_IN, amount=1000),
    ]
    result = _parse_action(text, valid, 20)
    assert result is None


# ---------------------------------------------------------------------------
# Property 3: Action Keyword Round-Trip
# Validates: Requirements 2.5, 6.4
# ---------------------------------------------------------------------------

_KEYWORD_MAP = {
    ActionType.FOLD: "fold",
    ActionType.CHECK: "check",
    ActionType.CALL: "call",
    ActionType.RAISE: "raise 100",
    ActionType.ALL_IN: "all_in",
}


# Feature: openrouter-ai-player, Property 3: Action Keyword Round-Trip
@settings(max_examples=200)
@given(action_types=st.lists(st.sampled_from(list(ActionType)), min_size=1, unique=True))
def test_action_keyword_roundtrip(action_types):
    """For each action type, a text containing its keyword parses back to that type."""
    valid = [
        Action(type=t, amount=100 if t in (ActionType.RAISE, ActionType.ALL_IN) else 0)
        for t in action_types
    ]
    for a in valid:
        keyword_text = f"I will {_KEYWORD_MAP[a.type]} now"
        result = _parse_action(keyword_text, valid, 20)
        assert result is not None
        assert result.type == a.type


# ---------------------------------------------------------------------------
# Property 1: Exception Safety
# Validates: Requirements 3.1, 3.4, 8.5
# ---------------------------------------------------------------------------

_EXCEPTION_TYPES = [
    Exception("generic"),
    TimeoutError("timeout"),
    ValueError("bad value"),
    ConnectionError("conn"),
    RuntimeError("runtime"),
    OSError("os error"),
]


# Feature: openrouter-ai-player, Property 1: Exception Safety
@settings(max_examples=100)
@given(exc_index=st.integers(min_value=0, max_value=len(_EXCEPTION_TYPES) - 1))
def test_exception_safety(exc_index):
    """decide_action always returns FOLD and never raises when the API client throws."""
    exc = _EXCEPTION_TYPES[exc_index]
    player = OpenRouterPlayer(player_id="p1", chips=1000, model=SUPPORTED_MODELS[0])
    valid = [Action(type=ActionType.FOLD), Action(type=ActionType.CHECK)]
    game_state = {"session_id": "s1", "min_raise": 20, "hand_history": []}

    with patch("app.ai.openrouter_player.OpenRouter") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.chat.send.side_effect = exc
        mock_cls.return_value = mock_client

        result, reasoning = player.decide_action(game_state, valid)

    assert result.type in (ActionType.FOLD, ActionType.CHECK)


# ---------------------------------------------------------------------------
# Property 4: Model String Independence
# Validates: Requirements 1.2, 1.3
# ---------------------------------------------------------------------------

# Feature: openrouter-ai-player, Property 4: Model String Independence
@settings(max_examples=100)
@given(
    m1=st.text(min_size=1, max_size=30),
    m2=st.text(min_size=1, max_size=30),
)
def test_model_string_independence(m1, m2):
    """Two OpenRouterPlayer instances with different models retain their own model."""
    assume(m1 != m2)
    p1 = OpenRouterPlayer(player_id="p1", chips=1000, model=m1)
    p2 = OpenRouterPlayer(player_id="p2", chips=1000, model=m2)
    assert p1.model == m1
    assert p2.model == m2


# ---------------------------------------------------------------------------
# Property 8: Display Name Derivation
# Validates: Requirements 1.4, 5.4
# ---------------------------------------------------------------------------

# Feature: openrouter-ai-player, Property 8: Display Name Derivation
@settings(max_examples=200)
@given(
    provider=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("Ll",))),
    name=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Ll", "Nd"))),
    free=st.booleans(),
)
def test_display_name_derivation(provider, name, free):
    """_derive_display_name strips provider prefix and handles :free suffix correctly."""
    assume(len(name) > 0)
    model = f"{provider}/{name}:free" if free else f"{provider}/{name}"
    display = _derive_display_name(model)

    # No "/" in display name
    assert "/" not in display
    # Provider prefix not present as a standalone prefix
    assert not display.startswith(f"{provider}/")
    # :free suffix handled correctly
    assert display.endswith(" (free)") == free


# ---------------------------------------------------------------------------
# Feature: ai-move-review, Property 2: Reasoning extraction returns text before action keyword
# Validates: Requirements 1.2, 1.5
# ---------------------------------------------------------------------------

# Feature: ai-move-review, Property 2: Reasoning extraction returns text before action keyword
@settings(max_examples=200)
@given(text=st.text(), keyword=st.sampled_from(["fold", "check", "call", "raise", "all_in"]))
def test_extract_reasoning_returns_text_before_keyword(text, keyword):
    """_extract_reasoning returns text before the action keyword or 'No reasoning provided'."""
    result = _extract_reasoning(text, keyword)

    # Result is always a non-empty string
    assert isinstance(result, str)
    assert len(result) > 0

    if not text:
        # Empty input → fallback
        assert result == "No reasoning provided"
    elif keyword in text:
        before = text.split(keyword, 1)[0].strip()
        if before:
            # Non-empty text before keyword → return that text
            assert result == before
        else:
            # Nothing precedes the keyword → fallback
            assert result == "No reasoning provided"


# ---------------------------------------------------------------------------
# Feature: ai-move-review, Property 3: decide_action returns action and non-empty reasoning
# Validates: Requirements 1.4, 1.3
# ---------------------------------------------------------------------------

# Feature: ai-move-review, Property 3: decide_action returns action and non-empty reasoning
@settings(max_examples=100)
@given(
    game_state=_game_state_strategy,
    valid_actions=_valid_actions_strategy,
    response_text=st.sampled_from(["I have a strong hand. check", "fold", "", "gibberish xyz"]),
)
def test_decide_action_returns_action_and_non_empty_reasoning(game_state, valid_actions, response_text):
    """decide_action always returns a (Action, str) tuple where the str is non-empty."""
    player = OpenRouterPlayer(player_id="p1", chips=1000, model=SUPPORTED_MODELS[0])

    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = response_text
    mock_response.choices = [mock_choice]

    with patch("app.ai.openrouter_player.OpenRouter") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.chat.send.return_value = mock_response
        mock_cls.return_value = mock_client

        result = player.decide_action(game_state, valid_actions)

    # Must be a tuple of length 2
    assert isinstance(result, tuple)
    assert len(result) == 2

    action, reasoning = result

    # First element is an Action instance
    assert isinstance(action, Action)

    # Second element is a non-empty str
    assert isinstance(reasoning, str)
    assert len(reasoning) > 0
