"""Protocol and failure-path tests; no physical instruments required."""

import threading
import time
import pytest
import pyvisa
from bench_console import create_app
from bench_console.instruments.controller import new_device


@pytest.fixture
def console():
    app = create_app({"TESTING": True})
    controller = app.extensions["bench_controller"]
    yield controller
    controller.shutdown()


class FakeInstrument:
    interface_type = pyvisa.constants.InterfaceType.asrl

    def __init__(self, model):
        self.model = model
        self.commands = []
        self.voltage = 5.0
        self.current = 1.0
        self.ovp = 31.0
        self.ocp = 10.1
        self.output = True
        self.closed = False
        self.fail = None
        self.inflight = threading.Lock()
        self.delay = 0

    def exchange(self, command):
        assert not self.closed
        assert self.inflight.acquire(blocking=False), "Overlapping instrument I/O"
        try:
            self.commands.append(command)
            if self.fail == command:
                raise TimeoutError("Simulated timeout")
            if self.delay:
                time.sleep(self.delay)
        finally:
            self.inflight.release()

    def write(self, command):
        self.exchange(command)
        if command.startswith("VOLT "):
            self.voltage = float(command.split()[1])
        elif command.startswith("CURR "):
            self.current = float(command.split()[1])
        elif command.startswith("VOLT:LIM "):
            self.ovp = float(command.split()[1])
        elif command.startswith("CURR:LIM "):
            self.ocp = float(command.split()[1])
        elif command.startswith("OUTP "):
            self.output = command.endswith("ON")

    def query(self, command):
        self.exchange(command)
        return {
            "*IDN?": f"OWON,{self.model},123,FV:1.0\n",
            "VOLT?": str(self.voltage),
            "CURR?": str(self.current),
            "OUTP?": str(int(self.output)),
            "VOLT:LIM?": str(self.ovp),
            "CURR:LIM?": str(self.ocp),
            "MEAS:VOLT?": "4.990",
            "MEAS:CURR?": "0.250",
            "MEAS:POW?": "1.2475",
            "MEAS?": "4.990",
        }[command]

    def close(self):
        self.closed = True


class FakeManager:

    def __init__(self):
        self.resources = {
            "ASRL/dev/cu.dmm::INSTR": FakeInstrument("XDM1041"),
            "ASRL/dev/cu.psu::INSTR": FakeInstrument("SPE3102"),
        }

    def open_resource(self, port):
        return self.resources[port]

    def list_resources(self):
        return tuple(self.resources)


@pytest.fixture
def bench(console, monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(console, "rm", manager)
    monkeypatch.setattr(
        console, "devices", {"dmm": new_device("XDM1041"), "psu": new_device("SPE3102")}
    )
    monkeypatch.setattr(
        console, "device_state", {"current_mode": "DC Voltage", "cont_threshold": 50.0}
    )
    workers = []
    monkeypatch.setattr(
        console.socketio, "start_background_task", lambda *args: workers.append(args)
    )
    client = console.socketio.test_client(console.app)
    yield (manager, client, workers)
    for key in console.devices:
        with console.devices[key]["lock"]:
            console.close_device(key)
    client.disconnect()


def connect(client, key):
    client.emit(
        "connect_psu" if key == "psu" else "connect_device",
        {"port": f"/dev/cu.{key}", "baud": 115200},
    )


def test_connect_preserves_output_and_reads_settings(bench, console):
    manager, client, workers = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    assert console.devices["psu"]["instrument"] is inst
    assert inst.output is True
    assert "SYST:REM" in inst.commands
    assert not any((c.startswith("OUTP ") for c in inst.commands))
    assert inst.baud_rate == 115200
    assert console.devices["psu"]["latest"]["set_voltage"] == 5
    assert len(workers) == 1


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"voltage": "NaN", "current": 1},
        {"voltage": "Infinity", "current": 1},
        {"voltage": 31, "current": 1},
        {"voltage": -1, "current": 1},
        {"voltage": 10, "current": 11},
        {"voltage": 30, "current": 10},
        {"voltage": 5.001, "current": 1},
        {"voltage": "5;OUTP ON", "current": 1},
    ],
)
def test_invalid_setpoints_send_no_commands(bench, data):
    manager, client, _ = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    before = list(inst.commands)
    client.emit("set_psu", data)
    assert inst.commands == before
    assert any((event["name"] == "command_error" for event in client.get_received()))


