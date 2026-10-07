"""Calibration bounds, USB acknowledgement, lifecycle and demo behavior."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import threading

import numpy as np
import pytest
from bench_console import create_app
from bench_console.hantek.backend import DemoBackend, SigrokBackend
from bench_console.hantek.settings import CALIBRATION_FREQUENCIES, validate, plan


@pytest.mark.parametrize('frequency', CALIBRATION_FREQUENCIES)
def test_calibration_selection_and_demo_output(frequency):
    s = validate({'calibration_frequency': frequency})
    p = plan(s); p.update(rate=100000, count=20000)
    output = DemoBackend().capture({'driver': 'demo'}, s, p, threading.Event())[1]
    if frequency == 0:
        assert np.all(output == 0)
    else:
        # Count complete rising transitions rather than applying the bench
        # measurement minimum-eight-samples rule to the 20 kHz demo setting.
        edges = np.count_nonzero(np.diff(output) > 0)
        assert abs(edges - frequency * p['count'] / p['rate']) <= 1
        assert set(np.unique(output)) == {-1., 1.}


@pytest.mark.parametrize('frequency', [True, -1, 51, 1000.1, 20001, float('nan'), 'bad'])
def test_invalid_calibration_settings(frequency):
    with pytest.raises(ValueError): validate({'calibration_frequency': frequency})


@pytest.fixture
def controller():
    app = create_app({'TESTING': True})
    c = app.extensions['hantek_controller']
    yield c
    c.shutdown(); app.extensions['bench_controller'].shutdown()


def test_apply_without_acquisition_and_clear_confirmation_on_disconnect(controller):
    c = controller
    assert c.state()['calibration_frequency'] is None
    c.connect({'mode': 'scope', 'demo': True})
    assert c.state()['calibration_frequency'] == 1000
    c.configure({'calibration_frequency': 2000})
    assert not c.state()['running']
    assert c.state()['calibration_frequency'] == 2000
    c.configure({'calibration_frequency': 0})
    assert c.state()['calibration_frequency'] == 0
    c.disconnect()
    assert c.state()['calibration_frequency'] is None


def test_frequency_change_stops_capture_before_usb_and_resumes(controller):
    c = controller; c.connect({'mode': 'scope', 'demo': True})
    entered = threading.Event()
    class Tracked(DemoBackend):
        active = False
        applied = False
        def capture(self, *args):
            self.active = True; entered.set()
            try: return super().capture(*args)
            finally: self.active = False
        def calibrate(self, device, frequency):
            assert not self.active and c.worker is None and not c.running
            self.applied = True
            return frequency
    c.backend = backend = Tracked()
    c.run(); assert entered.wait(1)
    c.configure({'calibration_frequency': 500})
    assert backend.applied and c.running and c.settings['calibration_frequency'] == 500
    c.stop_capture()


def test_failed_readback_stops_and_does_not_commit_or_claim_confirmation(controller):
    c = controller; c.connect({'mode': 'scope', 'demo': True})
    class Failed(DemoBackend):
        def calibrate(self, device, frequency):
            raise RuntimeError('USB readback failed')
    c.backend = Failed(); c.run()
    with pytest.raises(RuntimeError, match='readback'):
        c.configure({'calibration_frequency': 5000})
    assert not c.running and c.settings['calibration_frequency'] == 1000
    assert c.state()['calibration_frequency'] is None
    assert c.last_frame is None and c.last_record is None
    assert 'readback' in c.state()['error']
    c.backend = DemoBackend()
    c.configure({'calibration_frequency': 1000})  # Retry even the same selection.
    assert c.state()['calibration_frequency'] == 1000


def test_logic_does_not_issue_calibration(controller):
    c = controller; c.connect({'mode': 'logic', 'demo': True})
    assert c.state()['calibration_frequency'] is None
    with pytest.raises(ValueError, match='scope mode'):
        c.configure({'calibration_frequency': 500})


def test_scope_helper_uses_local_firmware_and_verifies_response(monkeypatch):
    calls = []
    class Process:
        returncode = 0
        def __init__(self, arguments, **kwargs): calls.append((arguments, kwargs))
        def communicate(self, timeout): return json.dumps({'calibration_frequency': 2000}), ''
        def poll(self): return 0
    monkeypatch.setattr('bench_console.hantek.backend.build_helper', lambda: Path('/test-helper'))
    monkeypatch.setattr('bench_console.hantek.backend.subprocess.Popen', Process)
    b = SigrokBackend()
    device = {'driver': 'hantek-6xxx', 'conn': '1.7'}
    assert b.calibrate(device, 2000) == 2000
    args, kwargs = calls[-1]
    assert '--calibration-only' in args and args[-1] == '2000'
    assert (Path(kwargs['env']['SIGROK_FIRMWARE_DIR']) / 'fx2lafw-hantek-6022bl.fw').is_file()
    with pytest.raises(RuntimeError, match='readback mismatch'): b.calibrate(device, 500)


def test_native_usb_protocol(tmp_path):
    """Exercise the production C transfer code against a USB control stub."""
    if not shutil.which('cc') or not shutil.which('pkg-config'):
        pytest.skip('Native compiler/pkg-config unavailable')
    flags = subprocess.run(['pkg-config', '--cflags', '--libs', 'libusb-1.0'],
                           text=True, capture_output=True)
    if flags.returncode: pytest.skip('libusb development files unavailable')
    source = Path(__file__).resolve().parents[1] / 'bench_console/hantek/native'
    harness = tmp_path / 'usb-test.c'
    harness.write_text(r'''
#include "calibration.h"
#include <assert.h>
#include <string.h>
static int current=5, reads=0, writes=0, fault=0;
int LIBUSB_CALL libusb_control_transfer(libusb_device_handle *h, uint8_t type,
    uint8_t req, uint16_t value, uint16_t index, unsigned char *data,
    uint16_t length, unsigned int timeout) {
    (void)h; assert(value==0 && index==0 && timeout==1000);
    if(req==0xe7) {
        assert(type==0xc0 && length==6); reads++;
        memcpy(data,"BCAL",4); data[4]=1; data[5]=current;
        if(fault==1) data[0]='X';
        if(fault==2) return 5;
        if(fault==5) data[4]=2;
        return 6;
    }
    assert(req==0xe8 && type==0x40 && length==1); writes++;
    assert(data[0]<=9);
    if(fault==3) return -7;
    if(fault!=4) current=data[0];
    return 1;
}
int main(void) {
    int frequencies[]={0,50,100,200,500,1000,2000,5000,10000,20000};
    for(int i=0;i<10;i++) assert(calibration_code(frequencies[i])==i);
    assert(calibration_code(51)==-1);
    assert(calibration_transfer(NULL,5)==0 && writes==0 && reads==1);
    assert(calibration_transfer(NULL,6)==0 && writes==1 && reads==3 && current==6);
    assert(calibration_transfer(NULL,0)==0 && current==0);
    for(fault=1;fault<=5;fault++) {
        current=5; reads=0; writes=0;
        assert(calibration_transfer(NULL,6)!=0);
        if(fault==1 || fault==2 || fault==5) assert(writes==0);
    }
    assert(calibration_transfer(NULL,10)!=0);
    return 0;
}
''')
    binary = tmp_path / 'usb-test'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-I', str(source),
                    str(harness), str(source / 'calibration.c'), '-o', str(binary),
                    *shlex.split(flags.stdout)], check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, capture_output=True)
