Bench Console — OWON XDM1041 + SPE3102 / Hantek 6022BL

SETUP (macOS / Linux)
1. Extract the ZIP into a folder, then open Terminal in that folder.
2. Run:
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   python main.py
3. Open http://localhost:5001 in your browser.

This revision is a multi-file project. Extract the entire folder and run
main.py from that folder; copying main.py alone is no longer sufficient.
Socket.IO client 4.8.1 is bundled in bench_console/static/vendor with its MIT
license. The dashboard serves all scripts and styles locally and can run on
a LAN without internet access once the Python dependencies are installed.

CONNECTING
Connect both instruments to the computer running Python. Each has its own port
and connection button. Choose from the port suggestions or enter a resource:
  /dev/cu.wchusbserial1410             (macOS example; use your actual path)
  /dev/ttyUSB0                        (Linux example)
  COM3                               (Windows example)
  ASRL/dev/cu.wchusbserial1410::INSTR  (full VISA serial resource)
A USBTMC VISA resource can also be entered; that transport needs a working
PyVISA USB backend, typically pyusb/libusb. The serial backend is included in
the requirements and matches the original app's serial workflow.

Baud defaults to 115200 independently for both devices. For a serial resource,
select the rate matching the actual instrument; framing is 8-N-1, no flow
control, with LF command/reply termination. The SPE manual does not establish
a universal serial baud rate for all variants, so this default must be checked
on your hardware. USBTMC resources ignore the baud selection. No automatic
baud probing, device reset, or automatic output enable is performed.

SPE query timeouts close the connection to avoid confusing delayed replies.
Serial XDM1041 MEAS? queries have a 5-second timeout and a 0.1-second write/read
delay. Polling is every 0.5 seconds after the previous query, with a 1-second
settling period following connection or a measurement-mode change. The app
enters SCPI mode with SYST:REM and uses the documented parameterless AUTO
command, replacing the incorrect RANG:AUTO ON command from the earlier build.

On a serial meter measurement timeout, the app clears the displayed reading,
waits 1 second, discards queued serial input, and retries. A valid reply resets
the consecutive-timeout count. Three consecutive timeouts, non-timeout errors,
or a failure to discard queued input close the meter connection. Only serial
meter measurement queries get retries; other device operations do not.
Dual-display MEAS? replies use the primary value before the comma.

If the meter still times out, note whether the IDN connection succeeds and
which measurement mode is selected. Verify the actual baud rate, driver, cable,
and that another program is not using the port, then reconnect.

POWER-SUPPLY CONTROLS
- Live measured voltage, current, and power, plus queried output state.
- Voltage setpoint: 0–30 V, in 0.01 V steps.
- Current limit: 0–10 A, in 0.001 A steps. This is the constant-current limit;
  it does not force that current through a load.
- This app conservatively requires voltage setpoint x current limit <= 200 W.
  Examples: 30 V / 6 A, 20 V / 10 A, or 5 V / 10 A.
- Apply settings writes the setpoints and checks the readback. If output is
  already ON, the settings take effect live. Multi-command changes are not
  atomic; an interrupted change can leave one setting applied.
- The compact POWER OUTPUT toggle sits directly below measured power.
  It follows the queried output state and stays pending until the command is
  acknowledged. Unknown state disables the toggle. Enabling uses the confirmed
  instrument settings, not unapplied values in the input fields.
- Unsaved setpoint or protection edits block ON until applied or loaded from
  the supply. Unsaved edits never block OFF.
- Load actual settings replaces any unsaved edits with the instrument values.
- Connect enters remote mode and reads existing settings/output state.
- Disconnect sends OFF, queries confirmation, returns local mode, and closes
  the instrument. Failed OFF confirmation is reported as unknown, never OFF.
- Browser close or refresh leaves the hardware session and output running.
- Normal Python shutdown attempts OFF and local mode. Abrupt process termination,
  power loss, or a broken link cannot guarantee OFF; use the front panel.
- Editable overvoltage protection (OVP): 0–31 V in 0.01 V steps.
- Editable overcurrent protection (OCP): 0–10.1 A in 0.001 A steps.
- Apply protection writes VOLT:LIM and CURR:LIM, then queries both values to
  verify. These are hardware output-trip thresholds, separate from the voltage
  setpoint and constant-current limit. Protection ranges are independent of
  the 200 W setpoint constraint.
- Existing protection limits are read on connection and throughout polling.
  Connecting does not change them. Polling preserves unsaved edits; Load actual
  limits restores the verified instrument values to the inputs.
- Applying limits can cut off an already active output if it reaches a trip
  threshold. After a trip, adjust the settings and explicitly toggle ON to
  restart; the app never automatically restarts the output.
