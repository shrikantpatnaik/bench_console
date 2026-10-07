"""XDM1041 formatting and documented configuration commands."""

import time
from ..config import MODES_CONFIG


def format_reading(raw_str, base_unit):
    try:
        val = float(raw_str)
        if abs(val) > 1e30:
            return "O.L", base_unit

        abs_val = abs(val)
        if abs_val == 0:
            return "0.0000", base_unit

        if base_unit in ["V", "Ω"]:
            if abs_val >= 1e6:
                return f"{val / 1e6:.4f}", f"M{base_unit}"
            elif abs_val >= 1e3:
                return f"{val / 1e3:.4f}", f"k{base_unit}"
            else:
                return f"{val:.4f}", base_unit
        else:
            if abs_val >= 1e6:
                return f"{val / 1e6:.4f}", f"M{base_unit}"
            elif abs_val >= 1e3:
                return f"{val / 1e3:.4f}", f"k{base_unit}"
            elif abs_val >= 1:
                return f"{val:.4f}", base_unit
            elif abs_val >= 1e-3:
                return f"{val * 1e3:.3f}", f"m{base_unit}"
            elif abs_val >= 1e-6:
                return f"{val * 1e6:.3f}", f"µ{base_unit}"
            elif abs_val >= 1e-9:
                return f"{val * 1e9:.3f}", f"n{base_unit}"
            else:
                return f"{val:.4e}", base_unit
    except ValueError:
        if "O.L" in raw_str or "over" in raw_str.lower():
            return "O.L", base_unit
        return raw_str.strip(), base_unit


def configure_dmm(inst, mode):
    inst.write(f"CONF:{MODES_CONFIG[mode][0]}")
    time.sleep(0.1)
    if mode not in ("Diode", "Continuity", "Frequency", "Temperature"):
        # XDM1041/2041 manual: AUTO takes no parameter.
        inst.write("AUTO")
        time.sleep(0.1)
