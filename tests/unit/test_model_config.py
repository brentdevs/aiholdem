"""Unit tests for model registry and arena lineup configuration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.ai.model_config import (
    OLLAMA_BACKEND,
    OPENROUTER_BACKEND,
    _validate_arena_entries,
    arena_players_env_value,
    load_arena_player_configs,
)


@pytest.fixture(autouse=True)
def mock_available_models():
    """Mock the provider API calls so tests don't need real credentials."""
    models = {
        OPENROUTER_BACKEND: {"google/gemini-2.5-flash", "openai/gpt-4o-mini"},
        OLLAMA_BACKEND: {"deepseek-v4-pro", "kimi-k2.6", "qwen3.5:cloud"},
    }
    with patch("app.ai.model_config._get_available_models", return_value=models):
        yield


def test_valid_lineup_parses():
    configs = load_arena_player_configs(
        raw_value="ollama:deepseek-v4-pro,openrouter:google/gemini-2.5-flash"
    )

    assert [(c.backend, c.model) for c in configs] == [
        (OLLAMA_BACKEND, "deepseek-v4-pro"),
        (OPENROUTER_BACKEND, "google/gemini-2.5-flash"),
    ]
    assert configs[0].display_name
    assert configs[1].display_name


def test_invalid_backend_fails_fast():
    with pytest.raises(ValueError, match="Unsupported ARENA_PLAYERS backend"):
        load_arena_player_configs(raw_value="bad:deepseek-v4-pro")


def test_invalid_model_fails_fast():
    with pytest.raises(ValueError, match="not found on"):
        load_arena_player_configs(raw_value="ollama:not-a-model")


def test_duplicate_arena_entry_is_rejected():
    with pytest.raises(ValueError, match="Duplicate ARENA_PLAYERS entry"):
        load_arena_player_configs(raw_value="ollama:deepseek-v4-pro,ollama:deepseek-v4-pro")


def test_empty_arena_players_raises():
    with pytest.raises(ValueError, match="ARENA_PLAYERS environment variable must be set"):
        load_arena_player_configs(raw_value="")


def test_arena_players_env_value_round_trips():
    configs = load_arena_player_configs(
        raw_value="ollama:kimi-k2.6,openrouter:google/gemini-2.5-flash"
    )

    assert arena_players_env_value(configs) == "ollama:kimi-k2.6,openrouter:google/gemini-2.5-flash"


def test_model_allowed_when_provider_unavailable():
    """When provider fetch fails (empty set), models are allowed with a warning."""
    empty = {OPENROUTER_BACKEND: set(), OLLAMA_BACKEND: set()}
    with patch("app.ai.model_config._get_available_models", return_value=empty):
        configs = load_arena_player_configs(raw_value="ollama:anything-goes")
    assert configs[0].model == "anything-goes"
