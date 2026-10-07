"""Socket.IO transport adapters; instrument work lives in BenchController."""

from flask import current_app
from flask_socketio import emit


def controller():
    return current_app.extensions["bench_controller"]


def register_events(socketio):

    @socketio.on("get_ports")
    def handle_get_ports():
        return controller().discover_ports(emit)

    @socketio.on("get_state")
    def handle_get_state():
        return controller().send_snapshot(emit)

    @socketio.on("disconnect_device")
    def handle_disconnect():
        return controller().disconnect_dmm()

    @socketio.on("disconnect_psu")
    def handle_disconnect_psu():
        return controller().disconnect_psu()

    @socketio.on("change_mode")
    def handle_mode(data=None):
        return controller().set_dmm_mode(data, emit)

    @socketio.on("change_threshold")
    def handle_threshold(data=None):
        return controller().set_continuity_threshold(data, emit)

    @socketio.on("set_psu")
    def handle_set_psu(data=None):
        return controller().set_psu(data, emit)

    @socketio.on("set_psu_protection")
    def handle_psu_protection(data=None):
        return controller().set_protection(data, emit)

    @socketio.on("set_psu_output")
    def handle_psu_output(data=None):
        return controller().set_output(data, emit)

    @socketio.on("connect_device")
    def connect_dmm(data=None):
        return controller().connect_instrument("dmm", data, emit)

    @socketio.on("connect_psu")
    def connect_psu(data=None):
        return controller().connect_instrument("psu", data, emit)
