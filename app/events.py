"""WebSocket event handlers for the Texas Hold 'Em poker platform."""

from __future__ import annotations

import logging

from app import sio
from app.arena.arena_manager import ARENA_SESSION_ID, arena_manager

logger = logging.getLogger(__name__)


@sio.on("join_arena")
async def on_join_arena(sid: str, data: dict) -> None:
    logger.info("join_arena sid=%s", sid)
    await sio.enter_room(sid, ARENA_SESSION_ID)
    arena_manager.on_viewer_join(sid)
    arena_manager.get_or_create_session()
    state = arena_manager.get_arena_state()
    await sio.emit("arena_state", state, to=sid)
    await arena_manager.broadcast_viewer_count()


@sio.on("disconnect")
async def on_disconnect(sid: str) -> None:
    logger.debug("Client disconnected sid=%s", sid)
    if sid in arena_manager._viewer_sids:
        arena_manager.on_viewer_leave(sid)
        await arena_manager.broadcast_viewer_count()
