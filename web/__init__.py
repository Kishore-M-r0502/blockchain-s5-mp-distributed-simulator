"""Flask application factory."""
from __future__ import annotations

import os

from flask import Flask
from flask_socketio import SocketIO

from simulator.persistence import JSONFileStorage, load_config
from simulator.simulation import Simulation

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def create_app(config_path: str = None, seed: int = None, start_driver: bool = True):
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))
    cfg = load_config(config_path or os.path.join(ROOT, "config.json"))
    if seed is not None:
        cfg["seed"] = seed
    sim = Simulation(cfg)
    app.config["SIM"] = sim
    app.config["STORAGE"] = JSONFileStorage(os.path.join(ROOT, "data"))
    socketio = SocketIO(app, async_mode="threading", cors_allowed_origins=[])

    from .api import api
    from .routes import pages
    from .socket_events import register_socket_events

    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")
    register_socket_events(socketio, sim, start_driver)
    return app, socketio
