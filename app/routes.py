"""HTTP routes for the Texas Hold 'Em poker platform."""

import logging

from quart import Blueprint, Response, current_app, jsonify, render_template, request
from werkzeug.routing import BaseConverter
from werkzeug.routing.converters import ValidationError

from app.ai.model_config import get_supported_model_configs
from app.arena.arena_manager import (
    ARENA_PLAYER_CONFIGS,
    LOBBY_CONFIGS,
    get_arena_manager,
    get_lobby_summaries,
)

logger = logging.getLogger(__name__)

bp = Blueprint("main", __name__)


class UnknownLobbyError(ValueError):
    pass


class LobbyConverter(BaseConverter):
    """URL converter that only matches known arena lobby slugs.

    Unknown values fail to match the route, falling through to a normal 404
    instead of being swallowed by the catch-all shortlink route.
    """

    def to_python(self, value: str) -> str:
        if value not in {lobby.lobby_id for lobby in LOBBY_CONFIGS}:
            raise ValidationError(f"Unknown arena lobby: {value}")
        return value

    def to_url(self, value: str) -> str:
        return str(value)


async def _render_lobby(lobby_id: str):
    try:
        manager = get_arena_manager(lobby_id)
    except ValueError:
        return Response("Arena lobby not found", status=404)
    return await render_template(
        "arena.html",
        lobby=manager.lobby_config,
        arena_players=manager.player_configs,
    )


def _get_model_lobby_scope() -> str:
    requested = request.args.get("lobby_id", LOBBY_CONFIGS[0].lobby_id)
    if requested == "all":
        raise UnknownLobbyError(requested)
    try:
        get_arena_manager(requested)
    except ValueError as exc:
        raise UnknownLobbyError(requested) from exc
    return requested


