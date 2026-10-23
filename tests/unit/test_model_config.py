"""Unit tests for the YAML arena configuration and model registry."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

import pytest
import yaml

from app.ai import model_config
from app.ai.model_config import (
    OLLAMA_BACKEND,
    OPENROUTER_BACKEND,
    get_supported_model_configs,
    load_lobby_configs,
)


@pytest.fixture(autouse=True)
def mock_available_models(monkeypatch):
    """Keep configuration tests independent of credentials and provider APIs."""
    monkeypatch.delenv("ARENA_CONFIG_FILE", raising=False)
    models = {
        OPENROUTER_BACKEND: {"google/gemini-2.5-flash", "openai/gpt-4o-mini"},
        OLLAMA_BACKEND: {"deepseek-v4-pro", "kimi-k2.6", "qwen3.5:cloud"},
    }
    with patch("app.ai.model_config._get_available_models", return_value=models):
        yield models


@pytest.fixture
def document():
    return {
        "arenas": [
            {
                "id": "stable-table",
                "name": "Main Arena",
                "description": "Balanced table",
                "players": [
                    {"backend": "ollama", "model": "deepseek-v4-pro"},
                    {"backend": "openrouter", "model": "google/gemini-2.5-flash"},
                ],
            }
        ]
    }


@pytest.fixture
def config_file(tmp_path):
    def create(document):
        path = tmp_path / "arenas.yaml"
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        return path

    return create


def test_ordered_lobbies_and_players(document, config_file):
    document["arenas"].append(
        {
            "id": "cloud-table",
            "name": "Cloud Table",
            "description": "Ollama models",
            "players": [
                {"backend": "ollama", "model": "kimi-k2.6"},
                {"backend": "ollama", "model": "deepseek-v4-pro"},
            ],
        }
    )
    configs = load_lobby_configs(config_file(document))

    assert [config.lobby_id for config in configs] == ["stable-table", "cloud-table"]
    assert [config.name for config in configs] == ["Main Arena", "Cloud Table"]
    assert [config.description for config in configs] == ["Balanced table", "Ollama models"]
    assert [[(p.backend, p.model) for p in config.players] for config in configs] == [
        [(OLLAMA_BACKEND, "deepseek-v4-pro"), (OPENROUTER_BACKEND, "google/gemini-2.5-flash")],
        [(OLLAMA_BACKEND, "kimi-k2.6"), (OLLAMA_BACKEND, "deepseek-v4-pro")],
    ]


def test_id_is_independent_of_display_name(document, config_file):
    original = load_lobby_configs(config_file(document))[0]
    document["arenas"][0]["name"] = "Renamed Arena!"
    renamed = load_lobby_configs(config_file(document))[0]
    assert original.lobby_id == renamed.lobby_id == "stable-table"
    assert renamed.name == "Renamed Arena!"


@pytest.mark.parametrize(
    "lobby_id",
    ["Uppercase", "under_score", "two words", "-leading", "trailing-", "two--hyphens", "é", "a/b"],
)
def test_invalid_ids_are_rejected(lobby_id, document, config_file):
    document["arenas"][0]["id"] = lobby_id
    with pytest.raises(ValueError):
        load_lobby_configs(config_file(document))


def test_duplicate_ids_are_rejected(document, config_file):
    duplicate = deepcopy(document["arenas"][0])
    duplicate["name"] = "Different name"
    document["arenas"].append(duplicate)
    with pytest.raises(ValueError):
        load_lobby_configs(config_file(document))


def test_duplicate_players_are_rejected(document, config_file):
    players = document["arenas"][0]["players"]
    players.append(deepcopy(players[0]))
    with pytest.raises(ValueError):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize("field", ["id", "name", "description", "players"])
def test_missing_arena_fields_are_rejected(field, document, config_file):
    del document["arenas"][0][field]
    with pytest.raises(ValueError):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize("field", ["backend", "model"])
def test_missing_player_fields_are_rejected(field, document, config_file):
    del document["arenas"][0]["players"][0][field]
    with pytest.raises(ValueError):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize("field", ["id", "name", "description", "backend", "model"])
@pytest.mark.parametrize("value", [None, "", " \t ", 42, True, [], {}])
def test_string_fields_require_nonempty_strings(field, value, document, config_file):
    arena = document["arenas"][0]
    target = arena["players"][0] if field in {"backend", "model"} else arena
    target[field] = value
    with pytest.raises(ValueError):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        {},
        "arenas",
        1,
        {"arenas": None},
        {"arenas": []},
        {"arenas": {}},
        {"arenas": "table"},
        {"arenas": [None]},
        {"arenas": [[]]},
        {"arenas": ["table"]},
    ],
)
def test_invalid_root_or_arena_collections(invalid, config_file):
    with pytest.raises(ValueError):
        load_lobby_configs(config_file(invalid))


@pytest.mark.parametrize(
    "players", [None, [], {}, "ollama:deepseek-v4-pro", 1, [None], [[]], ["ollama:deepseek-v4-pro"]]
)
def test_invalid_player_collections(players, document, config_file):
    document["arenas"][0]["players"] = players
    with pytest.raises(ValueError):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize("level", ["root", "arena", "player"])
def test_unknown_fields_are_rejected(level, document, config_file):
    targets = {
        "root": document,
        "arena": document["arenas"][0],
        "player": document["arenas"][0]["players"][0],
    }
    targets[level]["typo"] = "unexpected"
    with pytest.raises(ValueError):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize(
    "content",
    [
        "arenas: []\narenas: []\n",
        "arenas:\n  - id: first\n    id: second\n    name: Table\n    description: Table\n    players: []\n",
        "arenas:\n  - id: table\n    name: Table\n    description: Table\n    players:\n      - backend: ollama\n        model: deepseek-v4-pro\n        model: kimi-k2.6\n",
    ],
)
def test_duplicate_mapping_keys_are_rejected_with_location(content, tmp_path):
    path = tmp_path / "duplicates.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, yaml.YAMLError)
    assert error.value.__cause__.problem_mark is not None


@pytest.mark.parametrize(
    "content",
    [
        "",
        "  \n# only a comment\n",
        "null\n",
        "arenas: [",
        "!!python/object/apply:builtins.list []",
        "arenas: []\n---\narenas: []\n",
    ],
)
def test_blank_malformed_and_unsafe_yaml_is_rejected(content, tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)


def test_missing_file_is_rejected(tmp_path):
    path = tmp_path / "missing.yaml"
    with pytest.raises(ValueError) as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_validation_error_identifies_path_and_field(document, config_file):
    document["arenas"][0]["players"][0]["model"] = None
    path = config_file(document)
    with pytest.raises(ValueError) as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert "arenas[0].players[0].model" in str(error.value)


@pytest.mark.parametrize("backend", ["bad", "OLLAMA"])
def test_unsupported_backend_is_rejected(backend, document, config_file):
    document["arenas"][0]["players"][0]["backend"] = backend
    with pytest.raises(ValueError):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize("backend", [OLLAMA_BACKEND, OPENROUTER_BACKEND])
def test_unavailable_model_is_rejected(backend, document, config_file):
    document["arenas"][0]["players"] = [{"backend": backend, "model": "not-a-model"}]
    with pytest.raises(ValueError):
        load_lobby_configs(config_file(document))


def test_model_allowed_when_provider_unavailable(document, config_file, caplog):
    document["arenas"][0]["players"] = [{"backend": "ollama", "model": "anything-goes"}]
    empty = {OPENROUTER_BACKEND: set(), OLLAMA_BACKEND: set()}
    with patch("app.ai.model_config._get_available_models", return_value=empty):
        configs = load_lobby_configs(config_file(document))
    assert configs[0].players[0].model == "anything-goes"
    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_env_path_override_is_relative_to_cwd(document, config_file, monkeypatch):
    path = config_file(document)
    monkeypatch.chdir(path.parent)
    monkeypatch.setenv("ARENA_CONFIG_FILE", path.name)
    assert load_lobby_configs()[0].lobby_id == "stable-table"


def test_explicit_path_overrides_environment(document, config_file, monkeypatch):
    path = config_file(document)
    monkeypatch.setenv("ARENA_CONFIG_FILE", str(path.parent / "missing.yaml"))
    assert load_lobby_configs(str(path))[0].lobby_id == "stable-table"


def test_default_location_is_independent_of_cwd(document, config_file, tmp_path, monkeypatch):
    path = config_file(document)
    monkeypatch.setattr(model_config, "__file__", str(tmp_path / "app" / "ai" / "model_config.py"))
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    assert path == tmp_path / "arenas.yaml"
    assert load_lobby_configs()[0].lobby_id == "stable-table"


def test_supported_models_include_configured_players_without_duplicates(
    document, config_file, monkeypatch
):
    document["arenas"][0]["players"] = [{"backend": "ollama", "model": "anything-goes"}]
    monkeypatch.setenv("ARENA_CONFIG_FILE", str(config_file(document)))
    available = {OPENROUTER_BACKEND: {"openai/gpt-4o-mini"}, OLLAMA_BACKEND: set()}
    with patch("app.ai.model_config._get_available_models", return_value=available):
        configs = get_supported_model_configs(load_lobby_configs())
    assert [(config.backend, config.model) for config in configs] == [
        (OPENROUTER_BACKEND, "openai/gpt-4o-mini"),
        (OLLAMA_BACKEND, "anything-goes"),
    ]


def test_supported_models_deduplicate_provider_and_arena_models(
    document, config_file, monkeypatch, mock_available_models
):
    document["arenas"].append(deepcopy(document["arenas"][0]))
    document["arenas"][1]["id"] = "second-table"
    monkeypatch.setenv("ARENA_CONFIG_FILE", str(config_file(document)))
    configs = get_supported_model_configs(load_lobby_configs())
    identities = [(config.backend, config.model) for config in configs]
    assert len(identities) == len(set(identities))
    assert set(identities) == {
        (backend, model) for backend, models in mock_available_models.items() for model in models
    }
