"""Flask application factory for the Texas Hold 'Em poker platform."""

import logging
import os

from flask import Flask
from flask_socketio import SocketIO

socketio = SocketIO()


def configure_logging() -> None:
    """Configure application-wide logging from environment variables.

    LOG_LEVEL: DEBUG | INFO | WARNING | ERROR (default: INFO)
    LOG_FORMAT: custom format string (optional)
    """
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

    # Quiet noisy third-party loggers
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("socketio").setLevel(logging.WARNING)
    logging.getLogger("eventlet").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """Create and configure the Flask application."""
    configure_logging()

    flask_app = Flask(__name__)
    flask_app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")
    socketio.init_app(flask_app, async_mode="eventlet", cors_allowed_origins="*")

    from app.arena.arena_manager import ARENA_PLAYER_MODELS, arena_manager
    from app.leaderboard.service import LeaderboardService
    from app.profiling.service import ProfilingService

    database_url = os.environ.get("DATABASE_URL")
    leaderboard_service = LeaderboardService(database_url)
    leaderboard_service.init_db()
    leaderboard_service.sync_retired_status(ARENA_PLAYER_MODELS)
    flask_app.leaderboard_service = leaderboard_service
    arena_manager.leaderboard_service = leaderboard_service

    profiling_service = ProfilingService(database_url)
    profiling_service.init_db()
    flask_app.profiling_service = profiling_service
    arena_manager.profiling_service = profiling_service

    try:
        arena_manager.get_or_create_session()
        logger.info("Arena session pre-created at startup")
        if not arena_manager._pause_on_empty:
            arena_manager._start_ai_loop()
            logger.info("Arena AI loop started at startup (ARENA_PAUSE_ON_EMPTY=false)")
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to pre-create arena session at startup: %s", exc)

    from app.routes import bp

    flask_app.register_blueprint(bp)

    import app.events  # noqa: F401 – side-effect import to register SocketIO handlers

    logger.info("Application started (LOG_LEVEL=%s)", os.environ.get("LOG_LEVEL", "INFO").upper())
    return flask_app
