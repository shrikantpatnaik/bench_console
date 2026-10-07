"""Per-application instrument sessions, serialized I/O and polling.

Socket.IO adapters supply request-scoped replies; this module does not use
Flask request globals. Polling broadcasts through the app's own SocketIO instance.
"""

import glob
import math
import threading
import time

import pyvisa
from serial.tools import list_ports

from ..config import BAUD_RATES, MODES_CONFIG
from .common import finite_reading, normalize_resource
from .multimeter import format_reading, configure_dmm
from .power_supply import (
    output_state,
    read_psu,
    validate_setpoints,
    validate_protection,
)


def new_device(model):
    return {
        "model": model,
        "instrument": None,
        "port": None,
        "idn": "",
        "lock": threading.RLock(),
        "stop": None,
        "latest": None,
        "next_poll_at": 0.0,
        "timeouts": 0,
    }


class BenchController:
    def __init__(self, app, socketio):
        self.app = app
        self.socketio = socketio
        self.connection_lock = threading.RLock()
        self.manager_lock = threading.Lock()
        self.rm = None
        self.backend_error = None
        self._manager_initialized = False
        self._closed = False
        self.devices = {"dmm": new_device("XDM1041"), "psu": new_device("SPE3102")}
        self.device_state = {"current_mode": "DC Voltage", "cont_threshold": 50.0}
        for name in (
            "DMM_TIMEOUT_MS",
            "DMM_POLL_SECONDS",
            "DMM_SETTLE_SECONDS",
            "DMM_MAX_TIMEOUTS",
        ):
            setattr(self, name, app.config[name])

    def ensure_resource_manager(self):
        # Importing the package and serving the dashboard never opens hardware.
        with self.manager_lock:
            if self.rm is not None or self._manager_initialized:
                return
            self._manager_initialized = True
            try:
                self.rm = pyvisa.ResourceManager("@py")
            except Exception as exc:
                self.backend_error = str(exc)

    def status(self, key, error=None, **extra):
        state = self.devices[key]
        result = {
            "connected": state["instrument"] is not None,
            "idn": state["idn"],
            "port": state["port"],
            "mode": self.device_state["current_mode"],
            "threshold": self.device_state["cont_threshold"],
        }
        if error:
            result["error"] = error
        result.update(extra)
        return result

    def publish_status(self, key, **extra):
        event = "connection_status" if key == "dmm" else "psu_connection_status"
        self.socketio.emit(event, self.status(key, **extra))

    def close_device(self, key):
        """Close under the device lock and stop only that session’s worker."""
        state = self.devices[key]
        if state["stop"]:
            state["stop"].set()
        inst = state["instrument"]
        state.update(instrument=None, stop=None, latest=None, port=None, idn="")
        if inst:
            try:
                inst.close()
            except Exception:
                self.app.logger.exception("Could not close %s", key)

    def communication_failed(self, key, exc):
        self.app.logger.warning("%s communication failed: %s", key, exc)
        self.close_device(key)
        message = f"Communication failed: {exc}. Reconnect to continue."
        if key == "psu":
            message += " Output state is unknown; check the supply front panel."
        self.publish_status(key, error=message)

    def poll_device(self, key, inst, stop):
        """Poll one session; serialize reads with controls and bound meter retries."""
        state = self.devices[key]
        interval = self.DMM_POLL_SECONDS if key == "dmm" else 0.8
        while not stop.wait(interval):
            with state["lock"]:
                if stop.is_set() or state["instrument"] is not inst:
                    return
                if key == "dmm" and time.monotonic() < state["next_poll_at"]:
                    continue
                try:
                    if key == "psu":
                        reading = read_psu(inst)
                        event = "psu_telemetry"
                    else:
                        if state["timeouts"]:
                            # The previous retry backoff lets late bytes arrive.
                            # @py serial implements this mask as an RX reset.
                            inst.flush(
                                pyvisa.constants.BufferOperation.discard_read_buffer
                            )
                        raw = inst.query("MEAS?")
                        raw = raw.strip().split(",", 1)[0].strip()
                        mode = self.device_state["current_mode"]
                        display, unit = format_reading(raw, MODES_CONFIG[mode][1])
                        lamp = False
                        if mode == "Continuity" and display != "O.L":
                            try:
                                lamp = (
                                    0
                                    <= float(raw)
                                    <= self.device_state["cont_threshold"]
                                )
                            except ValueError:
                                pass
                        reading = {
                            "display": display,
                            "unit": unit,
                            "lamp": lamp,
                            "is_ol": display == "O.L",
                        }
                        event = "telemetry"
                    state["latest"] = reading
                    if key == "dmm" and state["timeouts"]:
                        state["timeouts"] = 0
                        self.socketio.emit("dmm_poll_status", {"waiting": False})
                    self.socketio.emit(event, reading)
                except pyvisa.errors.VisaIOError as exc:
                    if (
                        key == "dmm"
                        and exc.error_code == pyvisa.constants.StatusCode.error_timeout
                        and (inst.interface_type == pyvisa.constants.InterfaceType.asrl)
                    ):
                        state["timeouts"] += 1
                        state["latest"] = None
                        attempt = state["timeouts"]
                        self.app.logger.warning(
                            "DMM MEAS? timeout (%s/%s), mode=%s, port=%s, baud=%s",
                            attempt,
                            self.DMM_MAX_TIMEOUTS,
                            self.device_state["current_mode"],
                            state["port"],
                            inst.baud_rate,
                        )
                        if attempt < self.DMM_MAX_TIMEOUTS:
                            state["next_poll_at"] = (
                                time.monotonic() + self.DMM_SETTLE_SECONDS
                            )
                            self.socketio.emit(
                                "dmm_poll_status",
                                {
                                    "waiting": True,
                                    "error": f"Waiting for meter: MEAS? timed out ({attempt}/{self.DMM_MAX_TIMEOUTS}); retrying…",
                                },
                            )
                            continue
                    self.communication_failed(key, exc)
                    return
                except Exception as exc:
                    self.communication_failed(key, exc)
                    return

    def connect_instrument(self, key, data, reply):
        """Verify the model before configuring it; never enable PSU output here."""
        state = self.devices[key]
        try:
            if not isinstance(data, dict):
                raise ValueError("Invalid connection request")
            port = normalize_resource(data.get("port"))
            baud = int(data.get("baud", 115200))
            if baud not in BAUD_RATES:
                raise ValueError("Unsupported baud rate")
            self.ensure_resource_manager()
            if self.rm is None:
                raise RuntimeError(f"PyVISA backend unavailable: {self.backend_error}")
            with self.connection_lock, state["lock"]:
                if state["instrument"]:
                    raise ValueError(
                        "Already connected; disconnect before changing ports"
                    )
                other = self.devices["psu" if key == "dmm" else "dmm"]
                if other["instrument"] and other["port"] == port:
                    raise ValueError(
                        "This port is already used by the other instrument"
                    )
                inst = None
                remote = False
                try:
                    with self.manager_lock:
                        inst = self.rm.open_resource(port)
                    if inst.interface_type == pyvisa.constants.InterfaceType.asrl:
                        inst.baud_rate = baud
                        inst.data_bits = 8
                        inst.parity = pyvisa.constants.Parity.none
                        inst.stop_bits = pyvisa.constants.StopBits.one
                        inst.flow_control = pyvisa.constants.ControlFlow.none
                    inst.read_termination = "\n"
                    inst.write_termination = "\n"
                    inst.timeout = self.DMM_TIMEOUT_MS if key == "dmm" else 2000
                    if key == "dmm":
                        inst.query_delay = 0.1
                    idn = inst.query("*IDN?").strip()
                    parts = [part.strip().upper() for part in idn.split(",")]
                    if len(parts) < 2 or parts[1] != state["model"]:
                        raise ValueError(f"Expected {state['model']}; received {idn!r}")
                    if key == "psu":
                        inst.write("SYST:REM")
                        remote = True
                        latest = read_psu(inst)
                    else:
                        inst.write("SYST:REM")
                        time.sleep(0.1)
                        configure_dmm(inst, self.device_state["current_mode"])
                        latest = None
                    stop = threading.Event()
                    state.update(
                        instrument=inst,
                        port=port,
                        idn=idn,
                        stop=stop,
                        latest=latest,
                        next_poll_at=(
                            time.monotonic() + self.DMM_SETTLE_SECONDS
                            if key == "dmm"
                            else 0
                        ),
                        timeouts=0,
                    )
                    self.publish_status(key)
                    if latest:
                        self.socketio.emit("psu_telemetry", latest)
                    self.socketio.start_background_task(
                        self.poll_device, key, inst, stop
                    )
                except Exception:
                    if state["instrument"] is inst and inst is not None:
                        self.close_device(key)
                    elif remote and inst:
                        try:
                            inst.write("SYST:LOC")
                        except Exception:
                            pass
                    if inst:
                        inst.close()
                    raise
        except Exception as exc:
            with state["lock"]:
                event = "connection_status" if key == "dmm" else "psu_connection_status"
                message = str(exc)
                if key == "psu" and (not state["instrument"]):
                    message += " Output state is unknown; check the front panel."
                reply(event, self.status(key, error=message))

    def discover_ports(self, reply):
        self.ensure_resource_manager()
        found = set()
        warnings = []
        try:
            found.update((f"ASRL{p.device}::INSTR" for p in list_ports.comports()))
            for pattern in (
                "/dev/cu.wchusbserial*",
                "/dev/cu.usbserial*",
                "/dev/cu.usbmodem*",
            ):
                found.update((f"ASRL{p}::INSTR" for p in glob.glob(pattern)))
        except Exception as exc:
            warnings.append(str(exc))
        if self.rm:
            try:
                with self.manager_lock:
                    found.update(self.rm.list_resources())
            except Exception as exc:
                warnings.append(str(exc))
        reply("ports_list", sorted(found))
        if warnings:
            self.app.logger.warning("Port discovery: %s", "; ".join(warnings))

    def send_snapshot(self, reply):
        for key in self.devices:
            with self.devices[key]["lock"]:
                event = "connection_status" if key == "dmm" else "psu_connection_status"
                reply(event, self.status(key))
                if (
                    key == "dmm"
                    and self.devices[key]["timeouts"]
                    and self.devices[key]["instrument"]
                ):
                    reply(
                        "dmm_poll_status",
                        {
                            "waiting": True,
                            "error": "Waiting for meter; retrying MEAS?…",
                        },
                    )
                elif self.devices[key]["latest"]:
                    reply(
                        "telemetry" if key == "dmm" else "psu_telemetry",
                        self.devices[key]["latest"],
                    )

    def disconnect_dmm(self):
        with self.connection_lock, self.devices["dmm"]["lock"]:
            self.close_device("dmm")
            self.publish_status("dmm")

    def disconnect_psu(self):
        with self.connection_lock, self.devices["psu"]["lock"]:
            inst = self.devices["psu"]["instrument"]
            error = None
            confirmed_off = False
            if inst:
                try:
                    inst.write("OUTP OFF")
                    confirmed_off = not output_state(inst.query("OUTP?"))
                    if not confirmed_off:
                        raise RuntimeError("Supply did not confirm output OFF")
                    inst.write("SYST:LOC")
                except Exception as exc:
                    error = f"Disconnect error: {exc}. "
                    error += (
                        "Output OFF was confirmed."
                        if confirmed_off
                        else "Output state is unknown; check the front panel."
                    )
            self.close_device("psu")
            self.publish_status("psu", error=error, output_off_confirmed=confirmed_off)

    def set_dmm_mode(self, data, reply):
        mode = data.get("mode") if isinstance(data, dict) else None
        if mode not in MODES_CONFIG:
            reply(
                "command_error", {"device": "dmm", "error": "Unknown measurement mode"}
            )
            return
        with self.devices["dmm"]["lock"]:
            inst = self.devices["dmm"]["instrument"]
            try:
                if inst:
                    configure_dmm(inst, mode)
                    self.devices["dmm"]["next_poll_at"] = (
                        time.monotonic() + self.DMM_SETTLE_SECONDS
                    )
                self.device_state["current_mode"] = mode
                self.devices["dmm"]["latest"] = None
                self.publish_status("dmm")
            except Exception as exc:
                self.communication_failed("dmm", exc)

    def set_continuity_threshold(self, data, reply):
        try:
            value = float(data["value"])
            if not math.isfinite(value) or value < 0:
                raise ValueError("Continuity threshold must be finite and nonnegative")
        except (ValueError, TypeError, KeyError) as exc:
            reply("command_error", {"device": "dmm", "error": str(exc)})
            return
        with self.devices["dmm"]["lock"]:
            self.device_state["cont_threshold"] = value
            self.publish_status("dmm")

    def set_psu(self, data, reply):
        """Apply V/I within the power envelope, then verify hardware readback."""
        try:
            voltage, current = validate_setpoints(data)
        except ValueError as exc:
            reply("command_error", {"device": "psu", "error": str(exc)})
            return
        with self.devices["psu"]["lock"]:
            inst = self.devices["psu"]["instrument"]
            if not inst:
                reply(
                    "command_error",
                    {"device": "psu", "error": "Connect the power supply first"},
                )
                return
            try:
                old_voltage = finite_reading(inst.query("VOLT?"))
                commands = [f"CURR {current:.3f}", f"VOLT {voltage:.2f}"]
                if old_voltage * current > 200:
                    commands.reverse()
                for command in commands:
                    inst.write(command)
                reading = read_psu(inst)
                self.devices["psu"]["latest"] = reading
                self.socketio.emit("psu_telemetry", reading)
                if (
                    abs(reading["set_voltage"] - voltage) > 0.0051
                    or abs(reading["set_current"] - current) > 0.00051
                ):
                    reply(
                        "command_error",
                        {
                            "device": "psu",
                            "error": "Readback differs from requested settings; check protection limits / front panel",
                        },
                    )
                else:
                    reply("psu_settings_applied", reading)
            except Exception as exc:
                self.communication_failed("psu", exc)

    def set_protection(self, data, reply):
        """Apply trip thresholds without changing setpoints or enabling output."""
        try:
            ovp, ocp = validate_protection(data)
        except ValueError as exc:
            reply("command_error", {"device": "psu", "error": str(exc)})
            return
        with self.devices["psu"]["lock"]:
            inst = self.devices["psu"]["instrument"]
            if not inst:
                reply(
                    "command_error",
                    {"device": "psu", "error": "Connect the power supply first"},
                )
                return
            try:
                inst.write(f"VOLT:LIM {ovp:.2f}")
                inst.write(f"CURR:LIM {ocp:.3f}")
                reading = read_psu(inst)
                self.devices["psu"]["latest"] = reading
                self.socketio.emit("psu_telemetry", reading)
                if (
                    abs(reading["ovp"] - ovp) > 0.0051
                    or abs(reading["ocp"] - ocp) > 0.00051
                ):
                    reply(
                        "command_error",
                        {
                            "device": "psu",
                            "error": "Protection readback differs from requested limits; check the front panel",
                        },
                    )
                else:
                    reply("psu_protection_applied", reading)
            except Exception as exc:
                self.communication_failed("psu", exc)

    def set_output(self, data, reply):
        """Acknowledge only the output state confirmed by the instrument."""
        enabled = data.get("enabled") if isinstance(data, dict) else None
        if type(enabled) is not bool:
            reply(
                "command_error",
                {"device": "psu", "error": "Output must be true or false"},
            )
            return {"ok": False}
        with self.devices["psu"]["lock"]:
            inst = self.devices["psu"]["instrument"]
            if not inst:
                reply(
                    "command_error",
                    {"device": "psu", "error": "Connect the power supply first"},
                )
                return {"ok": False}
            try:
                if enabled:
                    existing = {
                        "voltage": inst.query("VOLT?").strip(),
                        "current": inst.query("CURR?").strip(),
                    }
                    try:
                        validate_setpoints(existing)
                    except ValueError as exc:
                        reply("command_error", {"device": "psu", "error": str(exc)})
                        return {"ok": False}
                inst.write("OUTP ON" if enabled else "OUTP OFF")
                reading = read_psu(inst)
                self.devices["psu"]["latest"] = reading
                self.socketio.emit("psu_telemetry", reading)
                if reading["output"] != enabled:
                    reply(
                        "command_error",
                        {
                            "device": "psu",
                            "error": "Supply did not confirm the requested output state; check the front panel",
                        },
                    )
                    return {"ok": False}
                return {"ok": True, "output": reading["output"]}
            except Exception as exc:
                self.communication_failed("psu", exc)
                return {"ok": False}

    def shutdown(self):
        """Attempt PSU OFF/local and release sessions once; no retries or restarts."""
        with self.connection_lock:
            if self._closed:
                return
            self._closed = True
            for key, state in self.devices.items():
                with state["lock"]:
                    if key == "psu" and state["instrument"]:
                        try:
                            state["instrument"].write("OUTP OFF")
                            state["instrument"].write("SYST:LOC")
                        except Exception:
                            self.app.logger.warning(
                                "Shutdown could not disable PSU output"
                            )
                    self.close_device(key)
            if self.rm:
                try:
                    self.rm.close()
                except Exception:
                    pass
