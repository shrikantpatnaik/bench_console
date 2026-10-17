"""Cancellable libsigrok subprocesses and an explicitly selected demo source."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

import numpy as np
from .build import build_helper
from .settings import SCOPE_SETTLING_SAMPLES

class CaptureCancelled(Exception):
    pass

class SigrokBackend:
    def __init__(self):
        self._lock = threading.Lock()
        self._process = None

    def cancel(self):
        with self._lock:
            process = self._process
            if process and process.poll() is None:
                process.terminate()

    def _run(self, arguments, timeout, stop=None):
        helper = build_helper()
        environment = os.environ.copy()
        if '--driver' in arguments and arguments[arguments.index('--driver') + 1] == 'hantek-6xxx':
            # libsigrok 0.5.2 searches this before user/system firmware folders.
            # Only override the scope helper; logic uses distribution firmware.
            environment['SIGROK_FIRMWARE_DIR'] = str(Path(__file__).parent / 'firmware' / 'sigrok-firmware')
        with self._lock:
            if stop and stop.is_set():
                raise CaptureCancelled()
            process = subprocess.Popen([str(helper), *arguments], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, env=environment)
            self._process = process
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    output, error = process.communicate(timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    if stop and stop.is_set():
                        raise CaptureCancelled()
                    if time.monotonic() > deadline:
                        raise RuntimeError('USB capture timed out; check mode, USB connection and firmware')
            if stop and stop.is_set():
                raise CaptureCancelled()
            if process.returncode:
                raise RuntimeError((error.strip() or 'libsigrok acquisition failed')[-1200:])
            try:
                return json.loads(output)
            except json.JSONDecodeError as exc:
                raise RuntimeError('Invalid response from acquisition helper') from exc
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            with self._lock:
                if self._process is process:
                    self._process = None

    def discover(self, mode):
        driver = 'hantek-6xxx' if mode == 'scope' else 'fx2lafw'
        return self._run(['--driver', driver, '--mode', mode, '--scan'], 20)

    def capture(self, device, settings, plan, stop):
        args = ['--driver', device['driver'], '--mode', settings['mode'],
                '--conn', device['conn'], '--rate', str(plan['rate']),
                '--samples', str(plan['count']), '--vdiv1', str(plan['gains'][0]),
                '--vdiv2', str(plan['gains'][1])]
        if settings['mode'] == 'scope':
            args += ['--calibration-frequency', str(settings['calibration_frequency'])]
        with tempfile.TemporaryDirectory(prefix='hantek-capture-') as folder:
            meta = self._run([*args, '--output', folder],
                             max(12, plan['count'] / plan['rate'] + 10), stop)
            if meta['rate'] != plan['rate']:
                raise RuntimeError('Instrument sample-rate readback differs from the requested rate')
            if settings['mode'] == 'scope':
                if device['driver'] == 'hantek-6xxx' and (
                    meta.get('settling_samples') != SCOPE_SETTLING_SAMPLES or
                    meta.get('discarded') != [SCOPE_SETTLING_SAMPLES] * 2
                ):
                    raise RuntimeError('Scope helper has not verified ADC settling. Rebuild with '
                                       'python -m bench_console.hantek.build; remove an old HANTEK_HELPER override.')
                data = [np.fromfile(Path(folder) / f'CH{i+1}.f32', dtype=np.float32)
                        for i in range(2)]
                if any(len(ch) != plan['count'] for ch in data):
                    raise RuntimeError('Incomplete analog record; no stale values were published')
                if not all(np.isfinite(ch).all() for ch in data):
                    raise RuntimeError('Invalid analog samples')
            else:
                data = np.fromfile(Path(folder) / 'logic.bin', dtype=np.uint8)
                if len(data) != plan['count']:
                    raise RuntimeError('Incomplete digital record')
            return data

    def calibrate(self, device, frequency):
        reply = self._run(['--driver', device['driver'], '--mode', 'scope',
                           '--conn', device['conn'], '--calibration-only',
                           '--calibration-frequency', str(frequency)], 20)
        if reply.get('calibration_frequency') != frequency:
            raise RuntimeError('Calibration frequency readback mismatch; output state is unverified')
        return frequency

class DemoBackend:
    """No USB I/O. Frames and exports always identify themselves as demo."""
    def cancel(self):
        pass

    def discover(self, mode):
        return [dict(driver='demo', conn='demo', vendor='Simulated', model='6022BL', mode=mode)]

    def calibrate(self, device, frequency):
        return frequency

    def capture(self, device, settings, plan, stop):
        if stop.wait(min(.1, plan['count'] / plan['rate'])):
            raise CaptureCancelled()
        t = np.arange(plan['count']) / plan['rate']
        if settings['mode'] == 'scope':
            frequency = settings['calibration_frequency']
            calibration = np.where((t * frequency) % 1 < .5, 1., -1.) if frequency else np.zeros(len(t))
            return [np.clip(1.5 * np.sin(2*np.pi*1000*t), -5*plan['gains'][0], 5*plan['gains'][0]),
                    np.clip(calibration, -5*plan['gains'][1], 5*plan['gains'][1])]
        data = np.zeros(plan['count'], dtype=np.uint8)
        for bit in range(8):
            data |= (((t * (1000 / 2**bit)) % 1 >= .5).astype(np.uint8) << bit)
        return data
