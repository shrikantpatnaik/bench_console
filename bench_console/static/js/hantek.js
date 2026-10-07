/* Local canvas front end; hardware state is authoritative and shared. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const config = JSON.parse($('hantek-config').textContent);
  const socket = io('/hantek');
  let state = null, frame = null, pending = false, revision = -1, online = false;
  let history = [], drag = null;
  const colors = ['#ffd766', '#65c9ff', '#c696ff', '#50dca0', '#ff9a76', '#c2ed89', '#e59cce', '#a9bfff'];
  const canvas = $('waveform'), ctx = canvas.getContext('2d');
  const units = {vpp:'V', amplitude:'V', maximum:'V', minimum:'V', top:'V', middle:'V', base:'V', mean:'V', rms:'V', ac_rms:'V', frequency:'Hz', period:'s', rise_time:'s', fall_time:'s', positive_width:'s', negative_width:'s', positive_duty:'%', negative_duty:'%', overshoot:'%', preshoot:'%'};
  function format(value, unit='') {
    if (value === null || value === undefined || !Number.isFinite(value)) return '—';
    if (unit === '%') return value.toFixed(2) + ' %';
    const prefixes = [[1e9,'G'],[1e6,'M'],[1e3,'k'],[1,''],[1e-3,'m'],[1e-6,'µ'],[1e-9,'n'],[1e-12,'p']];
    const [scale,prefix] = value === 0 ? [1,''] : prefixes.find(([n]) => Math.abs(value)>=n) || [1e-12,'p'];
    return (value/scale).toPrecision(4).replace(/\.?0+$/, match => match.startsWith('.') ? '' : match) + ' ' + prefix + unit;
  }
  function options(id, values, label) {
    const node = $(id); node.replaceChildren();
    values.forEach(value => { const option=document.createElement('option'); option.value=value; option.textContent=label(value); node.append(option); });
  }
  options('time-div', config.times, value => format(value,'s')+'/div');
  options('calibration-frequency', config.calibration_frequencies, value => value===0 ? 'Off' : format(value,'Hz'));
  for (let i=0;i<2;i++) options(`ch${i}-vdiv`, config.vdivs, value => format(value,'V')+'/div');
  function showMode(mode) {
    const logic = mode === 'logic';
    document.querySelectorAll('.analog-only').forEach(node => {node.hidden=logic;});
    document.querySelectorAll('.logic-only').forEach(node => {node.hidden=!logic;});
    $('view-title').textContent = logic ? 'LOGIC ANALYZER · 8 CHANNELS' : 'OSCILLOSCOPE';
    $('vcd').hidden = !logic;
    options('sample-rate', ['auto', ...(logic ? config.logic_rates : config.scope_rates)], value => value==='auto' ? 'Auto' : format(value,'Sa/s'));
    options('trigger-source', Array.from({length:logic?8:2},(_,i)=>i), value => logic ? `D${value}` : `CH${value+1}`);
  }
  function loadSettings(s) {
    showMode(s.mode);
    $('device-mode').value=s.mode;
    $('time-div').value=s.time_div;
    $('sample-rate').value=s.sample_rate;
    $('calibration-frequency').value=s.calibration_frequency;
    for (let i=0;i<2;i++) {
      for (const [key,value] of Object.entries(s.channels[i])) {
        const node=$(`ch${i}-${key}`);
        if (node.type==='checkbox') node.checked=value; else node.value=value;
      }
      $('ch'+i+'-effective').textContent=format(s.channels[i].vdiv*s.channels[i].probe,'V')+'/div at probe tip';
    }
    const trigger = s.trigger;
    $('trigger-mode').value=trigger.mode;
    $('trigger-source').value=s.mode==='scope'?trigger.source:s.logic_trigger.source;
    $('trigger-edge').value=s.mode==='scope'?trigger.edge:s.logic_trigger.edge;
    $('trigger-level').value=trigger.level;
    $('trigger-pre').value=trigger.pretrigger*100;
    $('trigger-holdoff').value=trigger.holdoff;
    $('trigger-hysteresis').value=trigger.hysteresis;
    $('acquisition').value=s.acquisition; $('average-count').value=s.average_count;
    $('math').value=s.math; $('math-vdiv').value=s.math_vdiv;
    s.logic_channels.forEach((enabled,i) => {$(`d${i}-enabled`).checked=enabled;});
  }
  function readSettings() {
    const s=structuredClone(state?.settings || config.defaults);
    s.mode=$('device-mode').value; s.time_div=Number($('time-div').value);
    s.sample_rate=$('sample-rate').value==='auto'?'auto':Number($('sample-rate').value);
    s.calibration_frequency=Number($('calibration-frequency').value);
    for(let i=0;i<2;i++) for(const key of Object.keys(s.channels[i])) {
      const node=$(`ch${i}-${key}`);
      s.channels[i][key]=node.type==='checkbox'?node.checked:node.tagName==='SELECT'&&key==='coupling'?node.value:Number(node.value);
    }
    s.trigger.mode=$('trigger-mode').value;
    if(s.mode==='scope') {s.trigger.source=Number($('trigger-source').value); s.trigger.edge=$('trigger-edge').value;}
    else {s.logic_trigger.source=Number($('trigger-source').value); s.logic_trigger.edge=$('trigger-edge').value;}
    s.trigger.level=Number($('trigger-level').value); s.trigger.pretrigger=Number($('trigger-pre').value)/100;
    s.trigger.holdoff=Number($('trigger-holdoff').value); s.trigger.hysteresis=Number($('trigger-hysteresis').value);
    s.acquisition=$('acquisition').value; s.average_count=Number($('average-count').value);
    s.math=$('math').value; s.math_vdiv=Number($('math-vdiv').value);
    s.logic_channels=Array.from({length:8},(_,i)=>$(`d${i}-enabled`).checked);
    return s;
  }
  function command(name, data={}) {
    if(!online) return;
    pending=true; $('error').hidden=true; updateButtons();
    socket.emit('hantek_'+name, data);
  }
  function updateButtons() {
    const connected=online && state?.connected, running=connected && state.running;
    ['run','single','stop','autoset'].forEach(id => {$(id).disabled=!connected||pending;});
    $('stop').disabled=!running||pending; $('autoset').disabled=!connected||pending||state.settings.mode!=='scope'||!frame;
    ['scan','connect','demo'].forEach(id => {$(id).disabled=!online||connected||pending;});
    $('disconnect').disabled=!connected||pending;
    $('device-mode').disabled=!!connected||pending; $('device').disabled=!!connected||pending;
    document.querySelectorAll('.controls input,.controls select').forEach(node => {node.disabled=!online||pending;});
    $('calibration-frequency').disabled=!connected||pending||state?.settings.mode!=='scope';
    $('csv').disabled=!online||!frame||pending; $('vcd').disabled=!online||!frame||pending;
    $('png').disabled=!frame;
  }
  socket.on('connect', () => {online=true; pending=false; updateButtons();});
  socket.on('disconnect', () => {
    online=false; pending=false;
    $('status').textContent='App connection lost'; $('quality').textContent='Displayed waveform is retained data; reconnecting…';
    $('status-dot').style.background='#ff9a76'; updateButtons();
  });
  socket.on('state', next => {
    state=next; pending=next.status==='connecting';
    if(revision!==next.revision) {revision=next.revision;loadSettings(next.settings);history=[];}
    // Single mode is also changed by the Single button, without a settings edit.
    $('trigger-mode').value=next.settings.trigger.mode;
    $('status').textContent=next.status.toUpperCase();
    $('status-dot').style.background=next.running?'#50dca0':next.connected?'#65c9ff':'#93a6bd';
    $('source').textContent=next.device ? (next.device.driver==='demo'?'DEMO · generated signals':`${next.device.vendor} ${next.device.model} · USB ${next.device.conn}`) : 'No device connected';
    $('connect-note').hidden=next.connected;
    const calibration=next.calibration_frequency;
    $('calibration-status').textContent=!next.connected ? 'Connect in scope mode to apply' :
      next.settings.mode!=='scope' ? 'Available in scope mode' :
      calibration===null || calibration===undefined ? 'Output setting unverified — select a frequency to retry' :
      (next.device.driver==='demo' ? 'DEMO · simulated ' : 'USB setting verified · ') + (calibration===0 ? 'Off' : format(calibration,'Hz'));
    if(next.error){$('error').textContent=next.error;$('error').hidden=false;}
    if(next.frame_id===null) {frame=null;history=[];clearMeasurements();$('empty').hidden=false;$('capture-info').textContent='Waiting for capture';}
    if(next.status==='waiting' && frame) $('quality').textContent='Waiting for trigger · showing the last triggered record';
    updateButtons();draw();
  });
  socket.on('devices', devices => {
    pending=false; const select=$('device'); select.replaceChildren();
    devices.forEach(device => {const option=document.createElement('option');option.value=device.conn;option.textContent=`${device.vendor} ${device.model} · USB ${device.conn}`;select.append(option);});
    if(!devices.length){const option=document.createElement('option');option.value='';option.textContent='No matching devices found';select.append(option);}
    updateButtons();
  });
  socket.on('operation_error', message => {
    pending=false; $('error').textContent=message;$('error').hidden=false;
    if(state) loadSettings(state.settings); updateButtons();
  });
  function clearMeasurements() {
    document.querySelectorAll('.measurements td').forEach(node=>{node.textContent='—';});
    $('measurement-age').textContent='No record'; $('quality').textContent='';
  }
  function measurements() {
    if(!frame)return;
    if(frame.mode==='scope') for(let i=0;i<2;i++) for(const [key,value] of Object.entries(frame.channels[i].measurements)) $(`m${i}-${key}`).textContent=format(value,units[key]);
    else frame.channels.forEach((ch,i)=>{
      for(const key of ['frequency','period','positive_width','negative_width'])$(`d${i}-${key}`).textContent=format(ch.measurements[key],units[key]);
      $(`d${i}-occupancy`).textContent=format(ch.high_percent,'%');$(`d${i}-edges`).textContent=ch.edges.toLocaleString();
    });
    $('measurement-age').textContent=(frame.demo?'DEMO · ':'')+'record #'+frame.id;
  }
  socket.on('frame', next => {
    if ((state && next.revision < state.revision) || (frame && next.id <= frame.id)) return;
    frame=next;
    if($('persistence').checked && next.mode==='scope' && !['fft','xy'].includes(next.settings.math)) {history.push(next);if(history.length>12)history.shift();}
    else history=[];
    $('empty').hidden=true;
    $('capture-info').textContent=`${format(next.rate,'Sa/s')} · ${next.samples.toLocaleString()} samples · ${next.triggered?'TRIG':'AUTO / no edge'}${next.averages>1?' · avg '+next.averages:''}`;
    const warnings=[];
    if(next.demo) warnings.push('DEMO: generated signals');
    if(next.undersampled) warnings.push('Only '+next.samples+' samples across the screen; reduce timebase zoom for usable resolution');
    if(next.mode==='scope') {
      next.channels.forEach((ch,i)=>{if(ch.clipped)warnings.push(`CH${i+1} ADC clipping: increase range or probe attenuation`);});
      if(next.settings.channels.some(ch=>ch.coupling!=='DC'))warnings.push('AC/GND coupling is implemented in software');
      if(next.math?.invalid_samples) warnings.push('Division undefined at '+next.math.invalid_samples+' samples; invalid points drawn at zero');
      if(next.fft) warnings.push(`FFT: Hann window, AC component, ${format(next.fft.resolution,'Hz')} bin width`);
      $('scale-info').textContent=next.settings.channels.map((ch,i)=>`CH${i+1} ${format(ch.vdiv*ch.probe,'V')}/div · ×${ch.probe} · ADC ±${format(next.channels[i].front_end_vdiv*5,'V')} at BNC`).join('   |   ')+`   |   ${format(next.time_div,'s')}/div`;
    } else {
      if(next.channels.some(ch=>ch.truncated))warnings.push('Dense logic trace: drawing capped at 4,000 edges/channel; CSV/VCD retain every sample');
      $('scale-info').textContent=`D0–D7 · fixed logic threshold · ${format(next.time_div,'s')}/div`;
    }
    if(!next.triggered)warnings.push('Auto capture: time zero is the display reference, not a trigger event');
    $('quality').textContent=warnings.join(' · ');measurements();updateButtons();draw();
  });
  $('scan').onclick=()=>command('scan',{mode:$('device-mode').value});
  $('connect').onclick=()=>command('connect',{mode:$('device-mode').value,conn:$('device').value,demo:false});
  $('demo').onclick=()=>command('connect',{mode:$('device-mode').value,demo:true});
  for(const id of ['run','stop','single','autoset','disconnect'])$(id).onclick=()=>command(id);
  document.querySelectorAll('.controls input,.controls select').forEach(node=>{
    if(node.id!=='persistence')node.addEventListener('change',()=>command('settings',readSettings()));
  });
  $('device-mode').onchange=()=>{
    const mode=$('device-mode').value; const changed=structuredClone(state?.settings||config.defaults);changed.mode=mode;changed.sample_rate='auto';
    options('device',[''],()=> 'Scan for USB devices');command('settings',changed);
  };
  $('csv').onclick=()=>{window.location.href='/hantek/export.csv';};
  $('vcd').onclick=()=>{window.location.href='/hantek/export.vcd';};
  $('png').onclick=()=>{
    const image=document.createElement('canvas');image.width=canvas.width;image.height=canvas.height+50;
    const context=image.getContext('2d');context.fillStyle='#080e16';context.fillRect(0,0,image.width,image.height);context.drawImage(canvas,0,50);
    context.fillStyle='#e5edf7';context.font='14px system-ui';context.fillText((frame.demo?'DEMO · ':'')+'Hantek 6022BL · '+$('capture-info').textContent,12,30);
    const link=document.createElement('a');link.download=`hantek-${frame.mode}${frame.demo?'-demo':''}.png`;link.href=image.toDataURL('image/png');link.click();
  };
  $('clear-persistence').onclick=()=>{history=[];draw();};
  $('persistence').onchange=()=>{history=[];draw();};
  for(const id of ['cursors','t1','t2','v1','v2'])$(id).addEventListener('input',draw);
  function bounds() {return {left:52,top:22,width:canvas.clientWidth-68,height:canvas.clientHeight-52};}
  function draw() {
    const ratio=window.devicePixelRatio||1,w=canvas.clientWidth,h=canvas.clientHeight;
    if(!w||!h)return;
    if(canvas.width!==Math.round(w*ratio)||canvas.height!==Math.round(h*ratio)){canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);}
    ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,w,h);ctx.fillStyle='#080e16';ctx.fillRect(0,0,w,h);
    const b=bounds();ctx.strokeStyle='#243449';ctx.lineWidth=1;
    for(let i=0;i<=10;i++){let x=b.left+b.width*i/10;ctx.beginPath();ctx.moveTo(x,b.top);ctx.lineTo(x,b.top+b.height);ctx.stroke();}
    for(let i=0;i<=8;i++){let y=b.top+b.height*i/8;ctx.beginPath();ctx.moveTo(b.left,y);ctx.lineTo(b.left+b.width,y);ctx.stroke();}
    if(!frame)return;
    const s=frame.settings,mathMode=frame.mode==='scope'?s.math:'off',duration=frame.samples/frame.rate,pre=frame.pretrigger_samples/frame.rate;
    const x=t=>b.left+(t+pre)/duration*b.width;
    const y=(v,ch)=>b.top+b.height/2-(v+s.channels[ch].offset)/(s.channels[ch].vdiv*s.channels[ch].probe*8)*b.height;
    ctx.save();ctx.beginPath();ctx.rect(b.left,b.top,b.width,b.height);ctx.clip();
    function line(points,color,mapY,alpha=1,step=false) {
      ctx.strokeStyle=color;ctx.globalAlpha=alpha;ctx.lineWidth=1.5;ctx.beginPath();let last=null;
      points.forEach(([t,v],i)=>{let px=x(t),py=mapY(v);if(i===0)ctx.moveTo(px,py);else {if(step)ctx.lineTo(px,last);ctx.lineTo(px,py);}last=py;});ctx.stroke();ctx.globalAlpha=1;
    }
    if(frame.mode==='logic') frame.channels.forEach((ch,i)=>{
      if(!s.logic_channels[i])return;
      const row=b.height/8;line(ch.points,colors[i],v=>b.top+row*(i+.75-v*.5),1,true);
    });
    else if(mathMode==='fft' && frame.fft) {
      ctx.strokeStyle=colors[2];ctx.beginPath();frame.fft.points.forEach(([f,db],i)=>{const px=b.left+f/frame.fft.nyquist*b.width,py=b.top+(20-db)/140*b.height;if(!i)ctx.moveTo(px,py);else ctx.lineTo(px,py);});ctx.stroke();
    } else if(mathMode==='xy' && frame.xy) {
      ctx.strokeStyle=colors[2];ctx.beginPath();frame.xy.forEach(([a,v],i)=>{const px=b.left+b.width/2+(a+s.channels[0].offset)/(s.channels[0].vdiv*s.channels[0].probe*10)*b.width,py=y(v,1);if(!i)ctx.moveTo(px,py);else ctx.lineTo(px,py);});ctx.stroke();
    } else {
      history.slice(0,-1).forEach(old=>old.channels.forEach((ch,i)=>{if(s.channels[i].enabled)line(ch.points,colors[i],v=>y(v,i),.15);}));
      frame.channels.forEach((ch,i)=>{if(s.channels[i].enabled)line(ch.points,colors[i],v=>y(v,i));});
      if(frame.math)line(frame.math.points,colors[2],v=>b.top+b.height/2-v/(s.math_vdiv*8)*b.height);
      ctx.setLineDash([4,5]);ctx.strokeStyle='#50dca0';ctx.lineWidth=1;
      ctx.beginPath();ctx.moveTo(x(0),b.top);ctx.lineTo(x(0),b.top+b.height);ctx.stroke();
      const triggerY=y(frame.trigger_level,frame.trigger_source);ctx.beginPath();ctx.moveTo(b.left,triggerY);ctx.lineTo(b.left+b.width,triggerY);ctx.stroke();ctx.setLineDash([]);
    }
    const cursorOK=$('cursors').checked&&!['fft','xy'].includes(mathMode);
    if(cursorOK){
      ctx.strokeStyle='#cbd6e8';ctx.setLineDash([3,4]);
      for(const id of ['t1','t2']){const px=b.left+Number($(id).value)/100*b.width;ctx.beginPath();ctx.moveTo(px,b.top);ctx.lineTo(px,b.top+b.height);ctx.stroke();}
      if(frame.mode==='scope')for(const id of ['v1','v2']){const py=y(Number($(id).value),0);ctx.beginPath();ctx.moveTo(b.left,py);ctx.lineTo(b.left+b.width,py);ctx.stroke();}
      ctx.setLineDash([]);
      const dt=Math.abs(Number($('t2').value)-Number($('t1').value))/100*duration;
      $('cursor-readout').textContent=`Δt ${format(dt,'s')} · 1/Δt ${dt?format(1/dt,'Hz'):'—'}`+(frame.mode==='scope'?` · ΔV ${format(Math.abs(Number($('v2').value)-Number($('v1').value)),'V')}`:'');
    }else $('cursor-readout').textContent=['fft','xy'].includes(mathMode)?'Time/voltage cursors apply to time-domain views':'Enable cursors to measure Δt and ΔV';
    ctx.restore();ctx.fillStyle='#93a6bd';ctx.font='10px system-ui';
    if(frame.mode==='logic')for(let i=0;i<8;i++){ctx.fillStyle=colors[i];ctx.fillText('D'+i,12,b.top+b.height*(i+.5)/8);}
    else if(mathMode==='fft'){
      ctx.fillStyle='#93a6bd';ctx.fillText('20 dBV',2,b.top+4);ctx.fillText('−120',10,b.top+b.height);ctx.fillText('0 Hz',b.left,b.top+b.height+20);ctx.fillText(format(frame.fft.nyquist,'Hz'),b.left+b.width-65,b.top+b.height+20);return;
    }else {ctx.fillStyle=colors[0];ctx.fillText('CH1',8,b.top+12);ctx.fillStyle=colors[1];ctx.fillText('CH2',8,b.top+27);}
    ctx.fillStyle='#93a6bd';
    for(let i=0;i<=10;i+=2){const text=mathMode==='xy'?format((i-5)*s.channels[0].vdiv*s.channels[0].probe,'V'):format(i*duration/10-pre,'s');ctx.fillText(text,b.left+b.width*i/10-15,b.top+b.height+20);}
  }
  canvas.addEventListener('pointerdown',event=>{
    if(!$('cursors').checked||!frame||(frame.mode==='scope'&&['fft','xy'].includes(frame.settings.math)))return;
    const rect=canvas.getBoundingClientRect(),b=bounds(),px=event.clientX-rect.left;
    drag=Math.abs(px-(b.left+Number($('t1').value)/100*b.width))<Math.abs(px-(b.left+Number($('t2').value)/100*b.width))?'t1':'t2';
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove',event=>{if(!drag)return;const rect=canvas.getBoundingClientRect(),b=bounds();$(drag).value=Math.min(100,Math.max(0,(event.clientX-rect.left-b.left)/b.width*100));draw();});
  canvas.addEventListener('pointerup',()=>{drag=null;});canvas.addEventListener('pointercancel',()=>{drag=null;});
  new ResizeObserver(draw).observe(canvas);
  setInterval(()=>{if(frame&&online){const age=Math.max(0,(Date.now()/1000-frame.captured_at));$('measurement-age').textContent=(frame.demo?'DEMO · ':'')+`record #${frame.id} · ${age.toFixed(1)} s ago${state?.running?'':' · stopped'}`;}},500);
  loadSettings(config.defaults);updateButtons();draw();
})();
