"""Arena managers for always-on AI spectator game sessions."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from app.ai.model_config import LobbyConfig, ModelConfig, load_lobby_configs
from app.ai.ollama_player import OllamaCloudPlayer
from app.ai.openrouter_player import OpenRouterPlayer
from app.game.game_session import BLIND_SCHEDULE, STARTING_CHIPS, GameSession
from app.game.models import ActionType, SessionStatus
from app.game.players import AIPlayer

logger = logging.getLogger(__name__)

LOBBY_CONFIGS: list[LobbyConfig] = load_lobby_configs()
DEFAULT_LOBBY_CONFIG = LOBBY_CONFIGS[0]

ARENA_SESSION_ID = DEFAULT_LOBBY_CONFIG.lobby_id

ARENA_PLAYER_CONFIGS: list[ModelConfig] = DEFAULT_LOBBY_CONFIG.players

ARENA_PLAYER_MODELS: list[str] = [config.model for config in ARENA_PLAYER_CONFIGS]
ALL_ARENA_PLAYER_MODELS: list[str] = [
    config.model for lobby in LOBBY_CONFIGS for config in lobby.players
]


class ArenaManager:
    def __init__(self, lobby_config: LobbyConfig | None = None) -> None:
        if lobby_config is None:
            lobby_config = DEFAULT_LOBBY_CONFIG
        self.lobby_config = lobby_config
        self.session_id = lobby_config.lobby_id
        self.name = lobby_config.name
        self.description = lobby_config.description
        self.player_configs = lobby_config.players
        self.session: GameSession | None = None
        self.viewer_count: int = 0
        self._pause_on_empty: bool = (
            os.environ.get("ARENA_PAUSE_ON_EMPTY", "true").lower() != "false"
        )
        self.paused: bool = self._pause_on_empty
        self._viewer_sids: set[str] = set()
        self._loop_running: bool = False
        self._inter_hand_pause_running: bool = False
        self._reset_running: bool = False
        self.leaderboard_service = None
        self.profiling_service = None
        self._elimination_order: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_create_session(self) -> GameSession:
        """Return existing session or create + start a new one."""
        if self.session is None:
            self.session = self._create_session()
        return self.session

    def on_viewer_join(self, socket_id: str) -> None:
        """Add socket to viewer set, update viewer_count, resume if was paused."""
        was_paused = self.paused
        self._viewer_sids.add(socket_id)
        self.viewer_count = len(self._viewer_sids)
        if was_paused and self.viewer_count > 0:
            self.paused = False
            logger.info(
                "Arena resumed — viewer joined sid=%s count=%d", socket_id, self.viewer_count
            )
            if self.session is not None and self.session.status == SessionStatus.ACTIVE:
                self.start_ai_loop()

    def on_viewer_leave(self, socket_id: str) -> None:
        """Remove socket from viewer set, update viewer_count, pause if zero."""
        self._viewer_sids.discard(socket_id)
        self.viewer_count = len(self._viewer_sids)
        if self.viewer_count == 0 and self._pause_on_empty:
            self.paused = True
            logger.info("Arena paused — no viewers remaining")

    async def broadcast_state(self) -> None:
        """Emit arena_state to the arena room."""
        from app import sio

        if self.session is None:
            return
        state = self.get_arena_state()
        await sio.emit("arena_state", state, room=self.session_id)

    def get_arena_state(self) -> dict:
        """Return spectator state — all hole cards visible."""
        if self.session is None:
            return {}

        state = self.session.get_public_state()
        state["lobby"] = self.lobby_config.as_dict()
        player_map = {p.player_id: p for p in self.session.players}
        for player_entry in state["players"]:
            pid = player_entry["player_id"]
            p = player_map.get(pid)
            if p and p.hole_cards:
                player_entry["hole_cards"] = [
                    {"rank": c.rank, "suit": c.suit} for c in p.hole_cards
                ]
        state["live_move_logs"] = [
            {
                "player_name": log.player_name,
                "phase": log.phase,
                "action": log.action,
                "amount": log.amount,
                "reasoning": log.reasoning,
            }
            for log in self.session._hand_move_logs
        ]
        return state

    async def broadcast_viewer_count(self) -> None:
        """Emit arena_viewer_count to the arena room."""
        from app import sio

        await sio.emit("arena_viewer_count", {"count": self.viewer_count}, room=self.session_id)

    # ------------------------------------------------------------------
    # AI game loop
    # ------------------------------------------------------------------

    def start_ai_loop(self) -> None:
        """Schedule the AI loop as an asyncio task."""
        if self._loop_running:
            return
        if self._inter_hand_pause_running or self._reset_running:
            return
        session = self.session
        if session is None:
            return
        self._loop_running = True
        asyncio.ensure_future(self._dispatch_ai_turn(session))

    def _mark_loop_idle(self, session: GameSession | None = None) -> None:
        if session is None or session is self.session:
            self._loop_running = False

    async def _dispatch_ai_turn(self, session: GameSession) -> None:
        """Run AI player turns in a loop until the hand ends or pauses."""
        while True:
            if self.paused:
                self._mark_loop_idle(session)
                return

            if session is not self.session:
                self._mark_loop_idle(session)
                return

            if session.status == SessionStatus.COMPLETE:
                await self._schedule_reset_after_complete(session)
                return

            if session.showdown_pending:
                await self._schedule_inter_hand_pause(session)
                return

            if session.status != SessionStatus.ACTIVE or session.current_hand is None:
                self._mark_loop_idle(session)
                return

            active = session._get_active_players()
            if not active:
                self._mark_loop_idle(session)
                return

            hand = session.current_hand
            current = next((p for p in active if p.player_id == hand.current_player_id), None)

            skip_count = 0
            while current is not None and current.chips == 0:
                skip_count += 1
                if skip_count > len(session.players):
                    logger.warning(
                        "Arena all active players are all-in session=%s, advancing phase",
                        session.session_id,
                    )
                    session._advance_phase_if_needed()
                    await self.broadcast_state()
                    continue
                logger.debug("Arena skipping all-in player %s", current.player_id)
                hand.current_player_id = session._next_active_player_id(current.player_id, active)
                current = next((p for p in active if p.player_id == hand.current_player_id), None)

            if current is None or not isinstance(current, AIPlayer):
                self._mark_loop_idle(session)
                return

            await self._run_ai_turn(session, current)

    async def _run_ai_turn(self, session: GameSession, ai_player: AIPlayer) -> None:
        """Execute one AI turn, broadcast state, then chain to next."""
        await asyncio.sleep(0)

        if self.paused or session is not self.session:
            self._mark_loop_idle(session)
            return

        start = time.monotonic()
        logger.debug(
            "Arena AI turn session=%s player=%s model=%s",
            session.session_id,
            ai_player.player_id,
            getattr(ai_player, "model", "?"),
        )

        game_state = session.get_player_state(ai_player.player_id)
        valid_actions = session.get_valid_actions(ai_player)
        action, reasoning = await ai_player.decide_action(game_state, valid_actions)

        # Enforce minimum 6s turn duration
        elapsed = time.monotonic() - start
        remaining = 6.0 - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

        if self.paused or session is not self.session:
            self._mark_loop_idle(session)
            return

        try:
            session.apply_action(ai_player.player_id, action, reasoning=reasoning)
        except ValueError as exc:
            logger.error(
                "Arena AI action failed session=%s player=%s error=%s",
                session.session_id,
                ai_player.player_id,
                exc,
            )
            fallback = None
            fallback_actions = session.get_valid_actions(ai_player)
            for t in (ActionType.CHECK, ActionType.FOLD):
                for a in fallback_actions:
                    if a.type == t:
                        fallback = a
                        break
                if fallback:
                    break
            if fallback is None and fallback_actions:
                fallback = fallback_actions[0]
            if fallback is not None:
                try:
                    session.apply_action(
                        ai_player.player_id,
                        fallback,
                        reasoning="Fallback — original action was invalid",
                    )
                except ValueError as fallback_exc:
                    logger.error(
                        "Arena AI fallback also failed session=%s player=%s error=%s",
                        session.session_id,
                        ai_player.player_id,
                        fallback_exc,
                    )

        self._track_eliminations()
        await self.broadcast_state()

    async def _schedule_inter_hand_pause(self, session: GameSession) -> None:
        """Start the inter-hand pause once for a given showdown."""
        if session is not self.session:
            return
        if self._inter_hand_pause_running or self._reset_running:
            return
        self._loop_running = False
        self._inter_hand_pause_running = True
        await self._inter_hand_pause()

    async def _schedule_reset_after_complete(self, session: GameSession) -> None:
        """Start game reset once for a completed session."""
        if session is not self.session:
            return
        if self._reset_running:
            return
        self._loop_running = False
        self._reset_running = True
        await self._reset_after_complete()

    async def _inter_hand_pause(self) -> None:
        """Broadcast showdown state, sleep 10s, then start next hand."""
        from app import sio

        session = self.session
        if session is None:
            self._inter_hand_pause_running = False
            return

        state = session.get_public_state()
        state["showdown_pending"] = True
        state["inter_hand_ends_at"] = time.time() + 10
        state["live_move_logs"] = [
            {
                "player_name": log.player_name,
                "phase": log.phase,
                "action": log.action,
                "amount": log.amount,
                "reasoning": log.reasoning,
            }
            for log in session._hand_move_logs
        ]
        state["lobby"] = self.lobby_config.as_dict()
        await sio.emit("arena_state", state, room=self.session_id)

        await asyncio.sleep(10)

        if self.viewer_count == 0 and self._pause_on_empty:
            self.paused = True
            self._loop_running = False
            self._inter_hand_pause_running = False
            logger.info("Arena paused after inter-hand pause — no viewers")
            return

        try:
            session.next_hand()
        except ValueError as exc:
            logger.error("Arena next_hand failed: %s", exc)
            self._loop_running = False
            self._inter_hand_pause_running = False
            return
        await self.broadcast_state()
        self._inter_hand_pause_running = False
        self._loop_running = False
        self.start_ai_loop()

    async def _reset_after_complete(self) -> None:
        """Broadcast game-complete state, record results, sleep 10s, create new session."""
        await self.broadcast_state()

        self._track_eliminations()
        if self.session is not None:
            for p in self.session.players:
                if not p.is_eliminated and p.player_id not in self._elimination_order:
                    self._elimination_order.append(p.player_id)

        # Record game results to leaderboard
        if (
            self.session is not None
            and self.leaderboard_service is not None
            and self.leaderboard_service.available
        ):
            try:
                from app.leaderboard.models import GameResult, compute_placings

                placings = compute_placings(self._elimination_order)
                results: list[GameResult] = []
                for p in self.session.players:
                    if isinstance(p, AIPlayer) and hasattr(p, "model"):
                        results.append(
                            GameResult(
                                model_id=p.model,
                                display_name=p.name,
                                placing=placings.get(p.player_id, len(self.session.players)),
                                api_calls=p.game_api_calls,
                                api_failures=p.game_api_failures,
                                total_latency_ms=p.game_total_latency_ms,
                            )
                        )
                self.leaderboard_service.record_game_results(results, lobby_id=self.session_id)
                logger.info("Recorded arena game results for %d players", len(results))
            except Exception as exc:
                logger.error("Failed to record arena game results: %s", exc)

        # Record game end for profiling
        if (
            self.session is not None
            and self.profiling_service is not None
            and self.profiling_service.available
        ):
            try:
                from app.leaderboard.models import compute_placings

                placings = compute_placings(self._elimination_order)
                player_results = []
                for p in self.session.players:
                    result = {
                        "player_id": p.player_id,
                        "finish_position": placings.get(p.player_id, len(self.session.players)),
                        "final_stack": p.chips,
                        "buy_in": STARTING_CHIPS,
                        "net_profit": p.chips - STARTING_CHIPS,
                    }
                    if isinstance(p, AIPlayer) and hasattr(p, "model"):
                        result.update(
                            {
                                "model_id": p.model,
                                "display_name": p.name,
                                "api_calls": p.game_api_calls,
                                "api_failures": p.game_api_failures,
                                "latency_sum_ms": p.game_total_latency_ms,
                            }
                        )
                    player_results.append(result)
                self.profiling_service.record_game_end(
                    game_id=self.session.profiling_game_id,
                    player_results=player_results,
                )
            except Exception as exc:
                logger.error("Failed to record arena game end: %s", exc)

        await asyncio.sleep(10)

        new_session = self._create_session()
        self.session = new_session
        self._loop_running = False
        self._reset_running = False

        if self.viewer_count == 0 and self._pause_on_empty:
            self.paused = True
            logger.info("Arena paused after game reset — no viewers")
        else:
            self.start_ai_loop()

        await self.broadcast_state()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _track_eliminations(self) -> None:
        """Track newly eliminated players for placing computation."""
        if self.session is None:
            return
        for p in self.session.players:
            if p.is_eliminated and p.player_id not in self._elimination_order:
                self._elimination_order.append(p.player_id)

    def _create_session(self) -> GameSession:
        """Create GameSession with the configured lobby AI players."""
        self._elimination_order = []
        session = GameSession(self.session_id, self.name)
        session.profiling_service = self.profiling_service
        for config in self.player_configs:
            model = config.model
            backend = config.backend
            safe_model = model.replace("/", "_").replace(":", "_").replace(".", "_")
            player_id = f"{self.session_id}_{backend}_{safe_model}"
            if backend == "ollama":
                player = OllamaCloudPlayer(
                    player_id=player_id,
                    chips=STARTING_CHIPS,
                    model=model,
                )
            elif backend == "openrouter":
                player = OpenRouterPlayer(
                    player_id=player_id,
                    chips=STARTING_CHIPS,
                    model=model,
                )
            else:
                raise ValueError(f"Unsupported AI backend: {backend}")
            player.profiling_service = self.profiling_service
            player.reset_game_stats()
            session.add_player(player)
        session.start_game()
        logger.info(
            "Arena session created lobby=%s players=%d",
            self.session_id,
            len(session.players),
        )

        # Record game start for profiling
        if self.profiling_service is not None and self.profiling_service.available:
            try:
                players_data = [
                    {
                        "player_id": p.player_id,
                        "label": p.name,
                        "model": getattr(p, "model", ""),
                        "config": None,
                    }
                    for p in session.players
                ]
                self.profiling_service.record_game_start(
                    game_id=session.profiling_game_id,
                    lobby_id=self.session_id,
                    game_type=f"arena:{self.session_id}",
                    blind_structure=BLIND_SCHEDULE,
                    num_players=len(session.players),
                    players=players_data,
                )
            except Exception as exc:
                logger.error("Failed to record arena game start: %s", exc)

        return session


arena_managers: dict[str, ArenaManager] = {
    lobby.lobby_id: ArenaManager(lobby) for lobby in LOBBY_CONFIGS
}

# Backward-compatible default manager and constants.
arena_manager = arena_managers[DEFAULT_LOBBY_CONFIG.lobby_id]


def get_arena_manager(lobby_id: str | None = None) -> ArenaManager:
    key = lobby_id or DEFAULT_LOBBY_CONFIG.lobby_id
    try:
        return arena_managers[key]
    except KeyError as exc:
        raise ValueError(f"Unknown arena lobby: {key}") from exc


def get_lobby_summaries() -> list[dict]:
    summaries: list[dict] = []
    for lobby in LOBBY_CONFIGS:
        manager = arena_managers[lobby.lobby_id]
        provider_counts: dict[str, int] = {}
        for player in lobby.players:
            provider_counts[player.backend] = provider_counts.get(player.backend, 0) + 1
        session = manager.session
        hand_number = session.hand_number if session is not None else 0
        is_live = manager.viewer_count > 0 and not manager.paused
        summaries.append(
            {
                "lobby_id": lobby.lobby_id,
                "name": lobby.name,
                "description": lobby.description,
                "players": [player.as_dict() for player in lobby.players],
                "player_count": len(lobby.players),
                "provider_counts": provider_counts,
                "viewer_count": manager.viewer_count,
                "is_paused": manager.paused,
                "is_live": is_live,
                "status_label": "Live" if is_live else "Paused",
                "hand_number": hand_number,
                "url": f"/{lobby.lobby_id}",
            }
        )
    return summaries