- Applying protection never writes output ON/OFF or changes voltage/current
  setpoints. A failed multi-command update may leave one threshold changed.
- This version does not edit List programs or reset the supply.

MULTIMETER
Original measurement modes, formatting, and continuity indicator are retained;
autoranging uses the XDM1041's documented AUTO command. Instrument I/O is serialized, and reconnects use new
polling stop events. Device identity is checked before configuration commands;
this build intentionally accepts only XDM1041 and SPE3102 model IDs.

RUNTIME
Run a single Python process; do not enable Flask's reloader or multiple workers.
The default bind address remains 0.0.0.0 and port remains 5001. To restrict it
to this computer: HOST=127.0.0.1 python main.py
This is a bench application without authentication; anyone who can access it
can issue instrument controls. Socket.IO uses same-origin requests.

VERIFICATION
Automated tests use simulated instruments, not real hardware. They cover
SCPI command ordering, readback, both devices, validation, timeout handling,
disconnection, page refresh, serialized polling/control, and stale workers.
To run: python -m pip install pytest
        python -m pytest -q
Physical USB/serial behavior and firmware compatibility still need bench testing.

PRIMARY REFERENCES
OWON SP&SPE Single Channel DC Power Supply Programming Manual v2.0.0:
https://owonprecision.com/media/83e97a7a-227b-4e05-a303-3997f316537b/sp-spe-series-single-channel-dc-power-supply-programming-manual-v2-0-0-1220.pdf
OWON SPE ratings (SPE3102: 30 V, 10 A, 200 W):
https://in.owon.com/products_owon_spe_series_1_ch_dc_power_supply
PyVISA instrument communication:
https://pyvisa.readthedocs.io/en/1.16.0/introduction/communication.html

XDM1041 & XDM2041 programming manual (SYST:REM, AUTO, MEAS?):
https://files.owon.com.cn/software/Application/XDM1000_Digital_Multimeter_Programming_Manual.pdf

PROJECT STRUCTURE
main.py                           Small startup entry point
bench_console/__init__.py         Flask create_app factory
bench_console/cli.py              Socket.IO server and shutdown lifecycle
bench_console/config.py           Defaults and instrument capabilities
bench_console/extensions.py       Per-app Socket.IO initialization
bench_console/web.py              Dashboard Blueprint and HTTP route
bench_console/events.py           Socket.IO request adapters
bench_console/instruments/
  controller.py                   Device sessions, locks, discovery and polling
  common.py                       Resource normalization and numeric parsing
  multimeter.py                   XDM1041 formatting/configuration
  power_supply.py                 SPE3102 SCPI and validation
bench_console/templates/          Base page, dashboard and reusable panel parts
bench_console/static/css/         Dashboard styles
bench_console/static/js/          Browser state and controls
bench_console/static/vendor/      Bundled Socket.IO client and license
tests/                           Hardware simulations and factory/route checks
pyproject.toml                    Optional installable package metadata
requirements.txt                  Runtime dependencies
requirements-dev.txt              Test dependencies
pytest.ini                        Test discovery settings

The package uses a Flask application factory and a dashboard Blueprint. The
Socket.IO extension and instrument controller belong to each application.
Creating an app or loading the page does not initialize VISA; discovery and
connection requests initialize it on demand. No instrument opens on import.
Background workers keep explicit references to their controller and Socket.IO
instance rather than depending on a Flask request context.

Both panels place their connection controls at the bottom, with a top note
that disappears while connected and returns if the connection is lost. The
compact output toggle follows measured power and displays ON, OFF or UNKNOWN.

DEVELOPMENT
Run tests from the extracted project folder:
  python -m pip install -r requirements-dev.txt
  python -m pytest -q

Optional editable package installation:
  python -m pip install -e ".[dev]"
  owon-bench
The traditional python main.py command works without package installation.

VALIDATION
103 simulated-device, numerical-analysis, USB-protocol and Flask tests passed. UI behavior checks cover layout,
connection-note visibility, toggle states, pending commands, protection edits,
and disconnects. Physical hardware verification remains necessary.

OVP/OCP protection ranges, OWON SPE technical specification:
https://files.owon.com.cn/specifications/SPE_Series_Power_Supply.pdf
Flask factories / package structure:
https://flask.palletsprojects.com/en/stable/patterns/appfactories/
https://flask.palletsprojects.com/en/stable/patterns/packages/


HANTEK 6022BL — NEW PAGE /hantek
Open http://<pi-ip>:5001/hantek (or http://localhost:5001/hantek locally).
The OWON page remains at /. A navigation link joins the two pages. Both use
local browser assets; there is no CDN dependency. Each instrument subsystem
has independent state, and the Hantek uses its own Socket.IO namespace.

