"""SPE3102 SCPI readback and setpoint/protection validation."""

import time
from decimal import Decimal, InvalidOperation
from .common import finite_reading


def output_state(text):
    value = text.strip().upper()
    if value in ("1", "ON"):
        return True
    if value in ("0", "OFF"):
        return False
    raise ValueError(f"Unexpected output-state reply: {value!r}")


def read_psu(inst):
    # Use the individual commands supported by the SPE family, rather than
    # relying on ALL:INFO?, which may differ across firmware versions.
    return {
        "voltage": finite_reading(inst.query("MEAS:VOLT?")),
        "current": finite_reading(inst.query("MEAS:CURR?")),
        "power": finite_reading(inst.query("MEAS:POW?")),
        "output": output_state(inst.query("OUTP?")),
        "set_voltage": finite_reading(inst.query("VOLT?")),
        "set_current": finite_reading(inst.query("CURR?")),
        "ovp": finite_reading(inst.query("VOLT:LIM?")),
        "ocp": finite_reading(inst.query("CURR:LIM?")),
        "timestamp": time.time(),
    }


def validate_setpoints(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid settings request")
    try:
        voltage = Decimal(str(data["voltage"]))
        current = Decimal(str(data["current"]))
        if not voltage.is_finite() or not current.is_finite():
            raise ValueError("Settings must be finite numbers")
        if not 0 <= voltage <= 30 or not 0 <= current <= 10:
            raise ValueError("SPE3102 range: 0–30 V and 0–10 A")
        if voltage % Decimal("0.01") or current % Decimal("0.001"):
            raise ValueError("Use voltage steps of 0.01 V and current steps of 0.001 A")
        if voltage * current > 200:
            raise ValueError("Requested voltage × current must not exceed 200 W")
        return float(voltage), float(current)
    except (InvalidOperation, KeyError, TypeError) as exc:
        raise ValueError("Enter a valid voltage and current") from exc


def validate_protection(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid protection request")
    try:
        ovp = Decimal(str(data["ovp"]))
        ocp = Decimal(str(data["ocp"]))
        if not ovp.is_finite() or not ocp.is_finite():
            raise ValueError("Protection limits must be finite numbers")
        # SPE3102 datasheet: protection ranges extend beyond output ratings.
        if not 0 <= ovp <= 31 or not 0 <= ocp <= Decimal("10.1"):
            raise ValueError("SPE3102 protection range: OVP 0–31 V; OCP 0–10.1 A")
        if ovp % Decimal("0.01") or ocp % Decimal("0.001"):
            raise ValueError("Use OVP steps of 0.01 V and OCP steps of 0.001 A")
        return float(ovp), float(ocp)
    except (InvalidOperation, KeyError, TypeError) as exc:
        raise ValueError("Enter valid OVP and OCP limits") from exc
