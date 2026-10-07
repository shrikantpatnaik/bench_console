"""Numerical, lifecycle, namespace and export checks without physical hardware."""
import copy
import threading
import time

import numpy as np
import pytest
from bench_console import create_app
from bench_console.hantek import analysis
from bench_console.hantek.backend import CaptureCancelled, DemoBackend, SigrokBackend
from bench_console.hantek.settings import DEFAULTS, MAX_SAMPLES, validate, plan


def settings(**updates):
    return validate(updates)


def wait_until(condition, timeout=3):
    end = time.monotonic()+timeout
    while time.monotonic()<end:
        if condition(): return
        time.sleep(.01)
    raise AssertionError('Condition did not become true')


def test_measurements_sine_and_flat():
    rate=100000
    t=np.arange(4000)/rate
    m=analysis.measurements(2+3*np.sin(2*np.pi*1000*t), rate)
    assert m['vpp']==pytest.approx(6, rel=.01)
    assert m['mean']==pytest.approx(2, abs=.01)
    assert m['rms']==pytest.approx(np.sqrt(4+4.5), rel=.01)
    assert m['ac_rms']==pytest.approx(3/np.sqrt(2), rel=.01)
    assert m['frequency']==pytest.approx(1000, rel=.01)
    flat=analysis.measurements(np.ones(1000)*3, rate)
    assert flat['vpp']==0 and flat['rms']==3 and flat['frequency'] is None
    assert len(flat)==20


def test_square_wave_width_and_duty():
    y=np.tile(np.r_[np.ones(30),np.zeros(70)], 20)
    m=analysis.measurements(y,100000)
    assert m['frequency']==pytest.approx(1000)
    assert m['positive_duty']==pytest.approx(30)
    assert m['negative_duty']==pytest.approx(70)
    assert m['positive_width']==pytest.approx(.0003)
    assert m['negative_width']==pytest.approx(.0007)


def test_insufficient_cycles_and_high_frequency_rejected():
    m=analysis.measurements(np.r_[np.zeros(100),np.ones(100)],100000)
    assert m['frequency'] is None and m['positive_width'] is None
    m=analysis.measurements(np.tile([0.,1.], 100),100000)
    assert m['frequency'] is None


def test_ac_probe_invert_and_gnd():
    ch=copy.deepcopy(DEFAULTS['channels'][0]); ch.update(probe=10,coupling='AC',invert=True)
    np.testing.assert_allclose(analysis.coupled(np.array([1.,2.,3.]),ch),[10.,0.,-10.])
    ch['coupling']='GND'
    np.testing.assert_array_equal(analysis.coupled(np.ones(3),ch),np.zeros(3))


def test_envelope_preserves_one_sample_spike():
    y=np.zeros(100000); y[55555]=9
    points=analysis.envelope(y,100000,0)
    assert len(points)<=1800
    assert max(v for _,v in points)==9
    assert next(t for t,v in points if v==9)==pytest.approx(.55555)


def test_fft_peak():
    t=np.arange(16384)/100000
    spectrum=analysis.fft(2*np.sin(2*np.pi*1000*t),100000)
    frequency,amplitude=max(spectrum['points'],key=lambda p:p[1])
    assert frequency==pytest.approx(1000,abs=spectrum['resolution'])
    assert amplitude==pytest.approx(20*np.log10(2),abs=1)
    assert spectrum['nyquist']==50000


@pytest.mark.parametrize('data',[
    {'time_div':float('nan')},{'sample_rate':12345},{'mode':'bogus'},
    {'trigger':{'pretrigger':1}},{'trigger':{'level':float('inf')}},
    {'logic_channels':[True]*16},{'trigger':{'unknown':1}}, {'unknown':1},
    {'time_div':1,'sample_rate':48000000},
])
def test_bad_settings_rejected(data):
    with pytest.raises(ValueError): validate(data)


@pytest.mark.parametrize('mode',['scope','logic'])
@pytest.mark.parametrize('time_div',[1e-8,.001,1])
def test_plans_are_bounded(mode,time_div):
    p=plan(settings(mode=mode,time_div=time_div))
    assert 2*p['screen']<=p['count']<=MAX_SAMPLES


def test_software_trigger_pretrigger_and_edges():
    trigger=copy.deepcopy(DEFAULTS['trigger']);trigger.update(level=0,hysteresis=.2,pretrigger=.2)
    y=np.r_[np.full(100,-1.),np.full(200,1.),np.full(100,-1.)]
    trigger['holdoff']=.05
    assert analysis.trigger_index(y,trigger,100,100000)==100
    trigger['edge']='falling'
    assert analysis.trigger_index(y,trigger,100,100000)==300
    assert analysis.trigger_index(np.ones(400),trigger,100,100000) is None


@pytest.fixture
def app():
    app=create_app({'TESTING':True})
    yield app
    app.extensions['hantek_controller'].shutdown()
    app.extensions['bench_controller'].shutdown()