def _bounded_query_arg(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = request.args.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


# ── Favicon ───────────────────────────────────────────────────────────────────

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect x="12" y="20" width="40" height="34" rx="8" fill="#2a2a3e" stroke="#7ecbff" stroke-width="2"/>
  <rect x="22" y="10" width="20" height="14" rx="4" fill="#2a2a3e" stroke="#7ecbff" stroke-width="2"/>
  <line x1="32" y1="4" x2="32" y2="10" stroke="#7ecbff" stroke-width="2"/>
  <circle cx="32" cy="4" r="3" fill="#7ecbff"/>
  <circle cx="23" cy="33" r="5" fill="#7ecbff"/>
  <circle cx="41" cy="33" r="5" fill="#7ecbff"/>
  <path d="M32 40c-2 3-7 5-7 9a7 7 0 0 0 6 6.9V57h-1.5v2h5v-2H33v-1.1A7 7 0 0 0 39 49c0-4-5-6-7-9z" fill="#ffd700"/>
  <rect x="6" y="30" width="6" height="10" rx="3" fill="#2a2a3e" stroke="#7ecbff" stroke-width="1.5"/>
  <rect x="52" y="30" width="6" height="10" rx="3" fill="#2a2a3e" stroke="#7ecbff" stroke-width="1.5"/>
</svg>"""


@bp.route("/favicon.svg")
async def favicon():
    return Response(FAVICON_SVG, mimetype="image/svg+xml")


@bp.route("/")
async def index():
    return await render_template("lobbies.html", lobbies=get_lobby_summaries())


@bp.route("/arena")
async def arena():
    default_lobby = LOBBY_CONFIGS[0]
    return await render_template(
        "arena.html",
        lobby=default_lobby,
        arena_players=default_lobby.players,
    )


@bp.route("/arena/<lobby_id>")
async def arena_lobby(lobby_id: str):
    return await _render_lobby(lobby_id)


@bp.route("/faq")
async def faq():
    return await render_template(
        "faq.html",
        arena_players=ARENA_PLAYER_CONFIGS,
        arena_player_count=len(ARENA_PLAYER_CONFIGS),
    )


@bp.route("/leaderboard")
async def leaderboard():
    return await render_template(
        "leaderboard.html",
        lobbies=get_lobby_summaries(),
        current_lobby_id=request.args.get("lobby_id", "all"),
    )


@bp.route("/leaderboard/<lobby_id>")
async def leaderboard_lobby(lobby_id: str):
    try:
        get_arena_manager(lobby_id)
    except ValueError:
        return Response("Arena lobby not found", status=404)
    return await render_template(
        "leaderboard.html",
        lobbies=get_lobby_summaries(),
        current_lobby_id=lobby_id,
    )


@bp.route("/api/leaderboard")
async def api_leaderboard():
    service = current_app.leaderboard_service
    if not service.available:
        return jsonify({"error": "Leaderboard unavailable"}), 503
    lobby_id = request.args.get("lobby_id")
    if lobby_id == "all":
        lobby_id = None
    if lobby_id is not None:
        try:
            get_arena_manager(lobby_id)
        except ValueError:
            return jsonify({"error": "Arena lobby not found"}), 404
    return jsonify(service.get_leaderboard(lobby_id=lobby_id))


@bp.route("/models/<path:model_id>")
async def model_stats(model_id: str):
    try:
        current_lobby_id = _get_model_lobby_scope()
    except UnknownLobbyError:
        return Response("Arena lobby not found", status=404)
    active_provider_links = {}
    provider_names = {"openrouter": "OpenRouter", "ollama": "Ollama"}
    for lobby in LOBBY_CONFIGS:
        matches = [player for player in lobby.players if player.model_id == model_id]
        if len(matches) == 1:
            player = matches[0]
            active_provider_links[lobby.lobby_id] = {
                "url": player.provider_url,
                "name": provider_names.get(player.backend, player.backend),
            }
    return await render_template(
        "model_stats.html",
        model_id=model_id,
        lobbies=get_lobby_summaries(),
        current_lobby_id=current_lobby_id,
        active_provider_links=active_provider_links,
        current_provider_link=active_provider_links.get(current_lobby_id),
    )


@bp.route("/api/models/<path:model_id>/summary")
async def api_model_summary(model_id: str):
    try:
        lobby_id = _get_model_lobby_scope()
    except UnknownLobbyError:
        return jsonify({"error": "Arena lobby not found"}), 404
    service = current_app.leaderboard_service
    if not service.available:
        return jsonify({"error": "Leaderboard unavailable"}), 503
    summary = service.get_model_summary(model_id, lobby_id=lobby_id)
    if not summary:
        return jsonify({"error": "No data for model"}), 404
    return jsonify(summary)


@bp.route("/api/models/<path:model_id>/history")
async def api_model_history(model_id: str):
    try:
        lobby_id = _get_model_lobby_scope()
    except UnknownLobbyError:
        return jsonify({"error": "Arena lobby not found"}), 404
    try:
        days = _bounded_query_arg("days", 30, 1, 365)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    service = current_app.profiling_service
    if not service.available:
        return jsonify({"error": "Profiling unavailable"}), 503
    return jsonify(service.get_model_history(model_id, lobby_id=lobby_id, days=days))


@bp.route("/api/models/<path:model_id>/style")
async def api_model_style(model_id: str):
    try:
        lobby_id = _get_model_lobby_scope()
    except UnknownLobbyError:
        return jsonify({"error": "Arena lobby not found"}), 404
    try:
        window = _bounded_query_arg("window", 80, 10, 500)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    service = current_app.profiling_service
    if not service.available:
        return jsonify({"error": "Profiling unavailable"}), 503
    return jsonify(service.get_model_style(model_id, lobby_id=lobby_id, window=window))


@bp.route("/api/models/<path:model_id>/games")
async def api_model_games(model_id: str):
    try:
        lobby_id = _get_model_lobby_scope()
    except UnknownLobbyError:
        return jsonify({"error": "Arena lobby not found"}), 404
    try:
        limit = _bounded_query_arg("limit", 50, 1, 100)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    service = current_app.profiling_service
    if not service.available:
        return jsonify({"error": "Profiling unavailable"}), 503
    return jsonify(service.get_model_games(model_id, lobby_id=lobby_id, limit=limit))


@bp.route("/models")
async def get_models():
    supported_configs = get_supported_model_configs()
    supported = [config.as_dict() for config in supported_configs]
    arena_players = [config.as_dict() for config in ARENA_PLAYER_CONFIGS]
    return jsonify(
        {
            "models": supported,
            "arena_players": arena_players,
            "lobbies": [lobby.as_dict() for lobby in LOBBY_CONFIGS],
            "backends": {
                "openrouter": [
                    config.as_dict()
                    for config in supported_configs
                    if config.backend == "openrouter"
                ],
                "ollama": [
                    config.as_dict() for config in supported_configs if config.backend == "ollama"
                ],
            },
        }
    )


@bp.route("/<lobby:lobby_id>")
async def lobby_shortlink(lobby_id: str):
    return await _render_lobby(lobby_id)
