"""Bench defaults and instrument capabilities."""

import os

BAUD_RATES = (2400, 4800, 9600, 19200, 38400, 57600, 115200)
DMM_TIMEOUT_MS = 5000
DMM_POLL_SECONDS = 0.5
DMM_SETTLE_SECONDS = 1.0
DMM_MAX_TIMEOUTS = 3

MODES_CONFIG = {
    "DC Voltage": ("VOLT:DC", "V"),
    "AC Voltage": ("VOLT:AC", "V"),
    "DC Current": ("CURR:DC", "A"),
    "AC Current": ("CURR:AC", "A"),
    "Resistance": ("RES", "Ω"),
    "Capacitance": ("CAP", "F"),
    "Diode": ("DIOD", "V"),
    "Continuity": ("CONT", "Ω"),
    "Frequency": ("FREQ", "Hz"),
    "Temperature": ("TEMP", "°C"),
}


class Config:
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "5001"))
    DMM_TIMEOUT_MS = DMM_TIMEOUT_MS
    DMM_POLL_SECONDS = DMM_POLL_SECONDS
    DMM_SETTLE_SECONDS = DMM_SETTLE_SECONDS
    DMM_MAX_TIMEOUTS = DMM_MAX_TIMEOUTS
