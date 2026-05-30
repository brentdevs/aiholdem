"""Model registry and arena lineup configuration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

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

    # OpenRouter
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        try:
            from openrouter import OpenRouter

            client = OpenRouter(api_key=openrouter_key)
            response = client.models.list()
            models = response.data if hasattr(response, "data") else response
            for m in models:
                model_id = m.id if hasattr(m, "id") else m.get("id")
                if model_id:
                    available[OPENROUTER_BACKEND].add(model_id)
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


def _get_available_models() -> dict[str, set[str]]:
    global _available_models_cache
    if _available_models_cache is None:
        _available_models_cache = _fetch_available_models()
    return _available_models_cache


def _parse_arena_players(raw_value: str) -> list[tuple[str, str]]:
    """Parse ARENA_PLAYERS as comma-separated backend:model entries."""
    entries: list[tuple[str, str]] = []
    for raw_entry in raw_value.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(
                "Invalid ARENA_PLAYERS entry " f"{entry!r}; expected '<backend>:<model>'"
            )
        backend, model = entry.split(":", 1)
        entries.append((backend.strip().lower(), model.strip()))
    return entries


def _validate_arena_entries(entries: list[tuple[str, str]]) -> list[ModelConfig]:
    if not entries:
        raise ValueError("ARENA_PLAYERS must include at least one player")

    available = _get_available_models()
    seen: set[tuple[str, str]] = set()
    configs: list[ModelConfig] = []
    for backend, model in entries:
        if backend not in SUPPORTED_BACKENDS:
            valid = ", ".join(SUPPORTED_BACKENDS)
            raise ValueError(
                f"Unsupported ARENA_PLAYERS backend {backend!r}; valid backends: {valid}"
            )
        backend_models = available.get(backend, set())
        if backend_models and model not in backend_models:
            raise ValueError(
                f"Model {model!r} not found on {backend!r}. "
                f"Check the model name is correct and available on the provider."
            )
        if not backend_models:
            logger.warning(
                "Could not verify model %r on %r (no API key or fetch failed); allowing anyway",
                model,
                backend,
            )
        key = (backend, model)
        if key in seen:
            raise ValueError(f"Duplicate ARENA_PLAYERS entry {backend}:{model}")
        seen.add(key)
        configs.append(ModelConfig(backend, model, _display_name(backend, model)))
    return configs


def load_arena_player_configs(raw_value: str | None = None) -> list[ModelConfig]:
    """Load and validate the active arena lineup.

    Format: "ollama:deepseek-v4-pro,openrouter:google/gemini-2.5-flash".
    """
    if raw_value is None:
        raw_value = os.environ.get("ARENA_PLAYERS")
    if not raw_value or not raw_value.strip():
        raise ValueError(
            "ARENA_PLAYERS environment variable must be set with at least one player "
            "(format: 'backend:model,backend:model')"
        )
    entries = _parse_arena_players(raw_value)
    return _validate_arena_entries(entries)


def get_supported_model_configs() -> list[ModelConfig]:
    """Return available models from providers, including arena players."""
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
    try:
        for config in load_arena_player_configs():
            key = (config.backend, config.model)
            if key not in seen:
                seen.add(key)
                configs.append(config)
    except ValueError:
        pass
    return configs


def arena_players_env_value(configs: list[ModelConfig] | None = None) -> str:
    """Serialize arena configs back to ARENA_PLAYERS format."""
    if configs is None:
        configs = load_arena_player_configs()
    return ",".join(f"{config.backend}:{config.model}" for config in configs)
