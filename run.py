"""Entry point for the Texas Hold 'Em poker application."""

import socketio as python_socketio

from app import create_app, sio

quart_app = create_app()
app = python_socketio.ASGIApp(sio, quart_app)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)
