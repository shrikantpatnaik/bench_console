"""Validated display settings and bounded acquisition plans."""
import copy
import math

SCOPE_RATES = [100_000, 200_000, 500_000, 1_000_000, 4_000_000, 8_000_000,
               16_000_000, 24_000_000, 30_000_000, 48_000_000]
LOGIC_RATES = [20_000, 100_000, 200_000, 500_000, 1_000_000, 2_000_000,
               4_000_000, 8_000_000, 12_000_000, 16_000_000, 24_000_000]
VDIVS = [.02, .05, .1, .2, .5, 1, 2, 5]
TIMES = [m * 10 ** e for e in range(-8, 1) for m in (1, 2, 5)] + [1.0]
TIMES = sorted(set(t for t in TIMES if t <= 1))
MAX_SAMPLES = 2_097_152
CALIBRATION_FREQUENCIES = [0, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
DEFAULTS = {
    'mode': 'scope', 'time_div': .001, 'sample_rate': 'auto',
    'calibration_frequency': 1000,
    'channels': [dict(enabled=True, vdiv=.5, probe=1, offset=0., coupling='DC', invert=False),
                 dict(enabled=True, vdiv=.5, probe=1, offset=0., coupling='DC', invert=False)],
    'trigger': dict(mode='auto', source=0, edge='rising', level=0., pretrigger=.2,
                    hysteresis=.02, holdoff=0.),
    'acquisition': 'normal', 'average_count': 4,
    'logic_channels': [True] * 8,
    'logic_trigger': dict(source=0, edge='rising'),
    'math': 'off', 'math_vdiv': 1.,
}

def number(value, label, low, high):
    if isinstance(value, bool):
        raise ValueError(f'{label} must be a number')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be a number') from None
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f'{label} must be between {low:g} and {high:g}')
    return result

def choice(value, options, label):
    if value not in options:
        raise ValueError(f'Unsupported {label}')
    return value

def validate(data, current=None):
    if not isinstance(data, dict):
        raise ValueError('Settings must be an object')
    s = copy.deepcopy(current or DEFAULTS)
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise ValueError('Unknown settings: ' + ', '.join(sorted(unknown)))
    for key, value in data.items():
        if key in ('trigger', 'logic_trigger'):
            if not isinstance(value, dict) or set(value) - set(s[key]):
                raise ValueError(f'Invalid {key} settings')
            s[key].update(value)
        else:
            s[key] = copy.deepcopy(value)
    choice(s['mode'], ['scope', 'logic'], 'instrument mode')
    frequency = number(s['calibration_frequency'], 'Calibration frequency', 0, 20000)
    choice(frequency, CALIBRATION_FREQUENCIES, 'calibration frequency')
    s['calibration_frequency'] = int(frequency)
    s['time_div'] = number(s['time_div'], 'Time/div', min(TIMES), 1)
    if s['time_div'] not in TIMES:
        raise ValueError('Use a listed 1–2–5 time/div setting')
    rates = SCOPE_RATES if s['mode'] == 'scope' else LOGIC_RATES
    if s['sample_rate'] != 'auto':
        s['sample_rate'] = int(number(s['sample_rate'], 'Sample rate', min(rates), max(rates)))
        choice(s['sample_rate'], rates, 'sample rate')
    if not isinstance(s['channels'], list) or len(s['channels']) != 2:
        raise ValueError('Exactly two analog channels are required')
    for ch in s['channels']:
        if not isinstance(ch, dict) or set(ch) != set(DEFAULTS['channels'][0]):
            raise ValueError('Invalid channel settings')
        for key in ('enabled', 'invert'):
            if type(ch[key]) is not bool:
                raise ValueError(f'{key} must be true or false')
        ch['vdiv'] = number(ch['vdiv'], 'V/div', .02, 5)
        choice(ch['vdiv'], VDIVS, 'V/div')
        ch['probe'] = number(ch['probe'], 'Probe attenuation', 1, 1000)
        choice(ch['probe'], [1, 10, 100, 1000], 'probe attenuation')
        ch['offset'] = number(ch['offset'], 'Vertical position', -100000, 100000)
        choice(ch['coupling'], ['DC', 'AC', 'GND'], 'coupling')
    if s['mode'] == 'scope' and not any(ch['enabled'] for ch in s['channels']):
        raise ValueError('Enable at least one analog channel')
    t = s['trigger']
    choice(t['mode'], ['auto', 'normal', 'single'], 'trigger mode')
    choice(t['source'], [0, 1], 'trigger source')
    choice(t['edge'], ['rising', 'falling', 'either'], 'trigger edge')
    for key, lo, hi in [('level', -100000, 100000), ('pretrigger', .05, .9),
                        ('hysteresis', 0, 100000), ('holdoff', 0, 5)]:
        t[key] = number(t[key], key, lo, hi)
    choice(s['acquisition'], ['normal', 'average'], 'acquisition mode')
    choice(s['average_count'], [2, 4, 8, 16], 'average count')
    choice(s['math'], ['off', 'add', 'subtract', 'multiply', 'divide', 'fft', 'xy'], 'math mode')
    s['math_vdiv'] = number(s['math_vdiv'], 'Math units/div', .000001, 1000000)
    if not isinstance(s['logic_channels'], list) or len(s['logic_channels']) != 8 or any(type(v) is not bool for v in s['logic_channels']):
        raise ValueError('Exactly eight logic channel visibility flags are required')
    choice(s['logic_trigger']['source'], list(range(8)), 'logic trigger channel')
    choice(s['logic_trigger']['edge'], ['rising', 'falling', 'either'], 'logic trigger edge')
    plan(s)  # Reject a record that cannot cover the selected time window.
    return s

def plan(settings):
    duration = settings['time_div'] * 10
    rates = SCOPE_RATES if settings['mode'] == 'scope' else LOGIC_RATES
    rate = settings['sample_rate']
    if rate == 'auto':
        affordable = [r for r in rates if math.ceil(2 * duration * r) <= MAX_SAMPLES]
        if not affordable:
            raise ValueError('Time window exceeds the capture memory limit')
        target = [r for r in affordable if duration * r <= 131072]
        rate = max(target) if target else min(affordable)
    screen = max(2, math.ceil(duration * rate))
    count = max(1024, screen * 2)
    if count > MAX_SAMPLES:
        raise ValueError('Lower the sample rate or time/div: the record exceeds 2 Mi samples')
    # Supported front-end ranges from libsigrok: .1, .25, .5, 1 V/div,
    # each spanning ten divisions at the BNC connector. Display scaling is separate.
    gains = [next((v for v in [.1, .25, .5, 1.] if v >= ch['vdiv']), 1.)
             for ch in settings['channels']]
    return dict(rate=rate, screen=screen, count=count, gains=gains,
                duration=duration, undersampled=screen < 50)
