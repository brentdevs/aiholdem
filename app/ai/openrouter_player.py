"""OpenRouter AI player — single implementation replacing ChatGPTPlayer and ClaudePlayer."""
from __future__ import annotations

import json
import logging
import os
import re
import time

from openrouter import OpenRouter

from app.game.models import Action, ActionType
from app.game.players import AIPlayer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported models — single source of truth for backend validation and UI
# ---------------------------------------------------------------------------

SUPPORTED_MODELS: list[str] = [
    "qwen/qwen3.6-plus:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "stepfun/step-3.5-flash:free",
    "deepseek/deepseek-v3.2",
    "google/gemini-3-flash-preview",
    "x-ai/grok-4.1-fast",
    "sao10k/l3-lunaris-8b",
    "qwen/qwen-2.5-72b-instruct",
    "mistralai/ministral-8b-2512",
    "liquid/lfm-2-24b-a2b",
    "anthropic/claude-opus-4.6-fast",
    "openai/gpt-5.4-mini",
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-flash",
    "openai/gpt-4o-mini",
    "qwen/qwen3-235b-a22b-2507",
    "mistralai/mistral-medium-3",
    "meta-llama/llama-4-maverick",
    "deepseek/deepseek-chat",
    "meta-llama/llama-4-scout",
    "qwen/qwen3-30b-a3b",
    "microsoft/phi-4",
    "anthropic/claude-3-haiku",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_display_name(model: str) -> str:
    """Derive a human-readable display name from a model string.

    Examples:
        "qwen/qwen3-6-plus:free"           -> "Qwen3-6-plus (free)"
        "x-ai/grok-4.1-fast"               -> "Grok-4.1-fast"
        "nvidia/nemotron-3-super-120b-a12b:free" -> "Nemotron-3-super-120b-a12b (free)"
    """
    # Strip provider prefix
    name = model.split("/", 1)[-1] if "/" in model else model
    is_free = name.endswith(":free")
    if is_free:
        name = name[:-5]  # strip ":free"
    # Title-case first character only
    name = name[0].upper() + name[1:] if name else name
    return f"{name} (free)" if is_free else name


def _serialize_history(
    history: list[tuple[str, str, str, int | None]],
    name_to_label: dict[str, str],
) -> str:
    """Serialize hand history to a compact single-line string using P1/P2/etc labels.

    Input:  [("PF","Alice","R",200), ("PF","Bob","C",None), ("F","Alice","X",None)]
    Output: "PF: P1 R200, P2 C | F: P1 X"

    Returns "" for empty history.
    """
    if not history:
        return ""

    # Group by phase, preserving insertion order
    phases: dict[str, list[str]] = {}
    for phase_abbr, player_name, action_code, amount in history:
        label = name_to_label.get(player_name, player_name)
        entry = f"{label} {action_code}{amount}" if amount is not None else f"{label} {action_code}"
        phases.setdefault(phase_abbr, []).append(entry)

    return " | ".join(f"{phase}: {', '.join(actions)}" for phase, actions in phases.items())


# Fixed glossary header included at the top of every prompt
_PROMPT_GLOSSARY = (
    "Action codes: F=fold X=check C=call R=raise A=all-in"
)


_SUIT_UNICODE = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
_RANK_LABEL = {11: "J", 12: "Q", 13: "K", 14: "A"}


def _card_str(c: dict) -> str:
    """Format a card dict as e.g. A♠ or 10♥."""
    rank = c.get("rank", "?")
    suit = c.get("suit", "?")
    rank_label = _RANK_LABEL.get(rank, str(rank))
    suit_symbol = _SUIT_UNICODE.get(suit, suit)
    return f"{rank_label}{suit_symbol}"


def _build_prompt(
    game_state: dict,
    valid_actions: list[Action],
    history: list[tuple[str, str, str, int | None]],
    self_player_id: str,
    profiles_block: str | None = None,
) -> str:
    """Build the full prompt string sent to the LLM."""
    community = game_state.get("community_cards", [])
    hole_cards = game_state.get("hole_cards", [])
    pot = game_state.get("pot", 0)
    players = game_state.get("players", [])
    position = game_state.get("position", "unknown")
    min_raise = game_state.get("min_raise", 0)
    phase = game_state.get("phase", None)
    blinds = game_state.get("blinds", (0, 0))

    # Build stable P1/P2/... labels ordered by player list; mark self as "you"
    name_to_label: dict[str, str] = {}
    id_to_label: dict[str, str] = {}
    for i, p in enumerate(players, start=1):
        pid = p.get("player_id", "")
        name = p.get("name", p.get("player_id", "?"))
        label = f"P{i}"
        name_to_label[name] = label
        id_to_label[pid] = label

    self_label = id_to_label.get(self_player_id, "you")

    community_str = ", ".join(
        _card_str(c) if isinstance(c, dict) else repr(c)
        for c in community
    ) or "none"

    hole_str = ", ".join(
        _card_str(c) if isinstance(c, dict) else repr(c)
        for c in hole_cards
    ) or "unknown"

    # Betting round label
    _phase_labels = {
        "pre_flop": "Pre-Flop", "flop": "Flop",
        "turn": "Turn", "river": "River",
    }
    round_name = _phase_labels.get(phase, phase or "Unknown")

    # Derive dealer/SB/BB roles from dealer_button index
    dealer_idx = game_state.get("dealer_button", None)
    non_elim = [p for p in players if not p.get("is_eliminated")]
    _role_map: dict[str, str] = {}
    if dealer_idx is not None and len(non_elim) >= 2:
        dealer_pid = players[dealer_idx]["player_id"] if dealer_idx < len(players) else None
        ne_ids = [p["player_id"] for p in non_elim]
        if dealer_pid in ne_ids:
            d = ne_ids.index(dealer_pid)
            n = len(ne_ids)
            _role_map[ne_ids[d]] = "dealer"
            _role_map[ne_ids[(d + 1) % n]] = "small blind"
            _role_map[ne_ids[(d + 2) % n]] = "big blind"

    # Count active players (not folded, not eliminated)
    active_players = [p for p in players if p.get("is_active") and not p.get("is_eliminated")]
    num_active = len(active_players)

    # Player list with folded/all-in/role tags
    def _player_tags(p: dict) -> str:
        pid = p.get("player_id", "")
        tags = []
        if p.get("is_eliminated"):
            tags.append("eliminated")
        elif not p.get("is_active", True):
            tags.append("folded")
        elif p.get("chips", 1) == 0:
            tags.append("all-in")
        if pid in _role_map:
            tags.append(_role_map[pid])
        return f" [{', '.join(tags)}]" if tags else ""

    players_str = "\n".join(
        "  - {}: {} chips, {} in this round{}".format(
            id_to_label.get(p.get("player_id", ""), "P?")
            + (" (YOU)" if p.get("player_id") == self_player_id else ""),
            p.get("chips", "?"),
            p.get("current_bet", 0),
            _player_tags(p),
        )
        for p in players
    )

    # Self stats
    self_player = next((p for p in players if p.get("player_id") == self_player_id), {})
    self_stack = self_player.get("chips", 0)
    current_bet_level = game_state.get("current_bet", 0)
    already_in = self_player.get("current_bet", 0)
    to_call = max(0, current_bet_level - already_in)

    # Effective stack: your stack vs. the largest active opponent stack
    opponent_stacks = [
        p.get("chips", 0) for p in active_players
        if p.get("player_id") != self_player_id
    ]
    effective_stack = min(self_stack, max(opponent_stacks)) if opponent_stacks else self_stack

    # Concrete raise example
    min_raise_total = current_bet_level + min_raise
    raise_example = f'e.g. minimum raise → {{"action": "raise", "amount": {min_raise_total}}}'

    valid_str = ", ".join(a.type.value for a in valid_actions)

    lines = [
        _PROMPT_GLOSSARY,
        "You are playing Texas Hold'em poker.",
        f"You are: {self_label}",
        f"Your hole cards: {hole_str}",
        f"Your stack: {self_stack}",
        f"Effective stack (you vs. largest opponent): {effective_stack}",
        f"Community cards: {community_str}",
        f"Betting round: {round_name}",
        f"Pot: {pot}",
        f"Blinds: {blinds[0]}/{blinds[1]}",
        f"Your position: {position}",
        f"Players remaining in hand: {num_active}",
        f"Min raise: {min_raise}",
        f"Max raise: {self_stack} (your entire stack — raising more than your stack is not allowed, use all_in instead)",
        f"You have already put {already_in} chips in this betting round. "
        + (f"You need to call {to_call} more chips to stay in."
           if to_call > 0 else
           "You have already matched the current bet (call cost is 0)."),
        f"Players:\n{players_str}",
    ]

    if profiles_block:
        lines.append(profiles_block)

    history_str = _serialize_history(history, name_to_label)
    if history_str:
        lines.append(f"History: {history_str}")

    lines.append(f"Valid actions: {valid_str}")
    lines.append(
        'Respond with a JSON object only — no markdown, no extra text:\n'
        '{"action": "<one of the valid actions>", "amount": <integer or null>, "reasoning": "<brief explanation in 1-3 sentences>"}\n'
        f'"amount" is the total size of YOUR bet this round (not the raise increment). {raise_example}. '
        'It must be null for all other actions.\n'
        '"action" must be exactly one of the valid action strings listed above.\n'
        'When mentioning cards in your reasoning, use unicode suit symbols (♠ ♥ ♦ ♣), e.g. "A♠ K♥".'
    )

    return "\n".join(lines)


def _extract_reasoning(text: str, action_keyword: str) -> str:
    """Return the text before the action keyword, stripped. Falls back to 'No reasoning provided'."""
    if not text:
        return "No reasoning provided"
    parts = text.split(action_keyword, 1)
    before = parts[0].strip()
    return before if before else "No reasoning provided"


def _parse_json_response(
    text: str,
    valid_actions: list[Action],
    min_raise: int,
    player_chips: int = 0,
) -> tuple[Action, str] | None:
    """Parse a JSON response from the LLM into an (Action, reasoning) tuple.

    Handles models that wrap JSON in markdown code fences.
    Returns None if the response cannot be parsed as valid JSON with the
    expected fields, so the caller can fall back to keyword parsing.
    """
    # Strip markdown code fences if present (```json ... ``` or ``` ... ```)
    stripped = text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence_match:
        stripped = fence_match.group(1).strip()
    # Strip bare "json" prefix some models emit without backticks
    stripped = re.sub(r"^json\s*", "", stripped, flags=re.IGNORECASE)

    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    action_str = str(data.get("action", "")).strip().lower()
    reasoning = str(data.get("reasoning", "")).strip() or "No reasoning provided"
    amount = data.get("amount")

    valid_types = {a.type for a in valid_actions}

    # Map action string to ActionType — includes single-letter codes some models use
    action_type_map = {
        "fold": ActionType.FOLD,   "f": ActionType.FOLD,
        "check": ActionType.CHECK, "x": ActionType.CHECK,
        "call": ActionType.CALL,   "c": ActionType.CALL,
        "raise": ActionType.RAISE, "r": ActionType.RAISE,
        "all_in": ActionType.ALL_IN, "all-in": ActionType.ALL_IN, "a": ActionType.ALL_IN,
    }
    action_type = action_type_map.get(action_str)
    if action_type is None:
        return None

    def _best_fallback() -> Action:
        for t in (ActionType.CHECK, ActionType.CALL, ActionType.FOLD):
            for a in valid_actions:
                if a.type == t:
                    return a
        return valid_actions[0]

    if action_type not in valid_types:
        return (_best_fallback(), reasoning)

    if action_type == ActionType.RAISE:
        try:
            raise_amount = int(amount) if amount is not None else min_raise
        except (TypeError, ValueError):
            raise_amount = min_raise
        # If the raise exceeds the player's stack, convert to all-in
        if player_chips > 0 and raise_amount > player_chips:
            if ActionType.ALL_IN in valid_types:
                for a in valid_actions:
                    if a.type == ActionType.ALL_IN:
                        return (a, reasoning)
            return (Action(type=ActionType.ALL_IN, amount=player_chips), reasoning)
        return (Action(type=ActionType.RAISE, amount=raise_amount), reasoning)

    for a in valid_actions:
        if a.type == action_type:
            return (a, reasoning)

    return (_best_fallback(), reasoning)


def _parse_action(
    text: str,
    valid_actions: list[Action],
    min_raise: int,
    player_chips: int = 0,
) -> Action | None:
    """Map LLM text response to an Action via keyword matching.

    The prompt instructs the model to place the action keyword AFTER the
    reasoning, so we scan only the last non-empty line to avoid false
    positives from action words that appear inside the reasoning text
    (e.g. "you risk being dominated by any raise").

    Priority: all_in > raise > call > check > fold.
    Falls back to the best available valid action if the preferred keyword
    isn't available.  Returns None only if no recognisable keyword is found.
    """
    # Use only the last non-empty line for keyword matching
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    action_line = lines[-1] if lines else text
    # Strip markdown bold markers some models add (e.g. **fold**)
    action_line = re.sub(r"\*+", "", action_line)
    text_lower = action_line.lower()
    valid_types = {a.type for a in valid_actions}

    def _best_fallback() -> Action | None:
        """Return the least aggressive valid action as a safe fallback."""
        for t in (ActionType.CHECK, ActionType.CALL, ActionType.FOLD):
            for a in valid_actions:
                if a.type == t:
                    return a
        return valid_actions[0] if valid_actions else None

    # all_in first — must check before "all" could match something else
    if re.search(r"\ball[_-]in\b", text_lower):
        if ActionType.ALL_IN in valid_types:
            for a in valid_actions:
                if a.type == ActionType.ALL_IN:
                    return a
        return _best_fallback()

    for keyword, action_type in [
        ("raise", ActionType.RAISE),
        ("call", ActionType.CALL),
        ("check", ActionType.CHECK),
        ("fold", ActionType.FOLD),
    ]:
        if keyword in text_lower:
            if action_type in valid_types:
                if action_type == ActionType.RAISE:
                    match = re.search(r"raise\s+(\d+)", text_lower) or re.search(r"raise\s+(\d+)", text.lower())
                    amount = int(match.group(1)) if match else min_raise
                    # If the raise exceeds the player's stack, convert to all-in
                    if player_chips > 0 and amount > player_chips:
                        if ActionType.ALL_IN in valid_types:
                            for a in valid_actions:
                                if a.type == ActionType.ALL_IN:
                                    return a
                        return Action(type=ActionType.ALL_IN, amount=player_chips)
                    return Action(type=ActionType.RAISE, amount=amount)
                for a in valid_actions:
                    if a.type == action_type:
                        return a
            # Keyword recognised but not valid — use best fallback silently
            return _best_fallback()

    return None


class OpenRouterPlayer(AIPlayer):
    """AI player backed by the OpenRouter API, configurable per-instance model."""

    def __init__(
        self,
        player_id: str,
        chips: int,
        model: str = SUPPORTED_MODELS[0],
        name: str | None = None,
    ) -> None:
        display_name = name if name is not None else _derive_display_name(model)
        super().__init__(player_id, display_name, chips, provider="openrouter")
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

        # Fetch opponent profiles if profiling_service is available
        profiles_block = None
        try:
            if self.profiling_service is not None and self.profiling_service.available:
                players = game_state.get("players", [])
                player_ids = [p.get("player_id", "") for p in players]
                id_to_label = {
                    p.get("player_id", ""): f"P{i}"
                    for i, p in enumerate(players, start=1)
                }
                game_id = game_state.get("session_id", "")
                profiles_block = self.profiling_service.get_opponent_profiles(
                    game_id=game_id,
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
            prompt = _build_prompt(game_state, valid_actions, history, self.player_id, profiles_block=profiles_block)
            logger.debug(
                "OpenRouterPlayer requesting action session=%s player=%s model=%s\nPROMPT:\n%s",
                session_id, self.player_id, self.model, prompt,
            )
            import eventlet
            call_start = time.monotonic()
            with eventlet.Timeout(20, TimeoutError):
                with OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY")) as client:
                    send_kwargs: dict = dict(
                        models=[self.model],
                        messages=[{"role": "user", "content": prompt}],
                        reasoning={"effort": "none"},
                    )
                    response = client.chat.send(**send_kwargs)
            call_elapsed_ms = int((time.monotonic() - call_start) * 1000)
            self.game_total_latency_ms += call_elapsed_ms
            text = response.choices[0].message.content or "" if response.choices else ""
            # Strip surrounding whitespace and quotes some models add
            text = text.strip().strip("'\"`")
            logger.debug(
                "OpenRouterPlayer raw response session=%s player=%s model=%s response=%r",
                session_id, self.player_id, self.model, text,
            )
            # Try JSON parsing first; fall back to keyword matching for non-compliant models
            self_player = next(
                (p for p in game_state.get("players", []) if p.get("player_id") == self.player_id),
                {},
            )
            player_chips = self_player.get("chips", 0)
            result = _parse_json_response(text, valid_actions, min_raise, player_chips=player_chips)
            if result is not None:
                action, reasoning = result
                logger.debug(
                    "OpenRouterPlayer action decided (json) session=%s player=%s action=%s amount=%s",
                    session_id, self.player_id, action.type.value, action.amount or "",
                )
                return (action, reasoning)
            # Fallback: keyword parsing
            action = _parse_action(text, valid_actions, min_raise, player_chips=player_chips)
            if action is not None:
                logger.debug(
                    "OpenRouterPlayer action decided (keyword) session=%s player=%s action=%s amount=%s",
                    session_id, self.player_id, action.type.value, action.amount or "",
                )
                action_keyword = action.type.value
                reasoning = _extract_reasoning(text, action_keyword)
                return (action, reasoning)
            logger.warning(
                "OpenRouterPlayer unparseable response session=%s player=%s model=%s response=%r\nPROMPT:\n%s",
                session_id, self.player_id, self.model, text, prompt,
            )
            return (_check_or_fold(), "No reasoning provided")
        except TimeoutError:
            self.game_api_failures += 1
            logger.error(
                "OpenRouterPlayer timed out (20s) session=%s player=%s model=%s",
                session_id, self.player_id, self.model,
            )
            return (_check_or_fold(), "⏱ Response timed out (20s) — defaulted to check/fold")
        except Exception as exc:
            self.game_api_failures += 1
            # Extract a clean, short error message — some library exceptions
            # embed the entire request body in their string representation.
            exc_str = str(exc)
            clean: str | None = None
            if "body.id" in exc_str and "'error'" in exc_str:
                import re as _re
                m = _re.search(r"'message':\s*'([^']+)'", exc_str)
                clean = m.group(1) if m else "upstream API error"
            if clean is None:
                # Truncate to avoid dumping the full prompt as the error
                clean = exc_str[:300] if len(exc_str) > 300 else exc_str
                # If the "error" still looks like the prompt, replace it
                if clean.startswith("PROMPT:") or clean.startswith("Action codes:"):
                    clean = f"{type(exc).__name__}: (error message contained prompt text)"
            logger.error(
                "OpenRouterPlayer API error session=%s player=%s model=%s error=%s",
                session_id, self.player_id, self.model, clean,
            )
            logger.debug(
                "OpenRouterPlayer failed prompt session=%s player=%s model=%s\nPROMPT:\n%s",
                session_id, self.player_id, self.model, prompt,
            )
            return (_check_or_fold(), "API error — defaulted to check/fold")

    def reset_game_stats(self) -> None:
        """Zero out per-game stat counters."""
        self.game_api_calls = 0
        self.game_api_failures = 0
        self.game_total_latency_ms = 0

    def __repr__(self) -> str:
        return (
            f"OpenRouterPlayer(id={self.player_id!r}, name={self.name!r}, "
            f"chips={self.chips}, model={self.model!r})"
        )
