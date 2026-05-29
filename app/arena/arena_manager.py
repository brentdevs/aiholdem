"""Arena manager — singleton that owns the always-on AI spectator game session."""

from __future__ import annotations

import logging
import os
import threading
import time

import eventlet

from app.ai.model_config import ModelConfig, load_arena_player_configs
from app.ai.ollama_player import OllamaCloudPlayer
from app.ai.openrouter_player import OpenRouterPlayer
from app.game.game_session import BLIND_SCHEDULE, STARTING_CHIPS, GameSession
from app.game.models import ActionType, SessionStatus
from app.game.players import AIPlayer

logger = logging.getLogger(__name__)

ARENA_SESSION_ID = "arena"

ARENA_PLAYER_CONFIGS: list[ModelConfig] = load_arena_player_configs()

ARENA_PLAYER_MODELS: list[str] = [config.model for config in ARENA_PLAYER_CONFIGS]


class ArenaManager:
    def __init__(self) -> None:
        self.session: GameSession | None = None
        self.viewer_count: int = 0
        self._pause_on_empty: bool = (
            os.environ.get("ARENA_PAUSE_ON_EMPTY", "true").lower() != "false"
        )
        self.paused: bool = self._pause_on_empty  # start unpaused if pause-on-empty is disabled
        self._viewer_sids: set[str] = set()
        self._lock: threading.RLock = threading.RLock()
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
        with self._lock:
            if self.session is None:
                self.session = self._create_session()
            return self.session

    def on_viewer_join(self, socket_id: str) -> None:
        """Add socket to viewer set, update viewer_count, resume if was paused."""
        with self._lock:
            was_paused = self.paused
            self._viewer_sids.add(socket_id)
            self.viewer_count = len(self._viewer_sids)
            if was_paused and self.viewer_count > 0:
                self.paused = False
                logger.info(
                    "Arena resumed — viewer joined sid=%s count=%d", socket_id, self.viewer_count
                )
                # Resume AI loop if session is active
                if self.session is not None and self.session.status == SessionStatus.ACTIVE:
                    self._start_ai_loop()

    def on_viewer_leave(self, socket_id: str) -> None:
        """Remove socket from viewer set, update viewer_count, pause if zero."""
        with self._lock:
            self._viewer_sids.discard(socket_id)
            self.viewer_count = len(self._viewer_sids)
            if self.viewer_count == 0 and self._pause_on_empty:
                self.paused = True
                logger.info("Arena paused — no viewers remaining")

    def broadcast_state(self) -> None:
        """Emit arena_state to the arena room with current session public state."""
        from app import socketio

        if self.session is None:
            return
        state = self.get_arena_state()
        socketio.emit("arena_state", state, room=ARENA_SESSION_ID)

    def get_arena_state(self) -> dict:
        """Return spectator state for the all-AI arena.

        Unlike the generic public state, arena spectators can see every AI
        player's hole cards throughout the hand.
        """
        if self.session is None:
            return {}

        state = self.session.get_public_state()
        # Always expose hole cards for all players (arena is all-AI, no privacy needed)
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

    def broadcast_viewer_count(self) -> None:
        """Emit arena_viewer_count with current count to the arena room."""
        from app import socketio

        socketio.emit("arena_viewer_count", {"count": self.viewer_count}, room=ARENA_SESSION_ID)

    # ------------------------------------------------------------------
    # AI game loop
    # ------------------------------------------------------------------

    def _start_ai_loop(self) -> None:
        """Dispatch first AI turn via socketio.start_background_task.
        Guards against starting a second concurrent loop."""
        with self._lock:
            if self._loop_running:
                logger.debug("Arena AI loop already running — skipping duplicate start")
                return
            if self._inter_hand_pause_running or self._reset_running:
                logger.debug("Arena transition task already running — skipping AI loop start")
                return
            session = self.session
            if session is None:
                return
            self._loop_running = True
        from app import socketio

        socketio.start_background_task(self._dispatch_ai_turn, session)

    def _mark_loop_idle(self, session: GameSession | None = None) -> None:
        """Clear the active-loop flag unless a newer session has replaced this task."""
        with self._lock:
            if session is None or session is self.session:
                self._loop_running = False

    def _schedule_inter_hand_pause(self, session: GameSession) -> None:
        """Start the inter-hand pause once for a given showdown."""
        with self._lock:
            if session is not self.session:
                return
            if self._inter_hand_pause_running:
                logger.debug("Arena inter-hand pause already running — skipping duplicate start")
                return
            if self._reset_running:
                logger.debug("Arena reset already running — skipping inter-hand pause")
                return
            self._loop_running = False
            self._inter_hand_pause_running = True
        from app import socketio

        socketio.start_background_task(self._inter_hand_pause)

    def _schedule_reset_after_complete(self, session: GameSession) -> None:
        """Start game reset once for a completed session."""
        with self._lock:
            if session is not self.session:
                return
            if self._reset_running:
                logger.debug("Arena reset already running — skipping duplicate start")
                return
            self._loop_running = False
            self._reset_running = True
        from app import socketio

        socketio.start_background_task(self._reset_after_complete)

    def _dispatch_ai_turn(self, session: GameSession) -> None:
        """Run the current AI player's turn, broadcast state, and chain to the next turn."""
        if self.paused:
            self._mark_loop_idle(session)
            return

        # Session may have been replaced (e.g. after reset) — bail if stale
        if session is not self.session:
            self._mark_loop_idle(session)
            return

        if session.status == SessionStatus.COMPLETE:
            self._schedule_reset_after_complete(session)
            return

        if session.showdown_pending:
            self._schedule_inter_hand_pause(session)
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
        # Skip all-in players — they have no action to take.
        # Use a loop instead of recursion to avoid stack overflow when the
        # game session keeps pointing at the same all-in player.
        skip_count = 0
        while current is not None and current.chips == 0:
            skip_count += 1
            if skip_count > len(session.players):
                # All remaining players are all-in — force phase advancement
                logger.warning(
                    "Arena all active players are all-in session=%s, advancing phase",
                    session.session_id,
                )
                session._advance_phase_if_needed()
                self.broadcast_state()
                # Re-enter dispatch from the top (non-recursively via background task)
                from app import socketio

                socketio.start_background_task(self._dispatch_ai_turn, session)
                return
            logger.debug("Arena skipping all-in player %s", current.player_id)
            # Advance current_player_id in the session so we don't loop on the same player
            hand.current_player_id = session._next_active_player_id(current.player_id, active)
            current = next((p for p in active if p.player_id == hand.current_player_id), None)
        if current is None:
            self._mark_loop_idle(session)
            return
        if not isinstance(current, AIPlayer):
            self._mark_loop_idle(session)
            return

        # Run the AI turn in a background task
        from app import socketio

        socketio.start_background_task(self._run_ai_turn, session, current)

    def _run_ai_turn(self, session: GameSession, ai_player: AIPlayer) -> None:
        """Execute one AI turn, broadcast state, then chain to the next turn."""
        eventlet.sleep(0)  # yield to event loop

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
        action, reasoning = ai_player.decide_action(game_state, valid_actions)

        # Enforce minimum 6s turn duration
        elapsed = time.monotonic() - start
        remaining = 6.0 - elapsed
        if remaining > 0:
            eventlet.sleep(remaining)

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
            # Force a safe fallback action so we don't retry the same player forever
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
        self.broadcast_state()

        # Chain: showdown → inter-hand pause; complete → reset; else next turn
        if session.status == SessionStatus.COMPLETE:
            self._schedule_reset_after_complete(session)
        elif session.showdown_pending:
            self._schedule_inter_hand_pause(session)
        else:
            self._dispatch_ai_turn(session)

    def _inter_hand_pause(self) -> None:
        """Background task: broadcast showdown state, sleep 10s, then start next hand."""
        from app import socketio

        session = self.session
        if session is None:
            with self._lock:
                self._inter_hand_pause_running = False
            return

        # Build showdown state with inter_hand_ends_at timestamp
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
        socketio.emit("arena_state", state, room=ARENA_SESSION_ID)

        eventlet.sleep(10)

        if self.viewer_count == 0 and self._pause_on_empty:
            with self._lock:
                self.paused = True
                self._loop_running = False
                self._inter_hand_pause_running = False
            logger.info("Arena paused after inter-hand pause — no viewers")
            return

        try:
            session.next_hand()
        except ValueError as exc:
            logger.error("Arena next_hand failed: %s", exc)
            with self._lock:
                self._loop_running = False
                self._inter_hand_pause_running = False
            return
        self.broadcast_state()
        with self._lock:
            self._inter_hand_pause_running = False
            self._loop_running = True
        self._dispatch_ai_turn(session)

    def _reset_after_complete(self) -> None:
        """Background task: broadcast game-complete state, record results, sleep 10s, create new session."""
        # Broadcast the game-complete state
        self.broadcast_state()

        # Track final eliminations and add the winner
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
                self.leaderboard_service.record_game_results(results)
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
                player_results = [
                    {
                        "player_id": p.player_id,
                        "finish_position": placings.get(p.player_id, len(self.session.players)),
                        "final_stack": p.chips,
                        "buy_in": STARTING_CHIPS,
                        "net_profit": p.chips - STARTING_CHIPS,
                    }
                    for p in self.session.players
                ]
                self.profiling_service.record_game_end(
                    game_id=self.session.profiling_game_id,
                    player_results=player_results,
                )
            except Exception as exc:
                logger.error("Failed to record arena game end: %s", exc)

        eventlet.sleep(10)

        # Replace session with a fresh one
        new_session = self._create_session()
        with self._lock:
            self.session = new_session
            self._loop_running = False
            self._reset_running = False

        if self.viewer_count == 0 and self._pause_on_empty:
            with self._lock:
                self.paused = True
            logger.info("Arena paused after game reset — no viewers")
        else:
            self._start_ai_loop()

        self.broadcast_state()

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
        """Create GameSession with ARENA_SESSION_ID and configured AI players."""
        self._elimination_order = []
        session = GameSession(ARENA_SESSION_ID, "arena")
        session.profiling_service = self.profiling_service
        for config in ARENA_PLAYER_CONFIGS:
            model = config.model
            backend = config.backend
            # Use model-based player_id so profiling stats stay tied to the model,
            # not the slot index. Swapping a model starts with a clean profile.
            safe_model = model.replace("/", "_").replace(":", "_").replace(".", "_")
            player_id = f"arena_{backend}_{safe_model}"
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
        logger.info("Arena session created with %d players", len(session.players))

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
                    game_type="arena",
                    blind_structure=BLIND_SCHEDULE,
                    num_players=len(session.players),
                    players=players_data,
                )
            except Exception as exc:
                logger.error("Failed to record arena game start: %s", exc)

        return session


# Module-level singleton
arena_manager = ArenaManager()
