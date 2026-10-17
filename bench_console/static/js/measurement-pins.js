/* Browser-local display choices. Selecting a value never changes acquisition. */
window.BenchMeasurementPins = (() => {
  'use strict';
  const storageKey = 'bench-console:hantek:measurement-pins:v1';
  function create({config, format, colors}) {
    const $ = id => document.getElementById(id);
    const choices = Array.from(document.querySelectorAll('.measurement-pin'));
    const definitions = {
      scope: new Map(config.measurements.map(([key, label, unit]) => [key, {label, unit}])),
      logic: new Map(config.logic_measurements.map(([key, label, unit]) => [key, {label, unit}])),
    };
    const defaults = {scope: ['0:vpp', '1:vpp', '0:frequency', '1:frequency'], logic: ['0:frequency', '0:occupancy']};
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(storageKey)); } catch (_) { /* Storage may be disabled. */ }
    const selected = {};
    for (const mode of ['scope', 'logic']) {
      const allowed = new Set(choices.filter(node => node.dataset.mode === mode)
        .map(node => `${node.dataset.channel}:${node.dataset.metric}`));
      const values = Array.isArray(saved?.[mode]) ? saved[mode] : defaults[mode];
      selected[mode] = new Set(values.filter(value => typeof value === 'string' && allowed.has(value)));
    }
    let mode = config.defaults.mode, record = null, status = 'disconnected', online = false;
    let readouts = [];

    function rebuild() {
      const nodes = [];
      readouts = [];
      for (const checkbox of choices) {
        const key = `${checkbox.dataset.channel}:${checkbox.dataset.metric}`;
        checkbox.checked = selected[checkbox.dataset.mode].has(key);
        if (checkbox.dataset.mode !== mode || !checkbox.checked) continue;
        const channel = Number(checkbox.dataset.channel), metric = checkbox.dataset.metric;
        const definition = definitions[mode].get(metric);
        const card = document.createElement('div');
        card.className = 'measurement-readout';
        card.dataset.channel = channel; card.dataset.metric = metric;
        card.style.setProperty('--channel-color', colors[channel]);
        const label = document.createElement('span');
        label.className = 'measurement-readout-label';
        label.textContent = `${mode === 'scope' ? 'CH' + (channel + 1) : 'D' + channel} · ${definition.label}`;
        const value = document.createElement('output');
        value.className = 'measurement-readout-value';
        card.append(label, value); nodes.push(card);
        readouts.push({channel, metric, unit: definition.unit, node: value});
      }
      $('measurement-readouts').replaceChildren(...nodes);
      $('measurement-selection-empty').hidden = nodes.length > 0;
      refreshValues();
    }
    function refreshValues() {
      const current = record?.mode === mode ? record : null;
      for (const item of readouts) {
        const channel = current?.channels[item.channel];
        const value = mode === 'logic' && item.metric === 'occupancy' ? channel?.high_percent :
          mode === 'logic' && item.metric === 'edges' ? channel?.edges : channel?.measurements[item.metric];
        item.node.textContent = item.metric === 'edges' && Number.isFinite(value)
          ? value.toLocaleString() : format(value, item.unit);
      }
      refreshStatus();
    }
    function refreshStatus() {
      const current = record?.mode === mode ? record : null;
      if (!current) { $('selected-measurement-status').textContent = 'No record'; return; }
      const age = Math.max(0, Date.now() / 1000 - current.captured_at);
      const suffix = !online ? ' · app offline' : status === 'waiting' ? ' · waiting for trigger' :
        ['stopped', 'single complete'].includes(status) ? ' · stopped' : '';
      $('selected-measurement-status').textContent = (current.demo ? 'DEMO · ' : '') +
        `record #${current.id} · ${age.toFixed(1)} s ago${suffix}`;
    }
    for (const checkbox of choices) checkbox.addEventListener('change', () => {
      const key = `${checkbox.dataset.channel}:${checkbox.dataset.metric}`;
      if (checkbox.checked) selected[checkbox.dataset.mode].add(key);
      else selected[checkbox.dataset.mode].delete(key);
      try {
        localStorage.setItem(storageKey, JSON.stringify({scope: [...selected.scope], logic: [...selected.logic]}));
      } catch (_) { /* Changes still work for this page when storage is unavailable. */ }
      rebuild();
    });
    rebuild();
    return {
      setMode(next) { mode = next; rebuild(); },
      update(next) { record = next; refreshValues(); },
      setStatus(nextStatus, nextOnline) { status = nextStatus; online = nextOnline; refreshStatus(); },
      refreshStatus,
    };
  }
  return {create};
})();
