"""Unit tests for OpenRouterPlayer and helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.ai.openrouter_player import (
    SUPPORTED_MODELS,
    OpenRouterPlayer,
    _build_prompt,
    _derive_display_name,
    _extract_reasoning,
    _parse_action,
    _parse_json_response,
    _serialize_history,
)
from app.game.models import Action, ActionType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_player(model=SUPPORTED_MODELS[0]) -> OpenRouterPlayer:
    return OpenRouterPlayer(player_id="p1", chips=1000, model=model)


def _valid_actions_all() -> list[Action]:
    return [
        Action(type=ActionType.FOLD),
        Action(type=ActionType.CHECK),
        Action(type=ActionType.CALL, amount=20),
        Action(type=ActionType.RAISE, amount=40),
        Action(type=ActionType.ALL_IN, amount=1000),
    ]


def _mock_client(response_text: str):
    """Return a context-manager mock that yields a client returning response_text."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = response_text

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.chat.send.return_value = mock_response
    return mock_client


# ---------------------------------------------------------------------------
# OpenRouterPlayer basics
# ---------------------------------------------------------------------------


def test_provider_field():
    player = _make_player()
    assert player.provider == "openrouter"


def test_default_model():
    player = OpenRouterPlayer(player_id="p1", chips=1000)
    assert player.model == SUPPORTED_MODELS[0]


def test_two_players_retain_distinct_models():
    p1 = OpenRouterPlayer(player_id="p1", chips=1000, model=SUPPORTED_MODELS[0])
    p2 = OpenRouterPlayer(player_id="p2", chips=1000, model=SUPPORTED_MODELS[1])
    assert p1.model == SUPPORTED_MODELS[0]
    assert p2.model == SUPPORTED_MODELS[1]


def test_explicit_name_overrides_derived():
    player = OpenRouterPlayer(player_id="p1", chips=1000, model=SUPPORTED_MODELS[0], name="Bot1")
    assert player.name == "Bot1"


# ---------------------------------------------------------------------------
# _derive_display_name
# ---------------------------------------------------------------------------


def test_derive_display_name_free():
    assert _derive_display_name("qwen/qwen3.6-plus:free") == "Qwen3.6-plus (free)"


def test_derive_display_name_no_free():
    assert _derive_display_name("x-ai/grok-4.1-fast") == "Grok-4.1-fast"


def test_derive_display_name_no_provider():
    assert _derive_display_name("somemodel") == "Somemodel"


# ---------------------------------------------------------------------------
# _serialize_history
# ---------------------------------------------------------------------------


def test_serialize_history_empty():
    assert _serialize_history([], {}) == ""


def test_serialize_history_single_phase():
    history = [("pre-flop", "Alice", "R", 200), ("pre-flop", "Bob", "C", None)]
    name_to_label = {"Alice": "P1", "Bob": "P2"}
    result = _serialize_history(history, name_to_label)
    assert result == "pre-flop: P1 R200, P2 C"


def test_serialize_history_multi_phase():
    history = [
        ("pre-flop", "Alice", "R", 200),
        ("pre-flop", "Bob", "C", None),
        ("flop", "Alice", "X", None),
    ]
    name_to_label = {"Alice": "P1", "Bob": "P2"}
    result = _serialize_history(history, name_to_label)
    assert result == "pre-flop: P1 R200, P2 C | flop: P1 X"


def test_serialize_history_all_in_includes_amount():
    history = [("turn", "Alice", "A", 500)]
    name_to_label = {"Alice": "P1"}
    result = _serialize_history(history, name_to_label)
    assert "A500" in result


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------


def _base_game_state(**kwargs) -> dict:
    state = {
        "community_cards": [],
        "pot": 100,
        "players": [{"player_id": "p1", "name": "Alice", "chips": 900}],
        "position": 1,
        "min_raise": 20,
        "hand_history": [],
    }
    state.update(kwargs)
    return state


def test_build_prompt_includes_glossary():
    prompt = _build_prompt(_base_game_state(), [Action(type=ActionType.FOLD)], [], "p1")
    assert "Action codes:" in prompt
    assert "F=fold" in prompt


def test_build_prompt_omits_history_when_empty():
    prompt = _build_prompt(_base_game_state(), [Action(type=ActionType.FOLD)], [], "p1")
    assert "History:" not in prompt


def test_build_prompt_includes_history_when_present():
    history = [("PF", "Alice", "R", 200)]
    prompt = _build_prompt(_base_game_state(), [Action(type=ActionType.FOLD)], history, "p1")
    assert "History:" in prompt
    assert "R200" in prompt


def test_build_prompt_includes_pot_and_position():
    prompt = _build_prompt(
        _base_game_state(pot=350, position=3), [Action(type=ActionType.FOLD)], [], "p1"
    )
    assert "350" in prompt
    assert "3" in prompt