RASPBERRY PI / DEBIAN SETUP FOR REAL USB ACQUISITION
Run these from the extracted project folder:
  sudo apt update
  sudo apt install -y python3-venv build-essential pkg-config libsigrok-dev libusb-1.0-0-dev sigrok-firmware-fx2lafw
  sudo usermod -aG plugdev,dialout "$USER"
  sudo install -m 644 bench_console/hantek/native/60-hantek-bench.rules /etc/udev/rules.d/60-hantek-bench.rules
  sudo udevadm control --reload-rules
Log out and reconnect SSH, then unplug and reconnect the USB instrument.
Use the same user account to run Python or a systemd service.
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install -r requirements.txt
  python -m bench_console.hantek.build
  python main.py
Existing installations should rerun pip install -r requirements.txt to add
NumPy. The native helper is compiled for the host architecture and cached
under ~/.cache/owon-bench-console. No desktop, browser on the Pi, NI-VISA,
Windows DLL, or precompiled x86 binary is required. Demo needs only the Python
requirements; libsigrok/compiler/USB permission setup is needed for hardware.
Hardware scan/connect will also build the helper on demand. The helper source
is included in the project and installable Python package.
Optional HANTEK_HELPER=/absolute/path/to/helper overrides the compiled helper.

HARDWARE CONNECTION
1. Select Oscilloscope (H/P button pressed) or Logic analyzer (H/P released).
2. Click Scan USB; firmware may be loaded into device RAM by libsigrok.
   After upgrading for calibration control, unplug USB power once first,
   then reconnect in scope mode so the bundled RAM firmware can load.
3. Select the matching device and Connect. Acquisition initially stays stopped.
4. Click Run, Single, or Auto Set after obtaining a waveform.
Disconnect before changing the physical mode. A USB replug/rescan can be
necessary after switching personality. Close other Hantek/sigrok applications.
The logic personality shares the Saleae Logic USB ID and can be named Saleae
Logic or an FX2 device. Select your actual 6022BL; the USB identity cannot
uniquely distinguish it from other devices with that shared personality.

CAPABILITIES AND IMPORTANT LIMITS
- Two analog channels with independent front-end gain, per-channel display
  V/div, probe x1/x10/x100/x1000, vertical position, invert and visibility.
- DC, software AC (subtract record mean) and software GND display coupling.
  These software modes do not change the physical input coupling.
- 1–2–5 time/div, automatic or explicit supported sample rates, up to 48 MS/s
  analog and 24 MS/s logic when accepted by firmware and USB transport.
- Standard sigrok support exposes eight digital channels. This release does
  not claim support for all 16 hardware logic channels. The two functions are
  mutually exclusive; this is not simultaneous mixed-signal acquisition.
- Software edge triggering: Auto, Normal and Single, CH1/CH2 or D0–D7 source,
  rising/falling/either edge, analog level/hysteresis, pre-trigger position and
  holdoff. Single stops only on a matching edge; it continues waiting otherwise.
- Normal and Single search finite captures. There are gaps between captures;
  pulses occurring between records can be missed. This is not a hardware
  trigger, continuous recorder or guaranteed event-capture system.
- The libsigrok front-end ranges are 0.1, 0.25, 0.5 and 1 V/div, with ten
  divisions of ADC range at the BNC. Other display scales are software zoom.
  The plot uses eight vertical divisions. It reports actual front-end range,
  probe-scaled display range, sample rate, sample count and ADC clipping.
- Highest sample-rate mode and clipping/offset accuracy need testing on the
  particular unit and firmware. No extra ADC resolution, analog bandwidth,
  hardware memory depth or calibration accuracy is implied by display controls.
- Bounded records (up to 2 Mi samples) keep server memory finite. Auto rate
  targets a practical record size. Explicit rates that cannot cover the
  selected time window are rejected instead of silently changing the scale.
- Samples are received in binary via a small libsigrok C helper, not CSV.
  Python computes measurements on the full visible record. Min/max envelopes
  reduce browser traffic without hiding narrow analog spikes. Dense digital
  rendering is limited to 4,000 transitions/channel and shows a warning;
  full samples remain available in CSV/VCD exports.
- Trigger-aligned averaging (2/4/8/16 records), up to 12 traces of display
  persistence, CH1+CH2, CH1-CH2, multiplication/division, XY and FFT. Division
  by zero is counted as invalid and shown in the plot warning.
- FFT uses a contiguous window up to 16,384 samples, a Hann window, mean
  removal and amplitude in dBV. The page reports bin width and Nyquist range.
