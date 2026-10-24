"""Unit tests for the YAML arena configuration and model registry."""

from __future__ import annotations

import json
from copy import deepcopy
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError

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
    with pytest.raises(ValueError, match=r"arenas\[0\]\.id"):
        load_lobby_configs(config_file(document))


def test_duplicate_ids_are_rejected(document, config_file):
    duplicate = deepcopy(document["arenas"][0])
    duplicate["name"] = "Different name"
    document["arenas"].append(duplicate)
    with pytest.raises(ValueError, match=r"arenas\[1\]\.id: duplicate"):
        load_lobby_configs(config_file(document))


def test_duplicate_players_are_rejected(document, config_file):
    players = document["arenas"][0]["players"]
    players.append(deepcopy(players[0]))
    with pytest.raises(ValueError, match=r"players\[2\]: duplicate player"):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize("field", ["id", "name", "description", "players"])
def test_missing_arena_fields_are_rejected(field, document, config_file):
    del document["arenas"][0][field]
    with pytest.raises(ValueError, match=rf"arenas\[0\]\.{field}: required"):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize("field", ["backend", "model"])
def test_missing_player_fields_are_rejected(field, document, config_file):
    del document["arenas"][0]["players"][0][field]
    with pytest.raises(ValueError, match=rf"players\[0\]\.{field}: required"):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize("field", ["id", "name", "description", "backend", "model"])
@pytest.mark.parametrize("value", [None, "", " \t ", 42, True, [], {}])
def test_string_fields_require_nonempty_strings(field, value, document, config_file):
    arena = document["arenas"][0]
    target = arena["players"][0] if field in {"backend", "model"} else arena
    target[field] = value
    with pytest.raises(ValueError, match=rf"\.{field}: expected a nonempty string"):
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
    with pytest.raises(ValueError, match=r"expected a (mapping|nonempty list)|\.arenas: required"):
        load_lobby_configs(config_file(invalid))


@pytest.mark.parametrize(
    "players",
    [None, [], {}, "ollama:deepseek-v4-pro", 1, [None, None], [[], []], ["model", "model"]],
)
def test_invalid_player_collections(players, document, config_file):
    document["arenas"][0]["players"] = players
    with pytest.raises(
        ValueError, match=r"\.players(?:\[0\])?: expected a (mapping|nonempty list)"
    ):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize("level", ["root", "arena", "player"])
def test_unknown_fields_are_rejected(level, document, config_file):
    targets = {
        "root": document,
        "arena": document["arenas"][0],
        "player": document["arenas"][0]["players"][0],
    }
    targets[level]["typo"] = "unexpected"
    with pytest.raises(ValueError, match=r"unknown field 'typo'"):
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
    with pytest.raises(ValueError, match="duplicate mapping key") as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, yaml.constructor.ConstructorError)
    assert error.value.__cause__.problem_mark is not None


@pytest.mark.parametrize(
    ("content", "cause_type", "diagnostic"),
    [
        ("", type(None), "expected a mapping"),
        ("  \n# only a comment\n", type(None), "expected a mapping"),
        ("null\n", type(None), "expected a mapping"),
        ("arenas: [", yaml.YAMLError, "could not load"),
    ],
)
def test_blank_and_malformed_yaml_is_rejected(content, cause_type, diagnostic, tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=diagnostic) as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, cause_type)


def test_missing_file_is_rejected(tmp_path):
    path = tmp_path / "missing.yaml"
    with pytest.raises(ValueError) as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_validation_error_identifies_path_and_field(document, config_file):
    document["arenas"][0]["players"][0]["model"] = None
    path = config_file(document)
    with pytest.raises(ValueError, match=r"players\[0\]\.model") as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert "arenas[0].players[0].model" in str(error.value)


@pytest.mark.parametrize("backend", ["bad", "OLLAMA"])
def test_unsupported_backend_is_rejected(backend, document, config_file):
    document["arenas"][0]["players"][0]["backend"] = backend
    with pytest.raises(ValueError, match=r"players\[0\]\.backend: unsupported backend"):
        load_lobby_configs(config_file(document))