# ---------------------------------------------------------------------------
# _extract_reasoning
# ---------------------------------------------------------------------------


def test_extract_reasoning_returns_text_before_keyword():
    result = _extract_reasoning("I have a strong hand. raise", "raise")
    assert result == "I have a strong hand."


def test_extract_reasoning_empty_string_returns_fallback():
    assert _extract_reasoning("", "fold") == "No reasoning provided"


def test_extract_reasoning_no_text_before_keyword_returns_fallback():
    assert _extract_reasoning("fold", "fold") == "No reasoning provided"


def test_extract_reasoning_whitespace_only_before_keyword_returns_fallback():
    assert _extract_reasoning("   check", "check") == "No reasoning provided"


def test_extract_reasoning_multi_sentence_reasoning():
    text = "I have pocket aces. The pot is large. raise 200"
    result = _extract_reasoning(text, "raise")
    assert result == "I have pocket aces. The pot is large."


def test_extract_reasoning_keyword_mid_sentence_splits_on_first():
    # keyword appears in reasoning text too — splits on first occurrence
    text = "I might raise but I'll fold"
    result = _extract_reasoning(text, "fold")
    assert result == "I might raise but I'll"


# ---------------------------------------------------------------------------
# _parse_action
# ---------------------------------------------------------------------------


def test_parse_action_fold():
    valid = [Action(type=ActionType.FOLD)]
    result = _parse_action("I will fold", valid, 20)
    assert result is not None
    assert result.type == ActionType.FOLD


def test_parse_action_check():
    valid = [Action(type=ActionType.CHECK)]
    result = _parse_action("check please", valid, 20)
    assert result is not None
    assert result.type == ActionType.CHECK


def test_parse_action_call():
    valid = [Action(type=ActionType.CALL, amount=20)]
    result = _parse_action("I call", valid, 20)
    assert result is not None
    assert result.type == ActionType.CALL


def test_parse_action_raise_with_amount():
    valid = [Action(type=ActionType.RAISE, amount=40)]
    result = _parse_action("raise 80", valid, 20)
    assert result is not None
    assert result.type == ActionType.RAISE
    assert result.amount == 80


def test_parse_action_raise_allows_total_above_remaining_chips_when_already_in():
    valid = [
        Action(type=ActionType.RAISE, amount=300),
        Action(type=ActionType.ALL_IN, amount=250),
    ]
    result = _parse_action(
        "raise 300",
        valid,
        100,
        player_chips=250,
        player_round_contrib=100,
    )
    assert result is not None
    assert result.type == ActionType.RAISE
    assert result.amount == 300


def test_parse_action_raise_converts_to_all_in_when_total_exceeds_stack_plus_already_in():
    valid = [
        Action(type=ActionType.RAISE, amount=300),
        Action(type=ActionType.ALL_IN, amount=250),
    ]
    result = _parse_action(
        "raise 400",
        valid,
        100,
        player_chips=250,
        player_round_contrib=100,
    )
    assert result is not None
    assert result.type == ActionType.ALL_IN
    assert result.amount == 250


def test_parse_action_raise_defaults_to_min_raise():
    valid = [Action(type=ActionType.RAISE, amount=40)]
    result = _parse_action("raise", valid, 20)
    assert result is not None
    assert result.type == ActionType.RAISE
    assert result.amount == 20


def test_parse_action_all_in():
    valid = [Action(type=ActionType.ALL_IN, amount=1000)]
    result = _parse_action("all_in", valid, 20)
    assert result is not None
    assert result.type == ActionType.ALL_IN


def test_parse_action_all_in_hyphen():
    valid = [Action(type=ActionType.ALL_IN, amount=1000)]
    result = _parse_action("all-in", valid, 20)
    assert result is not None
    assert result.type == ActionType.ALL_IN


def test_parse_action_no_match_returns_none():
    valid = _valid_actions_all()
    result = _parse_action("I have no idea what to do", valid, 20)
    assert result is None


def test_parse_json_response_raise_allows_total_above_remaining_chips_when_already_in():
    valid = [
        Action(type=ActionType.RAISE, amount=300),
        Action(type=ActionType.ALL_IN, amount=250),
    ]
    result = _parse_json_response(
        '{"action": "raise", "amount": 300, "reasoning": "Pressure the field."}',
        valid,
        100,
        player_chips=250,
        player_round_contrib=100,
    )
    assert result is not None
    action, reasoning = result
    assert action.type == ActionType.RAISE
    assert action.amount == 300
    assert reasoning == "Pressure the field."


