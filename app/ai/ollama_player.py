"""Ollama Cloud AI player implementation."""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from app.ai.openrouter_player import _build_prompt, _extract_reasoning, _parse_action, _parse_json_response
from app.game.models import Action, ActionType
from app.game.players import AIPlayer

logger = logging.getLogger(__name__)

OLLAMA_CLOUD_HOST = "https://ollama.com"

SUPPORTED_OLLAMA_MODELS: list[str] = [
    "deepseek-v4-pro",
    "kimi-k2.6",
    "glm-5.1",
    "minimax-m2.7",
    "gemma4:31b",
    "nemotron-3-super",
    "mistral-large-3:675b",
]


def _response_content(response: Any) -> str:
    """Extract assistant message content from Ollama response objects or dicts."""
    message = getattr(response, "message", None)
    if message is not None:
        content = getattr(message, "content", None)
        if content is not None:
            return str(content)

    if isinstance(response, dict):
        message_dict = response.get("message", {})
        if isinstance(message_dict, dict):
            return str(message_dict.get("content") or "")

    try:
        message_dict = response["message"]
        return str(message_dict.get("content") or "")
    except (KeyError, TypeError, AttributeError):
        return ""


def _derive_ollama_display_name(model: str) -> str:
    """Derive a display name without Ollama cloud deployment suffixes."""
    name = model.split("/", 1)[-1] if "/" in model else model
    if name.endswith(":cloud"):
        name = name[:-6]
    elif name.endswith("-cloud"):
        name = name[:-6]
    return name[0].upper() + name[1:] if name else name


def _create_ollama_client(api_key: str):
    from ollama import Client

    return Client(
        host=OLLAMA_CLOUD_HOST,
        headers={"Authorization": f"Bearer {api_key}"},
    )


class OllamaCloudPlayer(AIPlayer):
    """AI player backed by Ollama Cloud's hosted API."""

    def __init__(
        self,
        player_id: str,
        chips: int,
        model: str = SUPPORTED_OLLAMA_MODELS[0],
        name: str | None = None,
    ) -> None:
        display_name = name if name is not None else _derive_ollama_display_name(model)
        super().__init__(player_id, display_name, chips, provider="ollama")
        self.model = model
        self.profiling_service = None
        self.game_api_calls: int = 0
        self.game_api_failures: int = 0
        self.game_total_latency_ms: int = 0

    def decide_action(self, game_state: dict, valid_actions: list[Action]) -> tuple[Action, str]:
        session_id = game_state.get("session_id", "unknown")
        min_raise = game_state.get("min_raise", 0)
        history: list[tuple[str, str, str, int | None]] = game_state.get("hand_history", [])

        def _fold() -> Action:
            for a in valid_actions:
                if a.type == ActionType.FOLD:
                    return a
            return Action(type=ActionType.FOLD)

        def _check_or_fold() -> Action:
            for a in valid_actions:
                if a.type == ActionType.CHECK:
                    return a
            return _fold()

        profiles_block = None
        try:
            if self.profiling_service is not None and self.profiling_service.available:
                players = game_state.get("players", [])
                player_ids = [p.get("player_id", "") for p in players]
                id_to_label = {
                    p.get("player_id", ""): f"P{i}"
                    for i, p in enumerate(players, start=1)
                }
                profiles_block = self.profiling_service.get_opponent_profiles(
                    game_id=game_state.get("session_id", ""),
                    player_ids=player_ids,
                    self_player_id=self.player_id,
                    id_to_label=id_to_label,
                )
        except Exception as exc:
            logger.warning(
                "Failed to fetch opponent profiles session=%s player=%s: %s",
                session_id, self.player_id, exc,
            )
            profiles_block = None

        self.game_api_calls += 1
        prompt = "<not built>"
        try:
            prompt = _build_prompt(
                game_state,
                valid_actions,
                history,
                self.player_id,
                profiles_block=profiles_block,
            )
            logger.debug(
                "OllamaCloudPlayer requesting action session=%s player=%s model=%s\nPROMPT:\n%s",
                session_id, self.player_id, self.model, prompt,
            )

            api_key = os.getenv("OLLAMA_API_KEY")
            if not api_key:
                raise RuntimeError("OLLAMA_API_KEY is not configured")

            import eventlet

            call_start = time.monotonic()
            with eventlet.Timeout(20, TimeoutError):
                client = _create_ollama_client(api_key)
                response = client.chat(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    stream=False,
                )
            call_elapsed_ms = int((time.monotonic() - call_start) * 1000)
            self.game_total_latency_ms += call_elapsed_ms

            text = _response_content(response).strip().strip("'\"`")
            logger.debug(
                "OllamaCloudPlayer raw response session=%s player=%s model=%s response=%r",
                session_id, self.player_id, self.model, text,
            )

            self_player = next(
                (p for p in game_state.get("players", []) if p.get("player_id") == self.player_id),
                {},
            )
            player_chips = self_player.get("chips", 0)
            player_round_contrib = self_player.get("current_bet", 0)
            result = _parse_json_response(
                text,
                valid_actions,
                min_raise,
                player_chips=player_chips,
                player_round_contrib=player_round_contrib,
            )
            if result is not None:
                action, reasoning = result
                logger.debug(
                    "OllamaCloudPlayer action decided (json) session=%s player=%s action=%s amount=%s",
                    session_id, self.player_id, action.type.value, action.amount or "",
                )
                return (action, reasoning)

            action = _parse_action(
                text,
                valid_actions,
                min_raise,
                player_chips=player_chips,
                player_round_contrib=player_round_contrib,
            )
            if action is not None:
                logger.debug(
                    "OllamaCloudPlayer action decided (keyword) session=%s player=%s action=%s amount=%s",
                    session_id, self.player_id, action.type.value, action.amount or "",
                )
                return (action, _extract_reasoning(text, action.type.value))

            logger.warning(
                "OllamaCloudPlayer unparseable response session=%s player=%s model=%s response=%r\nPROMPT:\n%s",
                session_id, self.player_id, self.model, text, prompt,
            )
            return (_check_or_fold(), "No reasoning provided")
        except TimeoutError:
            self.game_api_failures += 1
            logger.error(
                "OllamaCloudPlayer timed out (20s) session=%s player=%s model=%s",
                session_id, self.player_id, self.model,
            )
            return (_check_or_fold(), "Response timed out (20s) - defaulted to check/fold")
        except Exception as exc:
            self.game_api_failures += 1
            exc_str = str(exc)
            clean = exc_str[:300] if len(exc_str) > 300 else exc_str
            if clean.startswith("PROMPT:") or clean.startswith("Action codes:"):
                clean = f"{type(exc).__name__}: (error message contained prompt text)"
            logger.error(
                "OllamaCloudPlayer API error session=%s player=%s model=%s error=%s",
                session_id, self.player_id, self.model, clean,
            )
            logger.debug(
                "OllamaCloudPlayer failed prompt session=%s player=%s model=%s\nPROMPT:\n%s",
                session_id, self.player_id, self.model, prompt,
            )
            return (_check_or_fold(), "API error - defaulted to check/fold")

    def reset_game_stats(self) -> None:
        """Zero out per-game stat counters."""
        self.game_api_calls = 0
        self.game_api_failures = 0
        self.game_total_latency_ms = 0

    def __repr__(self) -> str:
        return (
            f"OllamaCloudPlayer(id={self.player_id!r}, name={self.name!r}, "
            f"chips={self.chips}, model={self.model!r})"
        )
