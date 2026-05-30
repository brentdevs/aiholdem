"""Shared Quart app instance for tests that need an application context.

Import _app from here instead of calling create_app() in individual test modules.
"""

from app import create_app

_app = create_app()
_app.config["TESTING"] = True
