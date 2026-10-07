"""Shared reply parsing and VISA resource normalization."""

import math


def finite_reading(text):
    value = float(text.strip())
    if not math.isfinite(value) or abs(value) > 1e30:
        raise ValueError("Instrument returned an invalid numeric reading")
    return value


def normalize_resource(port):
    if not isinstance(port, str) or not port.strip():
        raise ValueError("Select or enter a VISA resource / serial port")
    port = port.strip()
    if port.startswith("/dev/") or (
        port.upper().startswith("COM") and port[3:].isdigit()
    ):
        return f"ASRL{port}::INSTR"
    return port
