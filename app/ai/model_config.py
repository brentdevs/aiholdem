"""Model registry and arena lineup configuration."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

import yaml
from yaml.constructor import ConstructorError

logger = logging.getLogger(__name__)

OPENROUTER_BACKEND = "openrouter"
OLLAMA_BACKEND = "ollama"
SUPPORTED_BACKENDS = (OPENROUTER_BACKEND, OLLAMA_BACKEND)


@dataclass(frozen=True)
class ModelConfig:
    backend: str
    model: str
    display_name: str

    @property
    def model_id(self) -> str:
        return self.model

    @property
    def provider_url(self) -> str:
        if self.backend == OLLAMA_BACKEND:
            base_model = self.model.split(":", 1)[0]
            return f"https://ollama.com/library/{base_model}"
        if self.backend == OPENROUTER_BACKEND:
            return f"https://openrouter.ai/{self.model}"
        raise ValueError(f"Unsupported AI backend: {self.backend}")

    def as_dict(self) -> dict[str, str]:
        return {
            "backend": self.backend,
            "model": self.model,
            "display_name": self.display_name,
            "provider_url": self.provider_url,
        }


@dataclass(frozen=True)
class LobbyConfig:
    lobby_id: str
    name: str
    description: str
    players: list[ModelConfig]

    def as_dict(self) -> dict:
        return {
            "lobby_id": self.lobby_id,
            "name": self.name,
            "description": self.description,
            "players": [player.as_dict() for player in self.players],
        }


def _display_name(backend: str, model: str) -> str:
    if backend == OPENROUTER_BACKEND:
        from app.ai.openrouter_player import _derive_display_name

        return _derive_display_name(model)
    if backend == OLLAMA_BACKEND:
        from app.ai.ollama_player import _derive_ollama_display_name

        return _derive_ollama_display_name(model)
    raise ValueError(f"Unsupported AI backend: {backend}")


def _fetch_available_models() -> dict[str, set[str]]:
    """Fetch available models from provider APIs at startup."""
    available: dict[str, set[str]] = {OPENROUTER_BACKEND: set(), OLLAMA_BACKEND: set()}
    _openrouter_reasoning_cache.clear()

    # OpenRouter
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        try:
            # The pinned SDK drops newer catalog fields such as `reasoning`.
            # Read the raw catalog so capability selection retains that metadata.
            request = Request(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {openrouter_key}"},
            )
            with urlopen(request, timeout=20) as response:
                models = json.load(response)["data"]
            for model in models:
                model_id = model["id"]
                available[OPENROUTER_BACKEND].add(model_id)
                if isinstance(model.get("reasoning"), dict):
                    _openrouter_reasoning_cache[model_id] = model["reasoning"]
            logger.info("Loaded %d OpenRouter models", len(available[OPENROUTER_BACKEND]))
        except Exception as e:
            logger.warning("Failed to fetch OpenRouter models: %s", e)

    # Ollama Cloud
    ollama_key = os.environ.get("OLLAMA_API_KEY")
    if ollama_key:
        try:
            from ollama import Client

            client = Client(
                host="https://ollama.com",
                headers={"Authorization": f"Bearer {ollama_key}"},
            )
            response = client.list()
            models = response.models if hasattr(response, "models") else response.get("models", [])
            for m in models:
                name = m.model if hasattr(m, "model") else m.get("model")
                if name:
                    available[OLLAMA_BACKEND].add(name)
                    # Also add without :latest suffix for flexible matching
                    if name.endswith(":latest"):
                        available[OLLAMA_BACKEND].add(name[: -len(":latest")])
            logger.info("Loaded %d Ollama models", len(available[OLLAMA_BACKEND]))
        except Exception as e:
            logger.warning("Failed to fetch Ollama models: %s", e)

    return available


# Cache fetched models at module level (populated on first call)
_available_models_cache: dict[str, set[str]] | None = None
_openrouter_reasoning_cache: dict[str, dict] = {}


def _get_available_models() -> dict[str, set[str]]:
    global _available_models_cache
    if _available_models_cache is None:
        _available_models_cache = _fetch_available_models()
    return _available_models_cache


def get_openrouter_reasoning_config(model_id: str) -> dict[str, str] | None:
    """Minimize reasoning within the model's advertised capabilities."""
    _get_available_models()
    metadata = _openrouter_reasoning_cache.get(model_id)
    if not metadata:
        # Unknown/non-reasoning models must not receive unsupported controls.
        return None
    if not metadata.get("mandatory"):
        return {"effort": "none"}
    if "supported_efforts" not in metadata:
        return None
    efforts = metadata["supported_efforts"]
    for effort in ("minimal", "low", "medium", "high", "xhigh", "max"):
        if efforts is None or effort in efforts:
            return {"effort": effort}
    # Mandatory models without a recognized effort use their provider defaults.
    return None


