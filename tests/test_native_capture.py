"""Exercise production packet handling without a USB device or disk I/O."""
from pathlib import Path
import json
import shlex
import shutil
import subprocess

import pytest
import numpy as np
import threading
from bench_console import create_app
from bench_console.hantek.backend import SigrokBackend
from bench_console.hantek.settings import validate, plan, SCOPE_SETTLING_SAMPLES


def test_native_record_buffering(tmp_path, monkeypatch):
    if not shutil.which('cc') or not shutil.which('pkg-config'):
        pytest.skip('Native compiler/pkg-config unavailable')
    flags = subprocess.run(['pkg-config', '--cflags', '--libs', 'libsigrok', 'libusb-1.0'],
                           text=True, capture_output=True)
    if flags.returncode:
        pytest.skip('libsigrok/libusb development files unavailable')
    source = Path(__file__).resolve().parents[1] / 'bench_console/hantek/native'
    harness = tmp_path / 'capture-test.c'
    harness.write_text(r'''
#include <assert.h>
#include <stdio.h>
static int in_callback = 0, stops = 0;
static size_t checked_write(const void *p, size_t size, size_t count, FILE *f) {
    assert(!in_callback); /* Writing here would block the USB event loop. */
    return fwrite(p, size, count, f);
}
#define fwrite checked_write
#define main bridge_main
#include "capture.c"
#undef main
#undef fwrite
int sr_session_stop(struct sr_session *session) {
    (void)session; stops++; return SR_OK;
}
static void feed(struct capture *cap, struct sr_datafeed_packet *packet) {
    in_callback = 1; datafeed(NULL, packet, cap); in_callback = 0;
}
int main(int argc, char **argv) {
    uint64_t rates[] = {100000,200000,500000,1000000,4000000,8000000};
    for (unsigned i=0; i<G_N_ELEMENTS(rates); i++) assert(scope_rate_supported(rates[i]));
    assert(!scope_rate_supported(123) && !scope_rate_supported(16000000));
    assert(!scope_rate_supported(24000000) && !scope_rate_supported(48000000));

    float ch1[6] = {0,0,0,0,0,999}, ch2[6] = {0,0,0,0,0,999};
    struct capture cap = {.limit=5, .analog={ch1,ch2}};
    struct sr_channel channels[2] = {{.name="CH1"},{.name="CH2"}};
    struct sr_analog_encoding encoding = {
        .unitsize=sizeof(float), .is_float=TRUE, .is_signed=TRUE,
        .scale={1,1}, .offset={0,1}
    };
    struct sr_analog_meaning meaning = {0};
    meaning.channels = g_slist_append(meaning.channels, &channels[0]);
    meaning.channels = g_slist_append(meaning.channels, &channels[1]);
    float first[] = {1,11,2,12,3,13};
    struct sr_datafeed_analog analog = {
        .data=first, .num_samples=3, .encoding=&encoding, .meaning=&meaning
    };
    struct sr_datafeed_packet packet = {.type=SR_DF_ANALOG, .payload=&analog};
    feed(&cap, &packet);
    assert(cap.counts[0]==3 && cap.counts[1]==3 && !cap.error && stops==0);
    float second[] = {4,14,5,15,6,16}; analog.data=second;
    feed(&cap, &packet); /* Last packet exceeds the remaining record length. */
    assert(cap.counts[0]==5 && cap.counts[1]==5 && stops==1);
    for (int i=0;i<5;i++) assert(ch1[i]==i+1 && ch2[i]==i+11);
    assert(ch1[5]==999 && ch2[5]==999);
    g_slist_free(meaning.channels);

    /* Hantek actually emits separate CH1/CH2 packets. Keep each channel's
     * chronological order even when delivery order differs. */
    cap.counts[0]=cap.counts[1]=0; stops=0;
    meaning.channels=g_slist_append(NULL, &channels[1]);
    float separate[]={21,22,23,24,25,26};
    analog.data=separate; analog.num_samples=6; feed(&cap, &packet);
    assert(cap.counts[1]==5 && cap.counts[0]==0 && stops==0);
    g_slist_free(meaning.channels);
    meaning.channels=g_slist_append(NULL, &channels[0]); feed(&cap, &packet);
    assert(cap.counts[0]==5 && stops==1 && ch1[4]==25 && ch2[4]==25);
    assert(ch1[5]==999 && ch2[5]==999); g_slist_free(meaning.channels);

    /* An interleaved prefix spanning packets must not consume record space
     * or stop before both channels finish their own settling phase. */
    cap.counts[0]=cap.counts[1]=0; cap.settling_samples=4; stops=0;
    meaning.channels=g_slist_append(NULL, &channels[0]);
    meaning.channels=g_slist_append(meaning.channels, &channels[1]);
    float settling1[]={-10,-20,-11,-21,-12,-22};
    analog.data=settling1; analog.num_samples=3; feed(&cap,&packet);
    assert(cap.discarded[0]==3 && cap.discarded[1]==3 && cap.counts[0]==0 && stops==0);
    float settling2[]={-13,-23,1,11,2,12}; analog.data=settling2; feed(&cap,&packet);
    assert(cap.discarded[0]==4 && cap.discarded[1]==4 && cap.counts[0]==2 && stops==0);
    float settling3[]={3,13,4,14,5,15}; analog.data=settling3; feed(&cap,&packet);
    assert(cap.counts[0]==5 && cap.counts[1]==5 && stops==1);
    for (int i=0;i<5;i++) assert(ch1[i]==i+1 && ch2[i]==i+11);
    assert(ch1[5]==999 && ch2[5]==999); g_slist_free(meaning.channels);

    /* Reproduce a 25 kHz / 8 MSa/s / 10 us-div capture with an unstable
     * startup followed by a clean periodic signal. Real packets for each
     * channel may have different boundaries; no global skip counter works. */
    struct capture bench={.limit=1600,.settling_samples=HANTEK_SETTLING_SAMPLES};
    uint64_t gross=bench.limit+bench.settling_samples;
    float *input[2]; stops=0;
    for (int ch=0;ch<2;ch++) {
        input[ch]=g_malloc_n(gross,sizeof(float));
        bench.analog[ch]=g_malloc_n(bench.limit+1,sizeof(float));
        bench.analog[ch][bench.limit]=999;
        for (uint64_t i=0;i<gross;i++) {
            if (i<bench.settling_samples) input[ch][i]=(i%37<11)?4:-1;
            else if (ch==0) input[ch][i]=((i-bench.settling_samples+180)%320<160)?3.3f:0;
            else input[ch][i]=.12f;
        }
    }
    input[1][bench.settling_samples+700]=.7f; /* Genuine later spike stays. */
    uint64_t received[2]={0,0};
    while (received[0]<gross || received[1]<gross) {
        for (int ch=0;ch<2;ch++) {
            if(received[ch]==gross) continue;
            uint64_t n=MIN(gross-received[ch],ch==0?1023:2049);
            meaning.channels=g_slist_append(NULL,&channels[ch]);
            analog.data=input[ch]+received[ch]; analog.num_samples=n;
            feed(&bench,&packet); received[ch]+=n; g_slist_free(meaning.channels);
        }
    }
    assert(stops==1 && !bench.error);
    for(int ch=0;ch<2;ch++) {
        assert(bench.counts[ch]==1600 && bench.discarded[ch]==HANTEK_SETTLING_SAMPLES);
        assert(bench.analog[ch][1600]==999);
        assert(!memcmp(bench.analog[ch],input[ch]+bench.settling_samples,1600*sizeof(float)));
        if(argc==2) {
            gchar *path=g_strdup_printf("%s/CH%d.f32",argv[1],ch+1);
            FILE *f=fopen(path,"wb"); assert(f);
            assert(fwrite(bench.analog[ch],sizeof(float),1600,f)==1600);
            assert(!fclose(f)); g_free(path);
        }
        g_free(input[ch]); g_free(bench.analog[ch]);
    }
    if(argc==2) printf("{\"rate\":8000000,\"settling_samples\":%u,\"discarded\":[%u,%u]}\n",
                       HANTEK_SETTLING_SAMPLES,HANTEK_SETTLING_SAMPLES,HANTEK_SETTLING_SAMPLES);

    uint8_t dest[6]={0,0,0,0,0,255};
    struct capture digital = {.limit=5, .logic=dest};
    uint8_t bytes[]={1,2,3};
    struct sr_datafeed_logic logic={.data=bytes,.length=3,.unitsize=1};
    packet.type=SR_DF_LOGIC; packet.payload=&logic; stops=0;
    feed(&digital,&packet);
    uint8_t wide[]={4,100,5,101,6,102};
    logic.data=wide; logic.length=6; logic.unitsize=2; feed(&digital,&packet);
    assert(digital.logic_count==5 && !digital.error && stops==1);
    for (int i=0;i<5;i++) assert(dest[i]==i+1);
    assert(dest[5]==255);
    logic.unitsize=0; feed(&digital,&packet);
    assert(digital.error && stops==2);
    return 0;
}
''')
    binary = tmp_path / 'capture-test'
    subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-I', str(source), str(harness), str(source / 'calibration.c'),
                    '-o', str(binary), *shlex.split(flags.stdout)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True)
    # Feed the production C callback output through the real Python reader,
    # trigger, measurements, plot envelope and CSV route.
    backend = SigrokBackend()
    def run(arguments, timeout, stop):
        folder = arguments[arguments.index('--output') + 1]
        reply = subprocess.run([str(binary), folder], check=True, capture_output=True, text=True)
        return json.loads(reply.stdout)
    monkeypatch.setattr(backend, '_run', run)
    settings = validate({'time_div': 1e-5, 'trigger': {'mode': 'normal', 'level': 1.65}})
    p = plan(settings)
    raw = backend.capture({'driver': 'hantek-6xxx', 'conn': 'test'}, settings, p, threading.Event())
    assert p['screen'] == 800 and p['count'] == 1600
    assert raw[1][700] == pytest.approx(.7)  # Discarding startup does not filter later glitches.
    app = create_app({'TESTING': True})
    controller = app.extensions['hantek_controller']
    try:
        frame, record = controller._frame(raw, settings, p, {'driver': 'hantek-6xxx'})
        assert frame['triggered'] and frame['settling_samples'] == SCOPE_SETTLING_SAMPLES
        m = frame['channels'][0]['measurements']
        assert m['frequency'] == pytest.approx(25_000)
        assert m['positive_width'] == pytest.approx(20e-6)
        assert m['positive_duty'] == pytest.approx(50)
        assert m['vpp'] == pytest.approx(3.3)
        assert frame['channels'][0]['timing_from_record']
        assert {round(v, 4) for _, v in frame['channels'][0]['points']} == {0., 3.3}
        controller.last_record = record
        csv = app.test_client().get('/hantek/export.csv').get_data(as_text=True)
        assert '# ADC startup samples discarded per channel: 4096' in csv
        assert '# Timing measurements used the longer captured record' in csv
        assert len([line for line in csv.splitlines() if not line.startswith('#')]) == 801
    finally:
        controller.shutdown(); app.extensions['bench_controller'].shutdown()