def test_apply_while_live_and_intermediate_power_envelope(bench):
    manager, client, _ = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    inst.voltage = 30
    inst.current = 6
    inst.commands.clear()
    client.emit("set_psu", {"voltage": 10, "current": 10})
    assert inst.commands[:3] == ["VOLT?", "VOLT 10.00", "CURR 10.000"]
    assert inst.output is True
    assert any(
        (event["name"] == "psu_settings_applied" for event in client.get_received())
    )


def test_output_on_off_and_disconnect(bench):
    manager, client, _ = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    client.emit("set_psu_output", {"enabled": False})
    assert inst.output is False
    client.emit("set_psu_output", {"enabled": True})
    assert inst.output is True
    client.emit("disconnect_psu")
    assert inst.output is False and inst.closed
    assert inst.commands[-3:] == ["OUTP OFF", "OUTP?", "SYST:LOC"]
    events = client.get_received()
    status = [e["args"][0] for e in events if e["name"] == "psu_connection_status"][-1]
    assert status["output_off_confirmed"] is True


def test_wrong_model_closes_without_control_writes(bench, console):
    manager, client, _ = bench
    manager.resources["ASRL/dev/cu.psu::INSTR"].model = "XDM1041"
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    assert inst.closed and inst.commands == ["*IDN?"]
    assert console.devices["psu"]["instrument"] is None


def test_both_devices_and_page_refresh(bench, console):
    manager, client, workers = bench
    connect(client, "dmm")
    connect(client, "psu")
    dmm = manager.resources["ASRL/dev/cu.dmm::INSTR"]
    client.emit("change_mode", {"mode": "Resistance"})
    assert "CONF:RES" in dmm.commands
    assert console.devices["psu"]["instrument"] is not None
    before = {key: list(inst.commands) for key, inst in manager.resources.items()}
    observer = console.socketio.test_client(console.app)
    observer.emit("get_state")
    assert {key: inst.commands for key, inst in manager.resources.items()} == before
    assert {e["name"] for e in observer.get_received()} >= {
        "connection_status",
        "psu_connection_status",
        "psu_telemetry",
    }
    observer.disconnect()
    assert console.devices["psu"]["instrument"] is not None
    assert len(workers) == 2


def test_duplicate_connection_does_not_start_worker(bench, console):
    _, client, workers = bench
    connect(client, "psu")
    connect(client, "psu")
    assert len(workers) == 1
    client.emit("connect_device", {"port": "/dev/cu.psu"})
    assert console.devices["dmm"]["instrument"] is None


def test_partial_apply_timeout_reports_unknown_and_closes(bench, console):
    manager, client, _ = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    inst.fail = "VOLT 8.00"
    client.emit("set_psu", {"voltage": 8, "current": 1})
    assert inst.closed and console.devices["psu"]["instrument"] is None
    status = [
        e["args"][0]
        for e in client.get_received()
        if e["name"] == "psu_connection_status"
    ][-1]
    assert "unknown" in status["error"]


def test_disconnect_timeout_never_claims_output_off(bench):
    manager, client, _ = bench
    connect(client, "psu")
    manager.resources["ASRL/dev/cu.psu::INSTR"].fail = "OUTP?"
    client.emit("disconnect_psu")
    status = [
        e["args"][0]
        for e in client.get_received()
        if e["name"] == "psu_connection_status"
    ][-1]
    assert status["output_off_confirmed"] is False
    assert "unknown" in status["error"]


def test_poll_and_controls_use_one_io_lock(bench, console):
    manager, client, workers = bench
    connect(client, "psu")
    _, key, inst, stop = workers[0]
    inst.delay = 0.01
    thread = threading.Thread(target=console.poll_device, args=(key, inst, stop))
    thread.start()
    deadline = time.monotonic() + 2
    initial = len(inst.commands)
    while len(inst.commands) == initial and time.monotonic() < deadline:
        time.sleep(0.01)
    client.emit("set_psu", {"voltage": 6, "current": 1})
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive() and (not inst.closed)
    assert inst.voltage == 6


def test_obsolete_worker_cannot_poll_new_session(bench):
    manager, client, workers = bench
    connect(client, "psu")
    func, key, old_inst, old_stop = workers[0]
    client.emit("disconnect_psu")
    manager.resources["ASRL/dev/cu.psu::INSTR"] = FakeInstrument("SPE3102")
    connect(client, "psu")
    new_inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    before = list(new_inst.commands)
    func(key, old_inst, old_stop)
    assert new_inst.commands == before


