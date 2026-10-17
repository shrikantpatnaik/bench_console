"""One acquisition owner shared by browsers, independent of OWON sessions."""
import copy
from collections import deque
import threading
import time

import numpy as np
from . import analysis
from .backend import SigrokBackend, DemoBackend, CaptureCancelled
from .settings import DEFAULTS, VDIVS, TIMES, SCOPE_SETTLING_SAMPLES, validate, plan

class HantekController:
    def __init__(self, app, socketio):
        self.app, self.socketio = app, socketio
        self.lock = threading.RLock()
        self.commands = threading.RLock()
        self.settings = copy.deepcopy(DEFAULTS)
        self.backend = None
        self.device = None
        self.worker = None
        self.stop = threading.Event()
        self.closed = False
        self.generation = 0
        self.running = False
        self.status = 'disconnected'
        self.error = ''
        self.last_frame = None
        self.last_record = None
        self.candidates = []
        self.sequence = 0
        self.average = deque(maxlen=4)
        self.revision = 0
        self.calibration_frequency = None  # Confirmed USB setting, not a measured frequency.

    def state(self):
        with self.lock:
            return dict(connected=self.device is not None, running=self.running,
                        status=self.status, error=self.error, device=copy.deepcopy(self.device),
                        settings=copy.deepcopy(self.settings), revision=self.revision,
                        calibration_frequency=self.calibration_frequency,
                        frame_id=self.last_frame['id'] if self.last_frame else None)

    def publish(self):
        self.socketio.emit('state', self.state(), namespace='/hantek')

    def discover(self, mode):
        with self.commands:
            if mode not in ('scope', 'logic'): raise ValueError('Choose scope or logic mode')
            with self.lock:
                if self.device: raise ValueError('Disconnect before scanning USB devices')
                if self.closed: raise ValueError('Application is shutting down')
            candidates = SigrokBackend().discover(mode)
            with self.lock:
                self.candidates = candidates
            return candidates

    def connect(self, request):
        with self.commands:
            with self.lock:
                if self.closed: raise ValueError('Application is shutting down')
                if self.device: raise ValueError('Already connected; disconnect first')
                s = validate({'mode': request.get('mode', 'scope'), 'sample_rate': 'auto'}, self.settings)
                self.status, self.error = 'connecting', ''
            self.publish()
            try:
                demo = request.get('demo', False)
                if type(demo) is not bool: raise ValueError('Demo selection must be true or false')
                backend = DemoBackend() if demo else SigrokBackend()
                if demo:
                    device = backend.discover(s['mode'])[0]
                else:
                    candidates = backend.discover(s['mode'])
                    conn = request.get('conn', '')
                    matches = [d for d in candidates if d['conn'] == conn] if conn else candidates
                    if len(matches) != 1:
                        raise ValueError('Select one detected USB device, then connect')
                    device = matches[0]
                with self.lock:
                    self.backend = backend
                    self.stop = threading.Event()
                probe = plan(s)
                probe.update(count=1024, screen=512)
                backend.capture(device, s, probe, self.stop)
                with self.lock:
                    self.device, self.settings = device, s
                    self.calibration_frequency = s['calibration_frequency'] if s['mode'] == 'scope' else None
                    self.status, self.error = 'stopped', ''
                    self.last_frame = self.last_record = None
                    self.revision += 1
                self.publish()
            except Exception:
                with self.lock:
                    self.backend = self.device = None
                    self.calibration_frequency = None
                    self.status = 'disconnected'
                raise

    def _stop_worker(self):
        with self.lock:
            self.generation += 1
            self.stop.set()
            backend, worker = self.backend, self.worker
            self.running = False
        if backend: backend.cancel()
        if worker and worker is not threading.current_thread():
            worker.join(timeout=4)
            if worker.is_alive():
                raise RuntimeError('Capture is still stopping; wait before reconnecting')
        with self.lock:
            self.worker = None

    def disconnect(self):
        with self.commands:
            self._stop_worker()
            with self.lock:
                self.device = self.backend = None
                self.calibration_frequency = None
                self.last_frame = self.last_record = None
                self.status, self.error = 'disconnected', ''
                self.average.clear()
            self.publish()

    def stop_capture(self):
        with self.commands:
            self._stop_worker()
            with self.lock:
                self.status = 'stopped' if self.device else 'disconnected'
            self.publish()

    def configure(self, request):
        with self.commands:
            with self.lock:
                s = validate(request, self.settings)
                if self.device and s['mode'] != self.settings['mode']:
                    raise ValueError('Disconnect and change the physical H/P mode before switching')
                was_running = self.running
                calibration_changed = s['calibration_frequency'] != self.settings['calibration_frequency']
                if self.device and s['mode'] == 'logic' and calibration_changed:
                    raise ValueError('Calibration output control requires scope mode')
                backend, device = self.backend, self.device
            self._stop_worker()
            if device and s['mode'] == 'scope' and (calibration_changed or self.calibration_frequency is None):
                try:
                    confirmed = backend.calibrate(device, s['calibration_frequency'])
                    if confirmed != s['calibration_frequency']:
                        raise RuntimeError('Calibration frequency readback mismatch')
                except Exception as exc:
                    with self.lock:
                        self.calibration_frequency = None
                        self.last_frame = self.last_record = None
                        self.status, self.error = 'stopped', str(exc)
                    self.publish()
                    raise
                with self.lock:
                    self.calibration_frequency = confirmed
            with self.lock:
                self.settings = s
                self.revision += 1
                self.average.clear()
                self.last_frame = self.last_record = None
                self.status = 'stopped' if self.device else 'disconnected'
                self.error = ''
            self.publish()
            if was_running: self.run()

    def run(self, single=False):
        with self.commands:
            with self.lock:
                if not self.device: raise ValueError('Connect a device or choose Demo first')
                if self.closed: raise ValueError('Application is shutting down')
            self._stop_worker()
            with self.lock:
                if single: self.settings['trigger']['mode'] = 'single'
                self.stop = threading.Event()
                self.running, self.status, self.error = True, 'running', ''
                self.average.clear()
                self.average = deque(maxlen=self.settings['average_count'])
                generation = self.generation
                worker = threading.Thread(target=self._acquire, args=(generation, self.stop),
                                          daemon=True, name='hantek-capture')
                self.worker = worker
                worker.start()
            self.publish()

    def autoset(self):
        with self.lock:
            record = self.last_record
            settings = copy.deepcopy(self.settings)
        if not record or record['mode'] != 'scope':
            raise ValueError('Run scope acquisition before using Auto Set')
        for i, y in enumerate(record['data']):
            ch = settings['channels'][i]
            wanted = max(float(np.ptp(y))/6, float(np.max(np.abs(y)))/4)/ch['probe']
            ch['vdiv'] = next((v for v in VDIVS if v >= wanted), VDIVS[-1])
            ch['offset'] = -float(np.mean(y))
        m = analysis.measurements(record['data'][settings['trigger']['source']], record['rate'])
        if m['period']:
            wanted = m['period'] * 3 / 10
            settings['time_div'] = min(TIMES, key=lambda t: abs(np.log(t/wanted)))
        settings['trigger']['level'] = m['mean'] or 0.
        settings['trigger']['mode'] = 'auto'
        settings['sample_rate'] = 'auto'
        self.configure(settings)

    def _acquire(self, generation, stop):
        try:
            while not stop.is_set():
                with self.lock:
                    s = copy.deepcopy(self.settings)
                    device, backend = self.device, self.backend
                    revision = self.revision
                p = plan(s)
                raw = backend.capture(device, s, p, stop)
                if stop.is_set(): break
                frame, record = self._frame(raw, s, p, device)
                with self.lock:
                    if generation != self.generation or stop.is_set(): break
                    self.calibration_frequency = s['calibration_frequency'] if s['mode'] == 'scope' else None
                    if frame is None:
                        self.status = 'waiting'
                    else:
                        self.sequence += 1
                        frame.update(id=self.sequence, revision=revision, captured_at=time.time())
                        self.last_frame, self.last_record = frame, record
                        self.status = 'triggered' if frame['triggered'] else 'auto'
                if frame is not None:
                    self.socketio.emit('frame', frame, namespace='/hantek')
                self.publish()
                if frame is not None and s['trigger']['mode'] == 'single':
                    with self.lock:
                        self.running, self.status = False, 'single complete'
                    self.publish()
                    break
                if stop.wait(max(.2, s['trigger']['holdoff']) if frame is not None else .2): break
        except CaptureCancelled:
            pass
        except Exception as exc:
            self.app.logger.warning('Hantek acquisition failed: %s', exc)
            with self.lock:
                if generation == self.generation:
                    self.error = str(exc)
                    self.running = False
                    self.status = 'error'
                    self.device = self.backend = None
                    self.calibration_frequency = None
                    self.last_frame = self.last_record = None
            self.publish()
        finally:
            with self.lock:
                if generation == self.generation: self.running = False

    def _frame(self, raw, s, p, device):
        rate, screen = p['rate'], p['screen']
        trigger = copy.deepcopy(s['trigger'])
        pre = int(screen*trigger['pretrigger'])
        if s['mode'] == 'scope':
            data = [analysis.coupled(raw[i], s['channels'][i]) for i in range(2)]
            hit = analysis.trigger_index(data[trigger['source']], trigger, screen, rate)
        else:
            data = [(raw >> i) & 1 for i in range(8)]
            trigger.update(s['logic_trigger'])
            hit = analysis.trigger_index(data[trigger['source']], trigger, screen, rate, True)
        if hit is None and trigger['mode'] != 'auto': return None, None
        start = hit-pre if hit is not None else 0
        window = [ch[start:start+screen].copy() for ch in data]
        frame = dict(mode=s['mode'], demo=device['driver'] == 'demo', triggered=hit is not None,
                     rate=rate, samples=screen, pretrigger_samples=pre,
                     time_div=s['time_div'], trigger_level=trigger['level'],
                     trigger_source=trigger['source'], undersampled=p['undersampled'],
                     sample_period=1/rate, settings=copy.deepcopy(s))
        frame['settling_samples'] = SCOPE_SETTLING_SAMPLES if s['mode'] == 'scope' and device['driver'] == 'hantek-6xxx' else 0
        if s['mode'] == 'scope':
            # Alignment averaging only combines triggered frames of this run.
            if s['acquisition'] == 'average' and hit is not None:
                self.average.append(window)
                window = [np.mean([frame[i] for frame in self.average], axis=0) for i in range(2)]
            frame['averages'] = len(self.average) if s['acquisition'] == 'average' and hit is not None else 1
            frame['channels'] = []
            for i, y in enumerate(window):
                clipped = bool(np.any(np.abs(raw[i][start:start+screen]) >= 4.98*p['gains'][i]))
                measurements, timing_from_record = analysis.scope_measurements(y, data[i], rate)
                frame['channels'].append(dict(points=analysis.envelope(y, rate, pre),
                    measurements=measurements, timing_from_record=timing_from_record,
                    timing_samples=len(data[i]) if timing_from_record else len(y), clipped=clipped,
                    front_end_vdiv=p['gains'][i], probe=s['channels'][i]['probe']))
            mode = s['math']
            if mode in ('add', 'subtract', 'multiply', 'divide'):
                a, b = window
                if mode == 'add': math = a+b
                elif mode == 'subtract': math = a-b
                elif mode == 'multiply': math = a*b
                else:
                    math = np.divide(a,b,out=np.full_like(a,np.nan),where=np.abs(b)>1e-12)
                finite = np.isfinite(math)
                frame['math'] = {'points': analysis.envelope(np.where(finite, math, 0), rate, pre),
                                 'invalid_samples': int(np.count_nonzero(~finite)),
                                 'units': 'V²' if mode == 'multiply' else 'ratio' if mode == 'divide' else 'V'}
            if mode == 'fft': frame['fft'] = analysis.fft(window[trigger['source']], rate)
            if mode == 'xy':
                stride = max(1, screen//2000)
                frame['xy'] = np.column_stack(window)[::stride].tolist()
        else:
            frame['channels'] = []
            for y in window:
                trace = analysis.logic_trace(y, rate, pre)
                trace['measurements'] = analysis.measurements(y, rate)
                trace['high_percent'] = float(np.mean(y))*100
                frame['channels'].append(trace)
        record = dict(mode=s['mode'], demo=frame['demo'], rate=rate, pre=pre,
                      data=window, settings=copy.deepcopy(s), settling_samples=frame['settling_samples'],
                      timing_from_record=[ch.get('timing_from_record', False) for ch in frame['channels']])
        return frame, record

    def export_snapshot(self):
        with self.lock:
            if self.last_record is None: raise ValueError('No captured waveform to export')
            return self.last_record

    def shutdown(self):
        with self.commands:
            self.closed = True
            self.disconnect()
