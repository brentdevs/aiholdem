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


logger = logging.getLogger(__name__)


def create_app() -> Quart:
    """Create and configure the Quart application."""
    configure_logging()

    quart_app = Quart(__name__)
    quart_app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")

    from app.arena.arena_manager import arena_managers
    from app.leaderboard.service import LeaderboardService
    from app.profiling.service import ProfilingService

    database_url = os.environ.get("DATABASE_URL")
    leaderboard_service = LeaderboardService(database_url)
    leaderboard_service.init_db()
    quart_app.leaderboard_service = leaderboard_service  # type: ignore[attr-defined]

    profiling_service = ProfilingService(database_url)
    profiling_service.init_db()
    quart_app.profiling_service = profiling_service  # type: ignore[attr-defined]

    for lobby_id, manager in arena_managers.items():
        manager.leaderboard_service = leaderboard_service
        manager.profiling_service = profiling_service
        leaderboard_service.sync_retired_status(
            [config.model for config in manager.player_configs],
            lobby_id,
        )
        try:
            manager.get_or_create_session()
            logger.info("Arena session pre-created at startup lobby=%s", lobby_id)
            if not manager._pause_on_empty:
                manager.start_ai_loop()
                logger.info(
                    "Arena AI loop started at startup lobby=%s (ARENA_PAUSE_ON_EMPTY=false)",
                    lobby_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to pre-create arena session lobby=%s: %s", lobby_id, exc)

    from app.routes import LobbyConverter, bp

    quart_app.url_map.converters["lobby"] = LobbyConverter
    quart_app.register_blueprint(bp)

    import app.events  # noqa: F401

    logger.info("Application started (LOG_LEVEL=%s)", os.environ.get("LOG_LEVEL", "INFO").upper())
    return quart_app
