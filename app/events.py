"""WebSocket event handlers for the Texas Hold 'Em poker platform."""

from __future__ import annotations

import logging

from app import sio
from app.arena.arena_manager import arena_managers, get_arena_manager

logger = logging.getLogger(__name__)


@sio.on("join_arena")
async def on_join_arena(sid: str, data: dict) -> None:
    lobby_id = (data or {}).get("lobby_id")
    logger.info("join_arena sid=%s lobby=%s", sid, lobby_id or "default")
    try:
        manager = get_arena_manager(lobby_id)
    except ValueError:
        await sio.emit("arena_error", {"error": "Unknown arena lobby"}, to=sid)
        return

    await sio.enter_room(sid, manager.session_id)
    manager.on_viewer_join(sid)
    manager.get_or_create_session()
    state = manager.get_arena_state()
    await sio.emit("arena_state", state, to=sid)
    await manager.broadcast_viewer_count()


@sio.on("disconnect")
async def on_disconnect(sid: str) -> None:
    logger.debug("Client disconnected sid=%s", sid)
    for manager in arena_managers.values():
        if sid in manager._viewer_sids:
            manager.on_viewer_leave(sid)
            await manager.broadcast_viewer_count()