def test_parse_json_response_raise_converts_to_all_in_when_total_exceeds_stack_plus_already_in():
    valid = [
        Action(type=ActionType.RAISE, amount=300),
        Action(type=ActionType.ALL_IN, amount=250),
    ]
    result = _parse_json_response(
        '{"action": "raise", "amount": 400, "reasoning": "Maximum pressure."}',
        valid,
        100,
        player_chips=250,
        player_round_contrib=100,
    )
    assert result is not None
    action, reasoning = result
    assert action.type == ActionType.ALL_IN
    assert action.amount == 250
    assert reasoning == "Maximum pressure."


# ---------------------------------------------------------------------------
# decide_action — normal path
# ---------------------------------------------------------------------------


def test_decide_action_uses_correct_model():
    player = _make_player(model=SUPPORTED_MODELS[2])
    game_state = _base_game_state()
    valid = [Action(type=ActionType.FOLD), Action(type=ActionType.CHECK)]

    with patch("app.ai.openrouter_player.OpenRouter") as mock_cls:
        mock_client = _mock_client("check")
        mock_cls.return_value = mock_client
        player.decide_action(game_state, valid)
        mock_client.chat.send.assert_called_once()
        call_kwargs = mock_client.chat.send.call_args.kwargs
        assert call_kwargs.get("models") == [SUPPORTED_MODELS[2]]


def test_decide_action_returns_parsed_action():
    player = _make_player()
    game_state = _base_game_state()
    valid = [Action(type=ActionType.FOLD), Action(type=ActionType.CHECK)]

    with patch("app.ai.openrouter_player.OpenRouter") as mock_cls:
        mock_cls.return_value = _mock_client("check")
        result, reasoning = player.decide_action(game_state, valid)

    assert result.type == ActionType.CHECK


# ---------------------------------------------------------------------------
# decide_action — error paths
# ---------------------------------------------------------------------------


def test_decide_action_check_on_exception_when_available():
    player = _make_player()
    game_state = _base_game_state()
    valid = [Action(type=ActionType.FOLD), Action(type=ActionType.CHECK)]

    with patch("app.ai.openrouter_player.OpenRouter") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.chat.send.side_effect = Exception("API down")
        mock_cls.return_value = mock_client
        result, reasoning = player.decide_action(game_state, valid)

    assert result.type == ActionType.CHECK


def test_decide_action_fold_on_exception_no_check():
    player = _make_player()
    game_state = _base_game_state()
    valid = [Action(type=ActionType.FOLD)]

    with patch("app.ai.openrouter_player.OpenRouter") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.chat.send.side_effect = Exception("API down")
        mock_cls.return_value = mock_client
        result, reasoning = player.decide_action(game_state, valid)

    assert result.type == ActionType.FOLD


def test_decide_action_check_on_unparseable():
    player = _make_player()
    game_state = _base_game_state()
    valid = [Action(type=ActionType.FOLD), Action(type=ActionType.CHECK)]

    with patch("app.ai.openrouter_player.OpenRouter") as mock_cls:
        mock_cls.return_value = _mock_client("I have no idea what to do here")
        result, reasoning = player.decide_action(game_state, valid)

    assert result.type == ActionType.CHECK


def test_decide_action_fold_on_unparseable_no_check():
    player = _make_player()
    game_state = _base_game_state()
    valid = [Action(type=ActionType.FOLD)]  # no check available

    with patch("app.ai.openrouter_player.OpenRouter") as mock_cls:
        mock_cls.return_value = _mock_client("I have no idea what to do here")
        result, reasoning = player.decide_action(game_state, valid)

    assert result.type == ActionType.FOLD


# ---------------------------------------------------------------------------
# decide_action — tuple return type (task 2.7)
# ---------------------------------------------------------------------------


def test_decide_action_returns_tuple():
    player = _make_player()
    game_state = _base_game_state()
    valid = [Action(type=ActionType.FOLD), Action(type=ActionType.CHECK)]

    with patch("app.ai.openrouter_player.OpenRouter") as mock_cls:
        mock_cls.return_value = _mock_client("check")
        result = player.decide_action(game_state, valid)

    assert isinstance(result, tuple)
    assert len(result) == 2


def test_decide_action_error_fallback_reasoning():
    player = _make_player()
    game_state = _base_game_state()
    valid = [Action(type=ActionType.FOLD), Action(type=ActionType.CHECK)]

    with patch("app.ai.openrouter_player.OpenRouter") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.chat.send.side_effect = Exception("API down")
        mock_cls.return_value = mock_client
        _, reasoning = player.decide_action(game_state, valid)

    assert reasoning == "API error \u2014 defaulted to check/fold"


def test_llm_called_once_per_decide_action():
    player = _make_player()
    game_state = _base_game_state()
    valid = [Action(type=ActionType.FOLD), Action(type=ActionType.CHECK)]

    with patch("app.ai.openrouter_player.OpenRouter") as mock_cls:
        mock_client = _mock_client("check")
        mock_cls.return_value = mock_client
        player.decide_action(game_state, valid)

    assert mock_client.chat.send.call_count == 1
