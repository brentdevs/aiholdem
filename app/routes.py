"""HTTP routes for the Texas Hold 'Em poker platform."""
import logging

from flask import Blueprint, Response, current_app, jsonify, redirect, render_template, url_for

from app.ai.ollama_player import SUPPORTED_OLLAMA_MODELS
from app.ai.openrouter_player import SUPPORTED_MODELS

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
def favicon():
    return Response(FAVICON_SVG, mimetype="image/svg+xml")


@bp.route("/")
def index():
    return redirect(url_for("main.arena"))


@bp.route("/arena")
def arena():
    return render_template("arena.html")


@bp.route("/faq")
def faq():
    return render_template("faq.html")


@bp.route("/leaderboard")
def leaderboard():
    return render_template("leaderboard.html")


@bp.route("/api/leaderboard")
def api_leaderboard():
    service = current_app.leaderboard_service
    if not service.available:
        return jsonify({"error": "Leaderboard unavailable"}), 503
    return jsonify(service.get_leaderboard())


@bp.route("/models")
def get_models():
    return jsonify({
        "models": SUPPORTED_MODELS + SUPPORTED_OLLAMA_MODELS,
        "backends": {
            "openrouter": SUPPORTED_MODELS,
            "ollama": SUPPORTED_OLLAMA_MODELS,
        },
    })
