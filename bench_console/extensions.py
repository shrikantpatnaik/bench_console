"""Initialize an independent Socket.IO extension for each Flask app."""

from flask_socketio import SocketIO


def init_socketio(app):
    socketio = SocketIO(async_mode="threading")
    socketio.init_app(app)
    return socketio