def test_flask_page_renders(console):
    response = console.app.test_client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "XDM1041" in html and "SPE3102" in html
    assert "{{" not in html


def test_enabling_excessive_existing_settings_is_rejected_but_off_works(bench):
    manager, client, _ = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    inst.voltage, inst.current, inst.output = (30, 10, False)
    client.emit("set_psu_output", {"enabled": True})
    assert "OUTP ON" not in inst.commands and (not inst.closed)
    client.emit("set_psu_output", {"enabled": False})
    assert "OUTP OFF" in inst.commands and (not inst.closed)


def test_output_query_timeout_closes_connection(bench, console):
    manager, client, _ = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    inst.fail = "OUTP?"
    client.emit("set_psu_output", {"enabled": False})
    assert inst.closed and console.devices["psu"]["instrument"] is None


def test_output_boolean_validation(bench):
    manager, client, _ = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    before = list(inst.commands)
    for invalid in ["false", 0, None, {}]:
        client.emit("set_psu_output", {"enabled": invalid})
    assert inst.commands == before


def test_dmm_uses_documented_commands_and_longer_timeout(bench, console):
    manager, client, _ = bench
    connect(client, "dmm")
    inst = manager.resources["ASRL/dev/cu.dmm::INSTR"]
    assert inst.commands == ["*IDN?", "SYST:REM", "CONF:VOLT:DC", "AUTO"]
    assert inst.timeout == 5000 and inst.query_delay == 0.1
    client.emit("change_mode", {"mode": "Resistance"})
    assert inst.commands[-2:] == ["CONF:RES", "AUTO"]
    assert console.devices["dmm"]["next_poll_at"] > time.monotonic()


class StepStop:
    """Advance a polling worker by a fixed number of iterations, without sleep."""

    def __init__(self, steps):
        self.remaining = steps
        self.stopped = False

    def wait(self, interval):
        if self.stopped or self.remaining == 0:
            return True
        self.remaining -= 1
        return False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True


def dmm_poll_fixture(bench, monkeypatch, responses, console):
    manager, client, _ = bench
    connect(client, "dmm")
    inst = manager.resources["ASRL/dev/cu.dmm::INSTR"]
    stop = StepStop(len(responses))
    console.devices["dmm"].update(stop=stop, next_poll_at=0)
    monkeypatch.setattr(console, "DMM_SETTLE_SECONDS", 0)
    replies = iter(responses)

    def query(command):
        inst.commands.append(command)
        response = next(replies)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(inst, "query", query)
    flushes = []
    monkeypatch.setattr(inst, "flush", lambda mask: flushes.append(mask), raising=False)
    client.get_received()
    return (inst, stop, flushes, client)


def visa_timeout():
    return pyvisa.errors.VisaIOError(pyvisa.constants.StatusCode.error_timeout)


def test_one_dmm_timeout_recovers_without_disconnecting(bench, monkeypatch, console):
    inst, stop, flushes, client = dmm_poll_fixture(
        bench, monkeypatch, [visa_timeout(), "4.990"], console
    )
    console.poll_device("dmm", inst, stop)
    assert not inst.closed and console.devices["dmm"]["timeouts"] == 0
    assert flushes == [pyvisa.constants.BufferOperation.discard_read_buffer]
    events = client.get_received()
    waiting = [
        event["args"][0]["waiting"]
        for event in events
        if event["name"] == "dmm_poll_status"
    ]
    assert waiting == [True, False]
    assert any((event["name"] == "telemetry" for event in events))


def test_three_consecutive_dmm_timeouts_close_connection(bench, monkeypatch, console):
    inst, stop, flushes, client = dmm_poll_fixture(
        bench, monkeypatch, [visa_timeout() for _ in range(3)], console
    )
    console.poll_device("dmm", inst, stop)
    assert inst.closed and console.devices["dmm"]["instrument"] is None
    assert len(flushes) == 2
    assert any(
        (
            event["name"] == "connection_status" and (not event["args"][0]["connected"])
            for event in client.get_received()
        )
    )


def test_non_timeout_dmm_error_is_not_retried(bench, monkeypatch, console):
    error = pyvisa.errors.VisaIOError(pyvisa.constants.StatusCode.error_connection_lost)
    inst, stop, flushes, _ = dmm_poll_fixture(bench, monkeypatch, [error], console)
    console.poll_device("dmm", inst, stop)
    assert inst.closed and flushes == []