class _ArenaLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every mapping level."""

    def __init__(self, stream):
        super().__init__(stream)
        self._checked_mappings = set()

    def flatten_mapping(self, node):
        # Anchors may reuse nodes already mutated by an earlier flattening.
        if node in self._checked_mappings:
            return super().flatten_mapping(node)
        # Check only explicit keys before PyYAML expands merges. Its normal
        # flattening preserves explicit overrides and merge-sequence precedence,
        # and visits merge-source mappings through this override as well.
        keys = set()
        for key_node, _ in node.value:
            if key_node.tag in {"tag:yaml.org,2002:merge", "tag:yaml.org,2002:value"}:
                key = key_node.value
            else:
                key = self.construct_object(key_node)
            try:
                duplicate = key in keys
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "mapping keys must be hashable",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate mapping key {key!r}",
                    key_node.start_mark,
                )
            keys.add(key)
        self._checked_mappings.add(node)
        super().flatten_mapping(node)


def _require_mapping(value: object, fields: set[str], context: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{context}: expected a mapping")
    for key in value:
        if key not in fields:
            raise ValueError(f"{context}: unknown field {key!r}")
    for field in sorted(fields):
        if field not in value:
            raise ValueError(f"{context}.{field}: required field is missing")
    return value


def _require_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: expected a nonempty string")
    return value.strip()


def _require_list(value: object, context: str) -> list:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context}: expected a nonempty list")
    return value


def _load_players(value: object, context: str) -> list[ModelConfig]:
    players = _require_list(value, context)
    if not 2 <= len(players) <= 9:
        raise ValueError(f"{context}: expected between 2 and 9 players")
    available = _get_available_models()
    seen: set[tuple[str, str]] = set()
    configs: list[ModelConfig] = []
    for index, player in enumerate(players):
        player_context = f"{context}[{index}]"
        entry = _require_mapping(player, {"backend", "model"}, player_context)
        backend = _require_string(entry["backend"], f"{player_context}.backend")
        model = _require_string(entry["model"], f"{player_context}.model")
        if backend not in SUPPORTED_BACKENDS:
            valid = ", ".join(SUPPORTED_BACKENDS)
            raise ValueError(
                f"{player_context}.backend: unsupported backend {backend!r}; "
                f"valid backends: {valid}"
            )
        key = (backend, model)
        if key in seen:
            raise ValueError(f"{player_context}: duplicate player {backend}:{model}")
        seen.add(key)
        backend_models = available.get(backend, set())
        if backend_models and model not in backend_models:
            raise ValueError(
                f"{player_context}.model: model {model!r} not found on {backend!r}. "
                "Check the model name is correct and available on the provider."
            )
        if not backend_models:
            logger.warning(
                "Could not verify model %r on %r (no API key or fetch failed); allowing anyway",
                model,
                backend,
            )
        configs.append(ModelConfig(backend, model, _display_name(backend, model)))
    return configs


def load_lobby_configs(
    config_path: str | os.PathLike[str] | None = None,
) -> list[LobbyConfig]:
    """Load arena definitions from YAML, preserving arena and player order."""
    if config_path is None:
        config_path = os.environ.get("ARENA_CONFIG_FILE")
        if config_path is None:
            config_path = Path(__file__).resolve().parents[2] / "arenas.yaml"
    if not os.fspath(config_path).strip():
        raise ValueError(
            "Arena configuration path must not be blank; "
            "unset ARENA_CONFIG_FILE to use arenas.yaml"
        )
    path = Path(config_path)
    try:
        with path.open(encoding="utf-8") as stream:
            document = yaml.load(stream, Loader=_ArenaLoader)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"{path}: could not load arena configuration: {exc}") from exc

    context = str(path)
    root = _require_mapping(document, {"arenas"}, context)
    arenas = _require_list(root["arenas"], f"{context}: arenas")
    lobbies: list[LobbyConfig] = []
    seen_ids: set[str] = set()
    for index, arena in enumerate(arenas):
        arena_context = f"{context}: arenas[{index}]"
        entry = _require_mapping(arena, {"id", "name", "description", "players"}, arena_context)
        lobby_id = _require_string(entry["id"], f"{arena_context}.id")
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", lobby_id) is None:
            raise ValueError(
                f"{arena_context}.id: expected a lowercase URL-safe slug "
                "(letters, digits, and single separating hyphens)"
            )
        if lobby_id in seen_ids:
            raise ValueError(f"{arena_context}.id: duplicate arena ID {lobby_id!r}")
        seen_ids.add(lobby_id)
        name = _require_string(entry["name"], f"{arena_context}.name")
        description = _require_string(entry["description"], f"{arena_context}.description")
        players = _load_players(entry["players"], f"{arena_context}.players")
        lobbies.append(LobbyConfig(lobby_id, name, description, players))
    return lobbies


def get_supported_model_configs(lobbies: list[LobbyConfig]) -> list[ModelConfig]:
    """Return provider models and players from the startup arena configuration."""
    configs: list[ModelConfig] = []
    seen: set[tuple[str, str]] = set()
    available = _get_available_models()
    for backend, models in available.items():
        for model in sorted(models):
            key = (backend, model)
            if key not in seen:
                seen.add(key)
                configs.append(ModelConfig(backend, model, _display_name(backend, model)))
    # Always include current arena players even if provider fetch failed
    for lobby in lobbies:
        for config in lobby.players:
            key = (config.backend, config.model)
            if key not in seen:
                seen.add(key)
                configs.append(config)
    return configs