@pytest.mark.parametrize("backend", [OLLAMA_BACKEND, OPENROUTER_BACKEND])
def test_unavailable_model_is_rejected(backend, document, config_file):
    document["arenas"][0]["players"][0] = {"backend": backend, "model": "not-a-model"}
    with pytest.raises(ValueError, match=r"players\[0\]\.model: .*not found"):
        load_lobby_configs(config_file(document))


def test_model_allowed_when_provider_unavailable(document, config_file, caplog):
    document["arenas"][0]["players"][0] = {"backend": "ollama", "model": "anything-goes"}
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


@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
@pytest.mark.parametrize("source", ["environment", "argument"])
def test_blank_configuration_paths_are_rejected(blank, source, monkeypatch):
    if source == "environment":
        monkeypatch.setenv("ARENA_CONFIG_FILE", blank)
        args = ()
    else:
        args = (blank,)
    with pytest.raises(ValueError, match="configuration path must not be blank"):
        load_lobby_configs(*args)


@pytest.mark.parametrize("override", ["missing.yaml", ""])
def test_explicit_path_overrides_environment(override, document, config_file, monkeypatch):
    path = config_file(document)
    monkeypatch.setenv("ARENA_CONFIG_FILE", override)
    assert load_lobby_configs(str(path))[0].lobby_id == "stable-table"


def test_default_location_is_independent_of_cwd(document, config_file, tmp_path, monkeypatch):
    config_file(document)
    monkeypatch.setattr(model_config, "__file__", str(tmp_path / "app" / "ai" / "model_config.py"))
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    assert load_lobby_configs()[0].lobby_id == "stable-table"


