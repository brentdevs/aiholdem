"""Shared Flask app instance for tests that need a socketio test client.

Creating the app (and calling socketio.init_app) more than once breaks the
socketio singleton.  Import _app and _socketio from here instead of calling
create_app() in individual test modules.
"""

from app import create_app
from app import socketio as _socketio

_app = create_app()
_app.config["TESTING"] = True
