"""Dashboard route and exports from full-resolution records."""
import json
from flask import Blueprint, Response, current_app, render_template
from .analysis import MEASUREMENTS
from .settings import DEFAULTS, VDIVS, TIMES, SCOPE_RATES, LOGIC_RATES, CALIBRATION_FREQUENCIES

hantek = Blueprint('hantek', __name__, url_prefix='/hantek')

LOGIC_MEASUREMENTS = [('frequency', 'Frequency', 'Hz'), ('period', 'Period', 's'),
    ('positive_width', 'Positive width', 's'), ('negative_width', 'Negative width', 's'),
    ('occupancy', 'High occupancy', '%'), ('edges', 'Edges', '')]

@hantek.get('')
@hantek.get('/')
def index():
    return render_template('hantek.html', config=dict(defaults=DEFAULTS, vdivs=VDIVS,
        times=TIMES, scope_rates=SCOPE_RATES, logic_rates=LOGIC_RATES,
        measurements=MEASUREMENTS, logic_measurements=LOGIC_MEASUREMENTS,
        calibration_frequencies=CALIBRATION_FREQUENCIES))

@hantek.get('/export.<kind>')
def export(kind):
    if kind not in ('csv', 'vcd'): return {'error': 'Choose CSV or VCD'}, 404
    try:
        record = current_app.extensions['hantek_controller'].export_snapshot()
    except ValueError as exc:
        return {'error': str(exc)}, 409
    if kind == 'vcd' and record['mode'] != 'logic':
        return {'error': 'VCD requires a digital capture'}, 409
    def stream():
        data, rate, pre = record['data'], record['rate'], record['pre']
        n = len(data[0])
        if kind == 'csv':
            yield '# Source: ' + ('DEMO' if record['demo'] else 'Hantek 6022BL') + '\n'
            yield '# Sample rate Hz: ' + str(rate) + '\n'
            if record['mode'] == 'scope':
                yield '# ADC startup samples discarded per channel: ' + str(record.get('settling_samples', 0)) + '\n'
                if any(record.get('timing_from_record', [])):
                    yield '# Timing measurements used the longer captured record; CSV contains the visible window\n'
            yield '# Settings: ' + json.dumps(record['settings']) + '\n'
            yield 'time_s,' + ','.join(f'CH{i+1}_V' if record['mode']=='scope' else f'D{i}' for i in range(len(data))) + '\n'
            for start in range(0, n, 1024):
                yield ''.join(f'{(i-pre)/rate:.12g},' + ','.join(f'{ch[i]:.10g}' for ch in data) + '\n'
                              for i in range(start, min(n,start+1024)))
        else:
            yield '$comment ' + ('DEMO ' if record['demo'] else '') + 'Hantek logic capture; t=0 is record start $end\n'
            yield '$timescale 1ps $end\n$scope module logic $end\n'
            for ch in range(8): yield f'$var wire 1 {chr(65+ch)} D{ch} $end\n'
            yield '$upscope $end\n$enddefinitions $end\n'
            previous = None
            for i in range(n):
                values = tuple(int(ch[i]) for ch in data)
                if values == previous: continue
                yield f'#{round(i*1e12/rate)}\n'
                yield ''.join(f'{value}{chr(65+ch)}\n' for ch,value in enumerate(values)
                              if previous is None or value != previous[ch])
                previous = values
    return Response(stream(), mimetype='text/csv' if kind=='csv' else 'text/plain',
                    headers={'Content-Disposition': f'attachment; filename="hantek-{record["mode"]}{"-demo" if record["demo"] else ""}.{kind}"'})