def test_dual_display_reads_primary_value(bench, monkeypatch, console):
    inst, stop, _, client = dmm_poll_fixture(
        bench, monkeypatch, ["4.990,50.0"], console
    )
    console.poll_device("dmm", inst, stop)
    assert console.devices["dmm"]["latest"]["display"] == "4.9900"


def test_mode_settle_delays_measurement(bench, monkeypatch, console):
    manager, client, _ = bench
    connect(client, "dmm")
    inst = manager.resources["ASRL/dev/cu.dmm::INSTR"]
    stop = StepStop(2)
    console.devices["dmm"]["stop"] = stop
    before = list(inst.commands)
    console.poll_device("dmm", inst, stop)
    assert inst.commands == before


def test_connect_reads_existing_protection_without_changing_it(bench, console):
    manager, client, _ = bench
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    inst.ovp, inst.ocp = (12.5, 2.25)
    connect(client, "psu")
    latest = console.devices["psu"]["latest"]
    assert latest["ovp"] == 12.5 and latest["ocp"] == 2.25
    assert not any(
        (command.startswith(("VOLT:LIM ", "CURR:LIM ")) for command in inst.commands)
    )


def test_protection_apply_changes_only_protection_and_verifies(bench):
    manager, client, _ = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    inst.commands.clear()
    client.get_received()
    client.emit("set_psu_protection", {"ovp": "12.30", "ocp": "2.500"})
    assert inst.commands[:2] == ["VOLT:LIM 12.30", "CURR:LIM 2.500"]
    assert inst.voltage == 5 and inst.current == 1 and (inst.output is True)
    assert not any((command.startswith("OUTP ") for command in inst.commands))
    assert any(
        (event["name"] == "psu_protection_applied" for event in client.get_received())
    )


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"ovp": "NaN", "ocp": 1},
        {"ovp": 32, "ocp": 1},
        {"ovp": 10, "ocp": 10.101},
        {"ovp": -1, "ocp": 1},
        {"ovp": 10.001, "ocp": 1},
        {"ovp": 10, "ocp": "1;OUTP ON"},
    ],
)
def test_invalid_protection_never_writes(bench, data):
    manager, client, _ = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    before = list(inst.commands)
    client.emit("set_psu_protection", data)
    assert inst.commands == before


def test_protection_full_range_is_independent_of_power_envelope(bench):
    manager, client, _ = bench
    connect(client, "psu")
    client.emit("set_psu_protection", {"ovp": 31, "ocp": 10.1})
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    assert "VOLT:LIM 31.00" in inst.commands and "CURR:LIM 10.100" in inst.commands


def test_protection_readback_mismatch_is_reported(bench, monkeypatch):
    manager, client, _ = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    original_query = inst.query
    monkeypatch.setattr(
        inst,
        "query",
        lambda command: "30" if command == "VOLT:LIM?" else original_query(command),
    )
    client.get_received()
    client.emit("set_psu_protection", {"ovp": 12, "ocp": 2})
    events = client.get_received()
    assert not any((event["name"] == "psu_protection_applied" for event in events))
    assert any((event["name"] == "command_error" for event in events))


def test_protection_timeout_closes_without_claiming_success(bench, console):
    manager, client, _ = bench
    connect(client, "psu")
    manager.resources["ASRL/dev/cu.psu::INSTR"].fail = "CURR:LIM 2.000"
    client.get_received()
    client.emit("set_psu_protection", {"ovp": 12, "ocp": 2})
    assert console.devices["psu"]["instrument"] is None
    assert not any(
        (event["name"] == "psu_protection_applied" for event in client.get_received())
    )


def test_output_toggle_acknowledges_confirmed_hardware_state(bench):
    _, client, _ = bench
    connect(client, "psu")
    result = client.emit("set_psu_output", {"enabled": False}, callback=True)
    assert result == {"ok": True, "output": False}


def test_output_toggle_does_not_acknowledge_unconfirmed_state(bench, monkeypatch):
    manager, client, _ = bench
    connect(client, "psu")
    inst = manager.resources["ASRL/dev/cu.psu::INSTR"]
    original_query = inst.query
    monkeypatch.setattr(
        inst,
        "query",
        lambda command: "1" if command == "OUTP?" else original_query(command),
    )
    result = client.emit("set_psu_output", {"enabled": False}, callback=True)
    assert result == {"ok": False}