- Time and CH1 voltage cursors; drag time cursors or use slider/numeric controls.
  CSV exports full visible samples; VCD exports digital transitions, with
  record-start time zero. PNG saves the current plot with acquisition metadata.
- Logic timing table: frequency, period, positive/negative width, high occupancy
  and transition count. No protocol decoding or adjustable hardware logic
  threshold is implemented in this release.
- Twenty automatic analog measurements: Vpp, amplitude, maximum/minimum,
  top/middle/base, mean, RMS, AC RMS, frequency/period, 10–90% rise and 90–10%
  fall time, positive/negative pulse width and duty, overshoot/preshoot.
  Top/base use histogram estimates. Timing measurements require at least two
  complete periods, >=8 samples/period and reasonably consistent periods;
  insufficient data shows a dash. These are software estimates, not calibrated
  precision measurements. Overshoot/preshoot are most useful on pulse signals.
- Stop retains the last record. While waiting for a trigger the page marks
  the retained record. USB errors clear the session and invalidate its exports.
  Browser close leaves a running hardware session active; Python shutdown
  cancels the acquisition subprocess. Multiple browsers share one owner and
  settings. Run one server process; do not use multiple WSGI workers.

CALIBRATION OUTPUT
The /hantek scope page has a Calibration out frequency selector: Off,
50/100/200/500 Hz and 1/2/5/10/20 kHz. Default: 1 kHz. Changes apply while
stopped, or briefly stop and restart a running acquisition. Calibration
output control is unavailable in logic mode. The selector is enabled after
connecting in scope mode. Demo CH2 follows the selection; Demo CH1 stays
at 1 kHz. The selection is included in CSV capture metadata.

The package includes a small extension to sigrok's 6022BL RAM firmware;
stock sigrok firmware does not implement selectable calibration output.
No permanent device update is needed, and no EEPROM is written. Scope
helpers automatically use the local firmware; logic uses system firmware.
Unplug USB power once after upgrading, close other Hantek applications,
reconnect in scope mode and Scan USB. If stock firmware was already loaded,
the app explains that it must be reconnected rather than claiming success.

"USB setting verified" means the firmware acknowledges the selected index.
It does not mean the output frequency has been physically measured. CAL OUT
is a nominal square wave with fixed amplitude; Off holds its pin low. Connect
CAL OUT to CH1 or CH2 to see its live measured frequency. Accuracy and jitter,
especially at the higher selections during USB activity, need bench testing.
The compiled 8051 firmware works on Pi without installing a firmware compiler.
Complete corresponding source, licenses and rebuild instructions are bundled
under bench_console/hantek/firmware. Details are in its README.txt.

DEMO AND VALIDATION
Demo is a separate, explicitly selected source with generated sine, square
and digital waveforms. Plot, live state, measurements and exports mark DEMO.
The Python tests cover signal calculations, trigger behavior, capture bounds,
namespace separation, demo acquisition, stopping, errors and CSV/VCD exports,
in addition to the existing OWON checks. Native compilation and browser UI
behavior are checked separately. Real Hantek USB acquisition and Raspberry Pi
performance have not been tested in this development environment.
Calibration tests also cover allowed values, stopped/running updates, failed
readback, logic-mode exclusion, demo signals and the C USB command protocol.

ADDED FILES
bench_console/hantek/settings.py      Settings validation and capture planning
bench_console/hantek/build.py         Architecture-local helper compilation
bench_console/hantek/native/capture.c libsigrok binary acquisition bridge
bench_console/hantek/native/calibration.c USB calibration command/readback
bench_console/hantek/firmware          Local RAM firmware, source and licenses
bench_console/hantek/backend.py       Cancellable USB and demo sources
bench_console/hantek/analysis.py      Measurements, trigger search, FFT, drawing
bench_console/hantek/controller.py    Shared Hantek acquisition lifecycle
bench_console/hantek/events.py        Independent Socket.IO namespace
bench_console/hantek/web.py           /hantek and full-resolution exports
bench_console/templates/hantek.html   Scope and logic page
bench_console/static/css/hantek.css   Responsive instrument layout
bench_console/static/js/hantek.js     Canvas plot, cursors and UI controls
bench_console/hantek/native/60-hantek-bench.rules        Headless USB access for plugdev

HANTEK REFERENCES
https://sigrok.org/wiki/Hantek_6022BL
https://sigrok.org/wiki/Using_sigrok-cli_with_an_oscilloscope
https://github.com/sigrokproject/libsigrok/tree/master/src/hardware/hantek-6xxx
https://www.hantek.com/products/detail/153
