"""Model registry and arena lineup configuration."""
from __future__ import annotations

from dataclasses import dataclass
import os

from app.ai.ollama_player import SUPPORTED_OLLAMA_MODELS, _derive_ollama_display_name
from app.ai.openrouter_player import SUPPORTED_MODELS, _derive_display_name

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

    def as_dict(self) -> dict[str, str]:
        return {
            "backend": self.backend,
            "model": self.model,
            "display_name": self.display_name,
        }


DEFAULT_ARENA_PLAYER_CONFIGS: tuple[tuple[str, str], ...] = tuple(
    (OLLAMA_BACKEND, model) for model in SUPPORTED_OLLAMA_MODELS
)


def _supported_models_by_backend() -> dict[str, list[str]]:
    return {
        OPENROUTER_BACKEND: SUPPORTED_MODELS,
        OLLAMA_BACKEND: SUPPORTED_OLLAMA_MODELS,
    }


def _display_name(backend: str, model: str) -> str:
    if backend == OPENROUTER_BACKEND:
        return _derive_display_name(model)
    if backend == OLLAMA_BACKEND:
        return _derive_ollama_display_name(model)
    raise ValueError(f"Unsupported AI backend: {backend}")


def get_supported_model_configs() -> list[ModelConfig]:
    """Return every locally supported model, grouped by backend."""
    configs: list[ModelConfig] = []
    seen: set[tuple[str, str]] = set()
    for backend in SUPPORTED_BACKENDS:
        for model in _supported_models_by_backend()[backend]:
            key = (backend, model)
            if key in seen:
                continue
            seen.add(key)
            configs.append(ModelConfig(backend, model, _display_name(backend, model)))
    return configs


def _parse_arena_players(raw_value: str) -> list[tuple[str, str]]:
    """Parse ARENA_PLAYERS as comma-separated backend:model entries."""
    entries: list[tuple[str, str]] = []
    for raw_entry in raw_value.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(
                "Invalid ARENA_PLAYERS entry "
                f"{entry!r}; expected '<backend>:<model>'"
            )
        backend, model = entry.split(":", 1)
        entries.append((backend.strip().lower(), model.strip()))
    return entries


def _validate_arena_entries(entries: list[tuple[str, str]]) -> list[ModelConfig]:
    if not entries:
        raise ValueError("ARENA_PLAYERS must include at least one player")

    supported = _supported_models_by_backend()
    seen: set[tuple[str, str]] = set()
    configs: list[ModelConfig] = []
    for backend, model in entries:
        if backend not in supported:
            valid = ", ".join(SUPPORTED_BACKENDS)
            raise ValueError(
                f"Unsupported ARENA_PLAYERS backend {backend!r}; valid backends: {valid}"
            )
        if model not in supported[backend]:
            raise ValueError(
                f"Unsupported ARENA_PLAYERS model {model!r} for backend {backend!r}"
            )
        key = (backend, model)
        if key in seen:
            raise ValueError(f"Duplicate ARENA_PLAYERS entry {backend}:{model}")
        seen.add(key)
        configs.append(ModelConfig(backend, model, _display_name(backend, model)))
    return configs


def load_arena_player_configs(raw_value: str | None = None) -> list[ModelConfig]:
    """Load and validate the active arena lineup.

    When ARENA_PLAYERS is unset, keep the historical default Ollama lineup.
    Format: "ollama:deepseek-v4-pro,openrouter:google/gemini-2.5-flash".
    """
    if raw_value is None:
        raw_value = os.environ.get("ARENA_PLAYERS")
    entries = (
        _parse_arena_players(raw_value)
        if raw_value is not None and raw_value.strip()
        else list(DEFAULT_ARENA_PLAYER_CONFIGS)
    )
    return _validate_arena_entries(entries)


def arena_players_env_value(configs: list[ModelConfig] | None = None) -> str:
    """Serialize arena configs back to ARENA_PLAYERS format."""
    if configs is None:
        configs = load_arena_player_configs()
    return ",".join(f"{config.backend}:{config.model}" for config in configs)
