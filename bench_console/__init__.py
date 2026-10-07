"""OWON bench console application factory."""

from flask import Flask

from .config import Config
from .extensions import init_socketio
from .instruments.controller import BenchController


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)

    socketio = init_socketio(app)
    app.extensions["bench_controller"] = BenchController(app, socketio)
    from .hantek.controller import HantekController
    app.extensions["hantek_controller"] = HantekController(app, socketio)

    from .web import web
    from .events import register_events

    app.register_blueprint(web)
    register_events(socketio)
    from .hantek.web import hantek
    from .hantek.events import register_events as register_hantek_events
    app.register_blueprint(hantek)
    register_hantek_events(socketio)
    return app
