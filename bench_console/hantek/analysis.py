"""Voltage uses the visible record; timing can use the longer settled capture."""
import numpy as np

MEASUREMENTS = [
    ('vpp', 'Vpp', 'V'), ('amplitude', 'Amplitude', 'V'),
    ('maximum', 'Maximum', 'V'), ('minimum', 'Minimum', 'V'),
    ('top', 'Top', 'V'), ('middle', 'Middle', 'V'), ('base', 'Base', 'V'),
    ('mean', 'Mean', 'V'), ('rms', 'RMS', 'V'), ('ac_rms', 'AC RMS', 'V'),
    ('frequency', 'Frequency', 'Hz'), ('period', 'Period', 's'),
    ('rise_time', 'Rise 10–90%', 's'), ('fall_time', 'Fall 90–10%', 's'),
    ('positive_width', 'Positive width', 's'), ('negative_width', 'Negative width', 's'),
    ('positive_duty', 'Positive duty', '%'), ('negative_duty', 'Negative duty', '%'),
    ('overshoot', 'Overshoot', '%'), ('preshoot', 'Preshoot', '%'),
]
TIMING_MEASUREMENTS = ('frequency', 'period', 'rise_time', 'fall_time',
                       'positive_width', 'negative_width', 'positive_duty', 'negative_duty')

def scope_measurements(visible, captured, rate):
    """Keep voltage statistics on the view; use the longer record for timing
    only when the view has too few complete periods (e.g. 25 kHz at 10 us/div).
    Report that fallback explicitly so its provenance is visible in the UI.
    """
    result = measurements(visible, rate)
    fallback = False
    if result['frequency'] is None and len(captured) > len(visible):
        timing = measurements(captured, rate)
        if timing['frequency'] is not None:
            for key in TIMING_MEASUREMENTS:
                if result[key] is None:
                    result[key] = timing[key]
                    fallback = True
    return result, fallback

def crossings(y, level, rising=True):
    mask = ((y[:-1] < level) & (y[1:] >= level)) if rising else ((y[:-1] > level) & (y[1:] <= level))
    indices = np.flatnonzero(mask)
    if len(indices) == 0:
        return np.empty(0)
    return indices + (level - y[indices]) / (y[indices+1] - y[indices])

def measurements(samples, rate):
    y = np.asarray(samples, dtype=np.float64)
    result = {key: None for key, _, _ in MEASUREMENTS}
    if len(y) < 2 or not np.isfinite(y).all():
        return result
    lo, hi = float(y.min()), float(y.max())
    mean = float(y.mean())
    result.update(vpp=hi-lo, maximum=hi, minimum=lo, mean=mean,
                  rms=float(np.sqrt(np.mean(y*y))),
                  ac_rms=float(np.sqrt(np.mean((y-mean)**2))))
    # Histogram modes of lower/upper halves estimate stable levels of pulses;
    # for non-pulse waveforms these are descriptive estimates, not DC levels.
    if hi-lo < 1e-12:
        result.update(top=hi, base=lo, middle=mean, amplitude=0.)
        return result
    hist, edges = np.histogram(y, bins=64, range=(lo, hi))
    centers = (edges[:-1] + edges[1:]) / 2
    base = float(centers[np.argmax(hist[:32])])
    top = float(centers[32 + np.argmax(hist[32:])])
    # Include rail plateaus exactly when many samples are at an endpoint.
    if np.count_nonzero(y == lo) > len(y) / 20: base = lo
    if np.count_nonzero(y == hi) > len(y) / 20: top = hi
    amplitude = top-base
    middle = (base+top)/2
    result.update(base=base, top=top, middle=middle, amplitude=amplitude)
    if amplitude <= max(1e-12, (hi-lo)*.05):
        return result
    result['overshoot'] = max(0., (hi-top)/amplitude*100)
    result['preshoot'] = max(0., (base-lo)/amplitude*100)
    rising = crossings(y, middle, True)
    falling = crossings(y, middle, False)
    # Reject noise/chatter and measurements with fewer than two full periods.
    periods = np.diff(rising)
    if len(periods) < 2:
        return result
    p = float(np.median(periods))
    if p < 8 or np.median(np.abs(periods-p)) > p*.2:
        return result
    result.update(period=p/rate, frequency=rate/p)
    widths = []
    for a, b in zip(rising[:-1], rising[1:]):
        hits = falling[(falling > a) & (falling < b)]
        if len(hits) == 1: widths.append((hits[0]-a, b-hits[0], b-a))
    if widths:
        w = np.array(widths)
        result.update(positive_width=float(np.median(w[:,0]))/rate,
                      negative_width=float(np.median(w[:,1]))/rate,
                      positive_duty=float(np.median(w[:,0]/w[:,2]))*100,
                      negative_duty=float(np.median(w[:,1]/w[:,2]))*100)
    for direction, key in [(True, 'rise_time'), (False, 'fall_time')]:
        first_level = base + amplitude*(.1 if direction else .9)
        second_level = base + amplitude*(.9 if direction else .1)
        first = crossings(y, first_level, direction)
        second = crossings(y, second_level, direction)
        intervals = []
        for a in first:
            k = np.searchsorted(second, a)
            if k < len(second) and 0 < second[k]-a < p*.5:
                intervals.append(second[k]-a)
        if intervals:
            result[key] = float(np.median(intervals))/rate
    return result

