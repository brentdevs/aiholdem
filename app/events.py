"""WebSocket event handlers for the Texas Hold 'Em poker platform."""
from __future__ import annotations

import logging

from flask import request
from flask_socketio import emit, join_room

from app import socketio
from app.arena.arena_manager import arena_manager, ARENA_SESSION_ID

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emit_error(code: str, message: str) -> None:
    logger.warning("Emitting error code=%s message=%r", code, message)
    emit("error", {"code": code, "message": message})


# ---------------------------------------------------------------------------
# Arena spectator events
# ---------------------------------------------------------------------------

@socketio.on("join_arena")
def on_join_arena(data: dict) -> None:
    logger.info("join_arena sid=%s", request.sid)
    join_room(ARENA_SESSION_ID)
    join_room(request.sid)
    arena_manager.on_viewer_join(request.sid)
    arena_manager.get_or_create_session()
    emit("arena_state", arena_manager.get_arena_state())
    arena_manager.broadcast_viewer_count()


# ---------------------------------------------------------------------------
# Disconnection handling
# ---------------------------------------------------------------------------

@socketio.on("disconnect")
def on_disconnect() -> None:
    sid = request.sid
    logger.debug("Client disconnected sid=%s", sid)

    # Handle arena viewer disconnect
    if sid in arena_manager._viewer_sids:
        arena_manager.on_viewer_leave(sid)
        arena_manager.broadcast_viewer_count()
