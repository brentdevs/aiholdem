"""Quart application factory for the Texas Hold 'Em poker platform."""

import logging
import os

import socketio
from quart import Quart

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


def configure_logging() -> None:
    """Configure application-wide logging from environment variables."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_format = os.environ.get(
        "LOG_FORMAT",
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("socketio").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(level)


logger = logging.getLogger(__name__)


def create_app() -> Quart:
    """Create and configure the Quart application."""
    configure_logging()

    quart_app = Quart(__name__)
    quart_app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")

    from app.arena.arena_manager import ARENA_PLAYER_MODELS, arena_manager
    from app.leaderboard.service import LeaderboardService
    from app.profiling.service import ProfilingService

    database_url = os.environ.get("DATABASE_URL")
    leaderboard_service = LeaderboardService(database_url)
    leaderboard_service.init_db()
    leaderboard_service.sync_retired_status(ARENA_PLAYER_MODELS)
    quart_app.leaderboard_service = leaderboard_service  # type: ignore[attr-defined]
    arena_manager.leaderboard_service = leaderboard_service

    profiling_service = ProfilingService(database_url)
    profiling_service.init_db()
    quart_app.profiling_service = profiling_service  # type: ignore[attr-defined]
    arena_manager.profiling_service = profiling_service

    try:
        arena_manager.get_or_create_session()
        logger.info("Arena session pre-created at startup")
        if not arena_manager._pause_on_empty:
            arena_manager.start_ai_loop()
            logger.info("Arena AI loop started at startup (ARENA_PAUSE_ON_EMPTY=false)")
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to pre-create arena session at startup: %s", exc)

    from app.routes import bp

    quart_app.register_blueprint(bp)

    import app.events  # noqa: F401

    logger.info("Application started (LOG_LEVEL=%s)", os.environ.get("LOG_LEVEL", "INFO").upper())
    return quart_app
