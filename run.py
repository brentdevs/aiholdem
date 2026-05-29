"""Entry point for the Texas Hold 'Em poker Flask application."""

import eventlet

eventlet.monkey_patch()

from app import create_app, socketio  # noqa: E402

flask_app = create_app()

if __name__ == "__main__":
    socketio.run(flask_app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
