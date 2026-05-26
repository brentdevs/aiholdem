"""Unit tests for model registry and arena lineup configuration."""
from __future__ import annotations

import pytest

from app.ai.model_config import (
    DEFAULT_ARENA_PLAYER_CONFIGS,
    OLLAMA_BACKEND,
    OPENROUTER_BACKEND,
    arena_players_env_value,
    get_supported_model_configs,
    load_arena_player_configs,
)
from app.ai.ollama_player import SUPPORTED_OLLAMA_MODELS


def test_default_lineup_matches_current_ollama_models():
    configs = load_arena_player_configs(raw_value="")

    assert [(c.backend, c.model) for c in configs] == list(DEFAULT_ARENA_PLAYER_CONFIGS)
    assert [c.model for c in configs] == SUPPORTED_OLLAMA_MODELS
    assert all(c.backend == OLLAMA_BACKEND for c in configs)


def test_mixed_provider_lineup_parses_and_preserves_order():
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
    with pytest.raises(ValueError, match="Unsupported ARENA_PLAYERS model"):
        load_arena_player_configs(raw_value="ollama:not-a-model")


def test_duplicate_arena_entry_is_rejected():
    with pytest.raises(ValueError, match="Duplicate ARENA_PLAYERS entry"):
        load_arena_player_configs(
            raw_value="ollama:deepseek-v4-pro,ollama:deepseek-v4-pro"
        )


def test_supported_model_configs_are_deduped_by_backend_and_model():
    configs = get_supported_model_configs()
    keys = [(c.backend, c.model) for c in configs]

    assert len(keys) == len(set(keys))


def test_arena_players_env_value_round_trips():
    configs = load_arena_player_configs(
        raw_value="ollama:kimi-k2.6,openrouter:google/gemini-2.5-flash"
    )

    assert arena_players_env_value(configs) == (
        "ollama:kimi-k2.6,openrouter:google/gemini-2.5-flash"
    )