def coupled(raw, channel):
    y = np.asarray(raw, dtype=np.float64) * channel['probe']
    if channel['coupling'] == 'AC': y = y - np.mean(y)
    elif channel['coupling'] == 'GND': y = np.zeros_like(y)
    if channel['invert']: y = -y
    return y

def trigger_index(y, trigger, screen, rate, digital=False):
    pre = int(screen*trigger['pretrigger'])
    last = len(y) - (screen-pre)
    candidates = []
    for rising in ([True, False] if trigger['edge'] == 'either' else [trigger['edge'] == 'rising']):
        level = .5 if digital else trigger['level']
        hyst = 0 if digital else trigger['hysteresis']
        hits = crossings(y, level, rising)
        armed = np.flatnonzero(y <= level-hyst) if rising else np.flatnonzero(y >= level+hyst)
        previous = -1
        for hit in hits:
            i = int(np.ceil(hit))
            k = np.searchsorted(armed, hit) - 1
            if k >= 0 and armed[k] > previous:
                if pre <= i <= last:
                    candidates.append(i)
                previous = i
    return min(candidates) if candidates else None

def envelope(y, rate, start, limit=1800):
    """Min/max buckets preserve narrow spikes and include their actual times."""
    if len(y) <= limit:
        indices = np.arange(len(y))
    else:
        edges = np.linspace(0, len(y), limit//2+1, dtype=int)
        selected = []
        for a,b in zip(edges[:-1], edges[1:]):
            block = y[a:b]
            selected.extend(sorted(set([a+int(np.argmin(block)), a+int(np.argmax(block))])))
        indices = np.array(selected)
    return [[float((i-start)/rate), float(y[i])] for i in indices]

def fft(samples, rate):
    y = np.asarray(samples, dtype=np.float64)
    # Use a contiguous window, never decimate a high-bandwidth signal into FFT.
    n = min(len(y), 16384)
    n = 2 ** int(np.log2(n)) if n >= 16 else 0
    if not n: return {'points': [], 'resolution': None, 'nyquist': rate/2}
    window = np.hanning(n)
    values = np.abs(np.fft.rfft((y[:n]-y[:n].mean()) * window)) * 2/window.sum()
    db = 20*np.log10(np.maximum(values, 1e-12))
    freqs = np.fft.rfftfreq(n, 1/rate)
    # Peak-preserving frequency bins for drawing.
    edges = np.linspace(0, len(db), min(1200, len(db))+1, dtype=int)
    indices = [a+int(np.argmax(db[a:b])) for a,b in zip(edges[:-1], edges[1:]) if b>a]
    return {'points': [[float(freqs[i]), float(db[i])] for i in indices],
            'resolution': rate/n, 'nyquist': rate/2}

def logic_trace(y, rate, pre, cap=4000):
    transitions = np.flatnonzero(np.diff(y) != 0) + 1
    indices = np.r_[0, transitions[:cap], len(y)-1]
    return {'points': [[float((i-pre)/rate), int(y[i])] for i in indices],
            'truncated': len(transitions) > cap, 'edges': len(transitions)}
