"""HTTP routes for the Texas Hold 'Em poker platform."""

import logging

from quart import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app.ai.model_config import get_supported_model_configs
from app.arena.arena_manager import ARENA_PLAYER_CONFIGS

logger = logging.getLogger(__name__)

bp = Blueprint("main", __name__)


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
    return redirect(url_for("main.arena"))


@bp.route("/arena")
async def arena():
    return await render_template("arena.html")


@bp.route("/faq")
async def faq():
    return await render_template(
        "faq.html",
        arena_players=ARENA_PLAYER_CONFIGS,
        arena_player_count=len(ARENA_PLAYER_CONFIGS),
    )


@bp.route("/leaderboard")
async def leaderboard():
    return await render_template("leaderboard.html")


@bp.route("/api/leaderboard")
async def api_leaderboard():
    service = current_app.leaderboard_service
    if not service.available:
        return jsonify({"error": "Leaderboard unavailable"}), 503
    return jsonify(service.get_leaderboard())


@bp.route("/model/<path:model_id>")
async def model_detail(model_id: str):
    return await render_template("model_detail.html", model_id=model_id)


@bp.route("/api/model/<path:model_id>/summary")
async def api_model_summary(model_id: str):
    service = current_app.profiling_service
    if not service.available:
        return jsonify({"error": "Profiling unavailable"}), 503
    summary = service.get_model_summary(model_id)
    if not summary:
        return jsonify({"error": "No data for model"}), 404
    return jsonify(summary)


@bp.route("/api/model/<path:model_id>/daily")
async def api_model_daily(model_id: str):
    service = current_app.profiling_service
    if not service.available:
        return jsonify({"error": "Profiling unavailable"}), 503
    days = int(request.args.get("days", "30"))
    return jsonify(service.get_model_daily_stats(model_id, days=days))


@bp.route("/api/model/<path:model_id>/style")
async def api_model_style(model_id: str):
    service = current_app.profiling_service
    if not service.available:
        return jsonify({"error": "Profiling unavailable"}), 503
    buckets = int(request.args.get("buckets", "10"))
    return jsonify(service.get_model_style_trends(model_id, buckets=buckets))


@bp.route("/api/model/<path:model_id>/hands")
async def api_model_hands(model_id: str):
    service = current_app.profiling_service
    if not service.available:
        return jsonify({"error": "Profiling unavailable"}), 503
    limit = int(request.args.get("limit", "20"))
    return jsonify(service.get_model_recent_hands(model_id, limit=limit))


@bp.route("/models")
async def get_models():
    supported_configs = get_supported_model_configs()
    supported = [config.as_dict() for config in supported_configs]
    arena_players = [config.as_dict() for config in ARENA_PLAYER_CONFIGS]
    return jsonify(
        {
            "models": supported,
            "arena_players": arena_players,
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
