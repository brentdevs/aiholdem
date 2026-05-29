"""Unit tests for OllamaCloudPlayer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.ai.ollama_player import (
    OLLAMA_CLOUD_HOST,
    SUPPORTED_OLLAMA_MODELS,
    OllamaCloudPlayer,
    _derive_ollama_display_name,
    _response_content,
)
from app.game.models import Action, ActionType


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


def _make_player(model=SUPPORTED_OLLAMA_MODELS[0]) -> OllamaCloudPlayer:
    return OllamaCloudPlayer(player_id="p1", chips=1000, model=model)


def test_provider_field():
    player = _make_player()
    assert player.provider == "ollama"


def test_default_model():
    player = OllamaCloudPlayer(player_id="p1", chips=1000)
    assert player.model == SUPPORTED_OLLAMA_MODELS[0]


def test_supported_models_use_direct_cloud_api_tags():
    assert SUPPORTED_OLLAMA_MODELS == [
        "deepseek-v4-pro",
        "kimi-k2.6",
        "glm-5.1",
        "minimax-m2.7",
        "gemma4:31b",
        "nemotron-3-super",
        "mistral-large-3:675b",
    ]


def test_default_name_strips_cloud_suffix():
    player = OllamaCloudPlayer(player_id="p1", chips=1000, model="kimi-k2.6:cloud")
    assert player.name == "Kimi-k2.6"


def test_derive_display_name_strips_dash_cloud_suffix():
    assert _derive_ollama_display_name("gemma4:31b-cloud") == "Gemma4:31b"


def test_explicit_name_overrides_derived_name():
    player = OllamaCloudPlayer(
        player_id="p1",
        chips=1000,
        model="deepseek-v4-pro:cloud",
        name="DeepSeek V4 Pro",
    )
    assert player.name == "DeepSeek V4 Pro"


def test_response_content_supports_dict_response():
    assert _response_content({"message": {"content": "check"}}) == "check"


def test_response_content_supports_object_response():
    response = MagicMock()
    response.message.content = "fold"
    assert _response_content(response) == "fold"


def test_decide_action_uses_ollama_cloud_client_and_model(monkeypatch):
    player = _make_player(model=SUPPORTED_OLLAMA_MODELS[2])
    game_state = _base_game_state()
    valid = [Action(type=ActionType.FOLD), Action(type=ActionType.CHECK)]
    mock_client = MagicMock()
    mock_client.chat.return_value = {
        "message": {
            "content": '{"action": "check", "amount": null, "reasoning": "No bet to call."}'
        }
    }

    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    with patch(
        "app.ai.ollama_player._create_ollama_client", return_value=mock_client
    ) as create_client:
        action, reasoning = player.decide_action(game_state, valid)

    create_client.assert_called_once_with("test-key")
    mock_client.chat.assert_called_once()
    call_kwargs = mock_client.chat.call_args.kwargs
    assert call_kwargs["model"] == SUPPORTED_OLLAMA_MODELS[2]
    assert call_kwargs["stream"] is False
    assert call_kwargs["think"] is False
    assert action.type == ActionType.CHECK
    assert reasoning == "No bet to call."


def test_ollama_cloud_host_points_at_ollama_api():
    assert OLLAMA_CLOUD_HOST == "https://ollama.com"