def test_supported_models_include_configured_players_without_duplicates(
    document, config_file, monkeypatch
):
    document["arenas"][0]["players"] = [
        {"backend": "ollama", "model": "anything-goes"},
        {"backend": "ollama", "model": "another-model"},
    ]
    second = deepcopy(document["arenas"][0])
    second["id"] = "second-table"
    document["arenas"].append(second)
    monkeypatch.setenv("ARENA_CONFIG_FILE", str(config_file(document)))
    available = {OPENROUTER_BACKEND: {"openai/gpt-4o-mini"}, OLLAMA_BACKEND: set()}
    with patch("app.ai.model_config._get_available_models", return_value=available):
        configs = get_supported_model_configs(load_lobby_configs())
    assert [(config.backend, config.model) for config in configs] == [
        (OPENROUTER_BACKEND, "openai/gpt-4o-mini"),
        (OLLAMA_BACKEND, "anything-goes"),
        (OLLAMA_BACKEND, "another-model"),
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


@pytest.mark.parametrize("count", [1, 10])
def test_player_capacity_rejected_before_provider_checks(count, document, config_file):
    document["arenas"][0]["players"] = [
        {"backend": "invalid", "model": f"unknown-{index}"} for index in range(count)
    ]
    path = config_file(document)
    with patch(
        "app.ai.model_config._get_available_models",
        side_effect=AssertionError("provider catalog must not be accessed"),
    ):
        with pytest.raises(ValueError, match=r"arenas\[0\]\.players: .*2.*9") as error:
            load_lobby_configs(path)
    assert str(path) in str(error.value)


@pytest.mark.parametrize("count", [2, 9])
def test_player_capacity_boundaries_are_accepted(count, document, config_file):
    models = [f"model-{index}" for index in range(count)]
    document["arenas"][0]["players"] = [{"backend": "ollama", "model": model} for model in models]
    with patch(
        "app.ai.model_config._get_available_models",
        return_value={OLLAMA_BACKEND: set(models)},
    ):
        configs = load_lobby_configs(config_file(document))
    assert [player.model for player in configs[0].players] == models


def test_unsafe_yaml_tag_cannot_create_file(tmp_path):
    sentinel = tmp_path / "unsafe-loader-executed"
    path = tmp_path / "unsafe.yaml"
    tag = "tag:yaml.org,2002:python/object/apply:builtins.eval"
    # An unsafe constructor would invoke a real filesystem side effect.
    path.write_text(
        "!!python/object/apply:builtins.eval\n"
        + yaml.safe_dump([f"__import__('pathlib').Path({str(sentinel)!r}).touch()"]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as error:
        load_lobby_configs(path)
    assert not sentinel.exists()
    assert str(path) in str(error.value)
    cause = error.value.__cause__
    assert isinstance(cause, yaml.constructor.ConstructorError)
    assert tag in cause.problem


def test_multiple_valid_yaml_documents_are_rejected(document, tmp_path):
    second = deepcopy(document)
    second["arenas"][0]["id"] = "second-table"
    path = tmp_path / "multiple.yaml"
    path.write_text(yaml.safe_dump_all([document, second]), encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, yaml.composer.ComposerError)


@pytest.mark.parametrize("content", ["!!map scalar\n", "!!map [one, two]\n"])
def test_malformed_mapping_nodes_preserve_constructor_error(content, tmp_path):
    path = tmp_path / "malformed-map.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="expected a mapping node") as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, yaml.constructor.ConstructorError)
    assert error.value.__cause__.problem_mark is not None


def test_invalid_implicit_date_has_path_and_scalar_error(document, tmp_path):
    path = tmp_path / "invalid-date.yaml"
    content = yaml.safe_dump(document).replace("name: Main Arena", "name: 2026-02-30")
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, ValueError)


@pytest.mark.parametrize(
    ("merge", "expected_backend", "expected_model"),
    [
        ("*defaults", "ollama", "kimi-k2.6"),
        (
            "[*defaults, {backend: openrouter, model: google/gemini-2.5-flash}]",
            "ollama",
            "kimi-k2.6",
        ),
        (
            "[{backend: openrouter, model: google/gemini-2.5-flash}, *defaults]",
            "openrouter",
            "openai/gpt-4o-mini",
        ),
    ],
)
@pytest.mark.parametrize("override_first", [False, True])
def test_yaml_merge_overrides_and_sequence_precedence(
    merge, expected_backend, expected_model, override_first, tmp_path
):
    override = f"        model: {expected_model}\n"
    merge_line = f"        <<: {merge}\n"
    path = tmp_path / "merged.yaml"
    path.write_text(
        "arenas:\n"
        "  - id: merged\n"
        "    name: Merged\n"
        "    description: Standard YAML merges\n"
        "    players:\n"
        "      - &defaults\n"
        "        <<: {backend: ollama, model: kimi-k2.6}\n"
        "        model: deepseek-v4-pro\n"
        "      -\n" + (override + merge_line if override_first else merge_line + override),
        encoding="utf-8",
    )
    players = load_lobby_configs(path)[0].players
    assert [(player.backend, player.model) for player in players] == [
        ("ollama", "deepseek-v4-pro"),
        (expected_backend, expected_model),
    ]


@pytest.mark.parametrize(
    "content",
    [
        "<<: {arenas: [], arenas: []}\n",
        "<<: [{arenas: [], arenas: []}, {arenas: []}]\n",
        "<<: {arenas: []}\n<<: {arenas: []}\n",
    ],
)
def test_explicit_duplicates_in_yaml_merges_are_rejected(content, tmp_path):
    path = tmp_path / "duplicate-merge.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate mapping key") as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, yaml.constructor.ConstructorError)
    assert error.value.__cause__.problem_mark is not None


@pytest.mark.parametrize("key", ["[one, two]", "{one: two}"])
def test_unhashable_mapping_keys_are_rejected_with_context(key, tmp_path):
    path = tmp_path / "unhashable.yaml"
    path.write_text(f"? {key}\n: value\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hashable") as error:
        load_lobby_configs(path)
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, yaml.constructor.ConstructorError)
    assert error.value.__cause__.problem_mark is not None