def test_scope_routes_are_lazy_local_and_namespace_isolated(app,monkeypatch):
    def unwanted(): raise AssertionError('Should not build or scan on page load')
    monkeypatch.setattr('bench_console.hantek.backend.build_helper',unwanted)
    client=app.test_client()
    html=client.get('/hantek').get_data(as_text=True)
    assert 'canvas id="waveform"' in html
    assert 'https://' not in html
    for path in ['/static/js/hantek.js','/static/css/hantek.css','/static/vendor/socket.io-4.8.1.min.js']:
        assert client.get(path).status_code==200
    io=app.extensions['socketio']
    bench=io.test_client(app)
    scope=io.test_client(app,namespace='/hantek')
    assert next(x for x in scope.get_received('/hantek') if x['name']=='state')['args'][0]['connected'] is False
    assert bench.get_received()==[]
    scope.disconnect(namespace='/hantek');bench.disconnect()


def test_demo_single_stop_exports_and_bench_remains_idle(app):
    c=app.extensions['hantek_controller']
    c.connect({'demo':True,'mode':'scope'})
    assert c.state()['connected'] and not c.state()['running']
    c.run(single=True)
    wait_until(lambda:c.state()['status']=='single complete')
    assert c.last_frame['demo'] and c.last_frame['triggered']
    assert c.last_frame['channels'][0]['measurements']['frequency']==pytest.approx(1000,rel=.01)
    assert app.extensions['bench_controller'].rm is None
    response=app.test_client().get('/hantek/export.csv')
    data=response.get_data(as_text=True)
    assert '# Source: DEMO' in data and 'CH1_V,CH2_V' in data
    assert len([line for line in data.splitlines() if not line.startswith('#')])-1==c.last_frame['samples']
    assert app.test_client().get('/hantek/export.vcd').status_code==409
    c.disconnect()
    assert c.last_frame is None
    assert app.test_client().get('/hantek/export.csv').status_code==409


def test_logic_demo_and_vcd(app):
    c=app.extensions['hantek_controller'];c.connect({'demo':True,'mode':'logic'})
    c.run(single=True);wait_until(lambda:c.state()['status']=='single complete')
    assert len(c.last_frame['channels'])==8
    vcd=app.test_client().get('/hantek/export.vcd').get_data(as_text=True)
    assert '$timescale 1ps' in vcd and '$var wire 1 H D7' in vcd and 'DEMO' in vcd
    assert len(c.last_record['data'])==8


def test_normal_waits_and_single_does_not_stop_without_edge(app):
    c=app.extensions['hantek_controller'];p=plan(DEFAULTS)
    raw=[np.ones(p['count']),np.ones(p['count'])]
    s=settings(trigger={'mode':'normal'})
    frame,record=c._frame(raw,s,p,{'driver':'demo'})
    assert frame is None and record is None
    s['trigger']['mode']='single'
    assert c._frame(raw,s,p,{'driver':'demo'})==(None,None)
    s['trigger']['mode']='auto'
    frame,_=c._frame(raw,s,p,{'driver':'demo'})
    assert frame['triggered'] is False


def test_failure_clears_stale_frame_and_connected_state(app):
    c=app.extensions['hantek_controller'];c.connect({'demo':True,'mode':'scope'})
    c.run(single=True);wait_until(lambda:c.state()['status']=='single complete')
    class Failed(DemoBackend):
        def capture(self,*args):raise RuntimeError('USB unplugged')
    c.backend=Failed();c.run()
    wait_until(lambda:c.state()['status']=='error')
    assert not c.state()['connected'] and c.last_frame is None and c.last_record is None
    assert c.state()['error']=='USB unplugged'


def test_stop_cancels_blocked_capture_and_reconnect(app):
    c=app.extensions['hantek_controller'];c.connect({'demo':True,'mode':'scope'})
    entered=threading.Event()
    class Blocked(DemoBackend):
        def capture(self,device,settings,plan,stop):
            entered.set();stop.wait(2);raise CaptureCancelled()
    c.backend=Blocked();c.run();assert entered.wait(1)
    c.stop_capture();assert not c.state()['running'] and c.worker is None
    c.disconnect();c.connect({'demo':True,'mode':'logic'})
    assert c.state()['settings']['mode']=='logic'


def test_connected_mode_change_requires_disconnect(app):
    c=app.extensions['hantek_controller'];c.connect({'demo':True,'mode':'scope'})
    with pytest.raises(ValueError,match='Disconnect'):c.configure({'mode':'logic'})


def test_apps_have_independent_hantek_sessions():
    a=create_app({'TESTING':True});b=create_app({'TESTING':True})
    try:
        a.extensions['hantek_controller'].configure({'time_div':.002})
        assert b.extensions['hantek_controller'].settings['time_div']==.001
    finally:
        for app in (a,b):app.extensions['hantek_controller'].shutdown();app.extensions['bench_controller'].shutdown()


def test_autoset_preserves_dc_headroom(app):
    c=app.extensions['hantek_controller']
    channels=copy.deepcopy(DEFAULTS['channels'])
    channels[0]['vdiv']=1.
    c.configure({'channels':channels})
    c.last_record=dict(mode='scope', data=[np.ones(1000)*4,np.ones(1000)],rate=100000)
    c.autoset()
    assert c.settings['channels'][0]['vdiv']==1.
    assert c.settings['channels'][0]['offset']==-4.
