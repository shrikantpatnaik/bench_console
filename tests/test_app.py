"""Factory isolation, routes and static resource checks."""

from html.parser import HTMLParser

from bench_console import create_app
from bench_console.instruments import controller as controller_module


def test_factory_and_dashboard_do_not_initialize_visa(monkeypatch):
    def unexpected_backend(*args, **kwargs):
        raise AssertionError("VISA must be lazy")

    monkeypatch.setattr(controller_module.pyvisa, "ResourceManager", unexpected_backend)
    app = create_app({"TESTING": True})
    assert app.test_client().get("/").status_code == 200
    assert app.extensions["bench_controller"].rm is None


def test_factory_instances_isolate_state_and_socket_events():
    first = create_app({"TESTING": True})
    second = create_app({"TESTING": True})
    first_controller = first.extensions["bench_controller"]
    second_controller = second.extensions["bench_controller"]
    assert first_controller is not second_controller
    assert first.extensions["socketio"] is not second.extensions["socketio"]
    first_client = first.extensions["socketio"].test_client(first)
    second_client = second.extensions["socketio"].test_client(second)
    try:
        first_client.emit("change_mode", {"mode": "Resistance"})
        assert first_controller.device_state["current_mode"] == "Resistance"
        assert second_controller.device_state["current_mode"] == "DC Voltage"
        assert second_client.get_received() == []
        second_client.emit("get_state")
        received = second_client.get_received()
        assert (
            next(
                event["args"][0]["mode"]
                for event in received
                if event["name"] == "connection_status"
            )
            == "DC Voltage"
        )
    finally:
        first_client.disconnect()
        second_client.disconnect()
        first_controller.shutdown()
        second_controller.shutdown()


def test_http_resources_and_unique_ids():
    app = create_app({"TESTING": True})
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert 'id="dmm-connection-note"' in html
    assert 'id="psu-connection-note"' in html
    assert "click to switch" not in html
    assert client.get("/static/css/dashboard.css").status_code == 200
    js = client.get("/static/js/dashboard.js")
    assert js.status_code == 200 and b"{{" not in js.data

    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []

        def handle_starttag(self, tag, attrs):
            self.ids.extend(value for key, value in attrs if key == "id")

    elements = Elements()
    elements.feed(html)
    assert len(elements.ids) == len(set(elements.ids))
    assert (
        html.index('id="psu-power"')
        < html.index('id="output-toggle"')
        < html.index('id="psu-port"')
    )
    assert html.index('id="modes-grid"') < html.index('id="dmm-port"')


def test_shutdown_is_idempotent():
    app = create_app({"TESTING": True})
    controller = app.extensions["bench_controller"]

    class Manager:
        count = 0

        def close(self):
            self.count += 1

    controller.rm = Manager()
    controller.shutdown()
    controller.shutdown()
    assert controller.rm.count == 1