@pytest.mark.parametrize("variable", ["ARENA_PLAYERS", "ARENA_LOBBIES"])
def test_obsolete_environment_does_not_replace_missing_yaml(variable, monkeypatch, tmp_path):
    monkeypatch.setenv(variable, "ollama:deepseek-v4-pro,ollama:kimi-k2.6")
    path = tmp_path / "missing.yaml"
    monkeypatch.setenv("ARENA_CONFIG_FILE", str(path))
    with pytest.raises(ValueError) as error:
        load_lobby_configs()
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_yaml_lineup_ignores_obsolete_environment(document, config_file, monkeypatch):
    monkeypatch.setenv("ARENA_PLAYERS", "ollama:kimi-k2.6,ollama:qwen3.5:cloud")
    monkeypatch.setenv("ARENA_LOBBIES", "obsolete|Old Table|ollama:kimi-k2.6,ollama:qwen3.5:cloud")
    monkeypatch.setenv("ARENA_CONFIG_FILE", str(config_file(document)))
    lobbies = load_lobby_configs()
    assert [lobby.lobby_id for lobby in lobbies] == ["stable-table"]
    assert [(player.backend, player.model) for player in lobbies[0].players] == [
        (OLLAMA_BACKEND, "deepseek-v4-pro"),
        (OPENROUTER_BACKEND, "google/gemini-2.5-flash"),
    ]


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (None, None),
        ({}, None),
        ({"mandatory": False, "default_enabled": True}, {"effort": "none"}),
        (
            {"mandatory": False, "supported_efforts": ["high", "low"]},
            {"effort": "none"},
        ),
        (
            {"mandatory": True, "supported_efforts": ["max", "high", "low"]},
            {"effort": "low"},
        ),
        (
            {"mandatory": True, "supported_efforts": ["high", "minimal", "low"]},
            {"effort": "minimal"},
        ),
        (
            {"mandatory": True, "supported_efforts": ["none", "high"]},
            {"effort": "high"},
        ),
        ({"mandatory": True, "supported_efforts": None}, {"effort": "minimal"}),
        ({"mandatory": True}, None),
        ({"mandatory": True, "supported_efforts": []}, None),
        ({"mandatory": True, "supported_efforts": ["none"]}, None),
        ({"mandatory": True, "supported_efforts": ["future-effort"]}, None),
    ],
)
def test_reasoning_config_uses_cheapest_compatible_control(metadata, expected, monkeypatch):
    cache = {} if metadata is None else {"example/model": metadata}
    monkeypatch.setattr(model_config, "_openrouter_reasoning_cache", cache)
    assert model_config.get_openrouter_reasoning_config("example/model") == expected


def test_raw_openrouter_catalog_retains_reasoning_capabilities(monkeypatch):
    catalog = {
        "data": [
            {
                "id": "example/mandatory",
                "reasoning": {"mandatory": True, "supported_efforts": ["high", "low"]},
            },
            {"id": "example/optional", "reasoning": {"mandatory": False}},
            {"id": "example/plain"},
        ]
    }
    monkeypatch.setenv("OPENROUTER_API_KEY", "catalog-test-key")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setattr(model_config, "_openrouter_reasoning_cache", {})
    monkeypatch.setattr(
        model_config,
        "urlopen",
        lambda request, timeout: BytesIO(json.dumps(catalog).encode()),
    )

    available = model_config._fetch_available_models()

    assert available[OPENROUTER_BACKEND] == {
        "example/mandatory",
        "example/optional",
        "example/plain",
    }
    assert model_config.get_openrouter_reasoning_config("example/mandatory") == {"effort": "low"}
    assert model_config.get_openrouter_reasoning_config("example/optional") == {"effort": "none"}
    assert model_config.get_openrouter_reasoning_config("example/plain") is None


def test_catalog_failure_does_not_reuse_stale_reasoning_capabilities(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "catalog-test-key")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setattr(
        model_config,
        "_openrouter_reasoning_cache",
        {"example/model": {"mandatory": False}},
    )

    def unavailable(request, timeout):
        raise URLError("catalog unavailable")

    monkeypatch.setattr(model_config, "urlopen", unavailable)
    available = model_config._fetch_available_models()

    assert available[OPENROUTER_BACKEND] == set()
    assert model_config.get_openrouter_reasoning_config("example/model") is None
