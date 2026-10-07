const socket = io();
const $ = (id) => document.getElementById(id);
const connected = { dmm: false, psu: false };
const busy = { dmm: false, psu: false };
let serverConnected = false,
  latestPSU = null,
  editingSettings = false,
  editingProtection = false,
  pendingOutput = false,
  outputOffConfirmed = false,
  activeMode = "DC Voltage";
const config = JSON.parse($("console-config").textContent);
const modes = config.modes;
for (const key of ["dmm", "psu"]) {
  for (const baud of config.bauds)
    $("" + key + "-baud").add(new Option(baud, baud));
  $(key + "-baud").value = "115200";
}
for (const mode of modes) {
  const button = document.createElement("button");
  button.textContent = mode;
  button.dataset.mode = mode;
  button.addEventListener("click", () => socket.emit("change_mode", { mode }));
  $("modes-grid").appendChild(button);
}
function renderMode() {
  for (const button of $("modes-grid").children) {
    button.className = button.dataset.mode === activeMode ? "active" : "";
    button.disabled = !serverConnected || !connected.dmm;
  }
  $("continuity-panel").className =
    activeMode === "Continuity" ? "panel visible" : "panel";
}
function controls() {
  for (const key of ["dmm", "psu"]) {
    $(key + "-connection-note").hidden = connected[key] && serverConnected;
    $(key + "-connect").disabled = !serverConnected || busy[key];
    $(key + "-port").disabled = connected[key] || busy[key];
    $(key + "-baud").disabled = connected[key] || busy[key];
    $(key + "-connect").textContent = busy[key]
      ? "Please wait…"
      : connected[key]
        ? "Disconnect"
        : "Connect";
    $(key + "-connect").className = connected[key] ? "disconnect" : "";
  }
  for (const id of [
    "set-voltage",
    "set-current",
    "apply",
    "load-settings",
    "set-ovp",
    "set-ocp",
    "apply-protection",
    "load-protection",
  ])
    $(id).disabled = !serverConnected || !connected.psu || busy.psu;
  renderOutputToggle();
  renderMode();
}
function renderOutputToggle(confirmedOff = outputOffConfirmed) {
  const toggle = $("output-toggle"),
    known = latestPSU && connected.psu && serverConnected;
  const enabled = known && latestPSU.output;
  toggle.disabled = !known || busy.psu || pendingOutput;
  toggle.className =
    "output-toggle " +
    (known ? (enabled ? "live" : "") : confirmedOff ? "" : "unknown");
  toggle.setAttribute("aria-checked", enabled ? "true" : "false");
  toggle.setAttribute("aria-busy", pendingOutput ? "true" : "false");
  toggle.setAttribute(
    "aria-label",
    known
      ? "Power supply output " + (enabled ? "on; switch off" : "off; switch on")
      : "Power supply output; state " +
          (confirmedOff ? "off confirmed" : "unknown"),
  );
  $("output-toggle-state").textContent = pendingOutput
    ? "Switching… · last " + (enabled ? "ON" : "OFF")
    : known
      ? enabled
        ? "ON"
        : "OFF"
      : confirmedOff
        ? "OFF · confirmed"
        : "UNKNOWN";
}
function message(key, text, error = false) {
  $(key + "-status").textContent = text;
  $(key + "-status").className =
    "status " + (error ? "error" : connected[key] ? "success" : "");
}
function clearPSU(confirmedOff = false) {
  latestPSU = null;
  editingSettings = false;
  editingProtection = false;
  pendingOutput = false;
  outputOffConfirmed = confirmedOff;
  for (const id of ["psu-voltage", "psu-current", "psu-power"])
    $(id).textContent = "—";
  $("set-voltage").value = "";
  $("set-current").value = "";
  $("psu-readback").textContent = "Supply settings: —";
  $("set-ovp").value = "";
  $("set-ocp").value = "";
  $("psu-protection-readback").textContent = "Protection limits: —";
  renderOutputToggle(confirmedOff);
}
socket.on("connect", () => {
  serverConnected = true;
  $("server-status").textContent = "App connected";
  socket.emit("get_state");
  scanPorts();
  controls();
});
socket.on("disconnect", () => {
  serverConnected = false;
  connected.dmm = false;
  connected.psu = false;
  busy.dmm = false;
  busy.psu = false;
  $("server-status").textContent =
    "App connection lost. Power-supply output state is unknown; check its front panel.";
  $("readout").textContent = "---.---";
  $("unit-readout").textContent = "Connection lost";
  $("status-lamp").className = "lamp";
  clearPSU();
  message("dmm", "App connection lost", true);
  message("psu", "App connection lost; output state unknown", true);
  controls();
});
socket.on("connect_error", () => {
  $("server-status").textContent = "Cannot connect to the app; retrying…";
});
socket.on("ports_list", (ports) => {
  $("ports").replaceChildren(...ports.map((port) => new Option(port, port)));
  if (ports.length === 1 && !$("dmm-port").value)
    $("dmm-port").value = ports[0];
  if (!ports.length)
    $("server-status").textContent =
      "App connected · no ports found; enter a resource manually";
});
function connectionStatus(key, data) {
  const wasConnected = connected[key];
  connected[key] = data.connected;
  busy[key] = false;
  if (data.port) $(key + "-port").value = data.port;
  if (key === "dmm") {
    if (data.mode && data.mode !== activeMode) {
      $("readout").textContent = "---.---";
      $("unit-readout").textContent = "Settling…";
      $("status-lamp").className = "lamp";
    }
    if (data.mode) activeMode = data.mode;
    if (
      data.threshold !== undefined &&
      document.activeElement !== $("cont-limit")
    )
      $("cont-limit").value = data.threshold;
    if (!data.connected) {
      $("readout").textContent = "---.---";
      $("unit-readout").textContent = "Disconnected";
      $("status-lamp").className = "lamp";
    }
  } else if (!data.connected) clearPSU(data.output_off_confirmed === true);
  else if (!wasConnected) {
    editingSettings = false;
    editingProtection = false;
    outputOffConfirmed = false;
  }
  message(
    key,
    data.error ||
      (data.connected
        ? "Connected: " + data.idn
        : key === "psu" && data.output_off_confirmed
          ? "Disconnected · output OFF confirmed"
          : "Disconnected"),
    Boolean(data.error),
  );
  controls();
}
socket.on("connection_status", (data) => connectionStatus("dmm", data));
socket.on("psu_connection_status", (data) => connectionStatus("psu", data));
socket.on("dmm_poll_status", (data) => {
  if (!connected.dmm) return;
  if (data.waiting) {
    $("readout").textContent = "---.---";
    $("unit-readout").textContent = "Waiting for meter";
    $("status-lamp").className = "lamp";
    message("dmm", data.error, true);
  } else message("dmm", "Connected · readings resumed");
});
socket.on("telemetry", (data) => {
  if (!connected.dmm) return;
  $("readout").textContent = data.display;
  $("readout").style.color = data.is_ol ? "#ff8d82" : "#2ecc71";
  $("unit-readout").textContent = data.unit;
  $("status-lamp").className = data.lamp ? "lamp active" : "lamp";
});
socket.on("psu_telemetry", (data) => {
  if (!connected.psu) return;
  latestPSU = data;
  outputOffConfirmed = false;
  $("psu-voltage").textContent = data.voltage.toFixed(2);
  $("psu-current").textContent = data.current.toFixed(3);
  $("psu-power").textContent = data.power.toFixed(2);
  renderOutputToggle();
  $("psu-readback").textContent =
    `Supply settings: ${data.set_voltage.toFixed(2)} V · ${data.set_current.toFixed(3)} A`;
  $("psu-protection-readback").textContent =
    `Protection limits: OVP ${data.ovp.toFixed(2)} V · OCP ${data.ocp.toFixed(3)} A`;
  if (!editingSettings) loadSettings();
  if (!editingProtection) loadProtection();
});
socket.on("psu_settings_applied", (data) => {
  latestPSU = data;
  editingSettings = false;
  loadSettings();
  message("psu", "Settings applied and verified");
});
socket.on("psu_protection_applied", (data) => {
  latestPSU = data;
  editingProtection = false;
  loadProtection();
  message("psu", "Protection limits applied and verified");
});
socket.on("command_error", (data) => message(data.device, data.error, true));
for (const id of ["set-voltage", "set-current"])
  $(id).addEventListener("input", () => {
    editingSettings = true;
  });
for (const id of ["set-ovp", "set-ocp"])
  $(id).addEventListener("input", () => {
    editingProtection = true;
  });
function scanPorts() {
  if (serverConnected) socket.emit("get_ports");
}
function toggleConnect(key) {
  if (!serverConnected || busy[key]) return;
  if (!connected[key] && !$(key + "-port").value.trim()) {
    message(key, "Select or enter a port first", true);
    return;
  }
  busy[key] = true;
  controls();
  if (connected[key])
    socket.emit(key === "dmm" ? "disconnect_device" : "disconnect_psu");
  else
    socket.emit(key === "dmm" ? "connect_device" : "connect_psu", {
      port: $(key + "-port").value,
      baud: Number($(key + "-baud").value),
    });
}
function loadSettings() {
  if (!latestPSU) return;
  $("set-voltage").value = latestPSU.set_voltage.toFixed(2);
  $("set-current").value = latestPSU.set_current.toFixed(3);
  editingSettings = false;
}
function applySettings() {
  if (!$("set-voltage").reportValidity() || !$("set-current").reportValidity())
    return;
  if (!$("set-voltage").value || !$("set-current").value) {
    message("psu", "Enter voltage and current", true);
    return;
  }
  const voltage = Number($("set-voltage").value),
    current = Number($("set-current").value);
  if (
    !Number.isFinite(voltage) ||
    !Number.isFinite(current) ||
    voltage * current > 200 + 1e-9
  ) {
    message("psu", "Voltage × current must not exceed 200 W", true);
    return;
  }
  message("psu", "Applying settings…");
  socket.emit("set_psu", {
    voltage: $("set-voltage").value,
    current: $("set-current").value,
  });
}
function loadProtection() {
  if (!latestPSU) return;
  $("set-ovp").value = latestPSU.ovp.toFixed(2);
  $("set-ocp").value = latestPSU.ocp.toFixed(3);
  editingProtection = false;
}
function applyProtection() {
  if (!$("set-ovp").reportValidity() || !$("set-ocp").reportValidity()) return;
  if (!$("set-ovp").value || !$("set-ocp").value) {
    message("psu", "Enter OVP and OCP limits", true);
    return;
  }
  message("psu", "Applying protection limits…");
  socket.emit("set_psu_protection", {
    ovp: $("set-ovp").value,
    ocp: $("set-ocp").value,
  });
}
function toggleOutput() {
  if (
    !serverConnected ||
    !connected.psu ||
    !latestPSU ||
    busy.psu ||
    pendingOutput
  )
    return;
  const enabled = !latestPSU.output;
  if (enabled && (editingSettings || editingProtection)) {
    message(
      "psu",
      "Apply or load your settings and protection limits before enabling output",
      true,
    );
    return;
  }
  pendingOutput = true;
  renderOutputToggle();
  socket.timeout(25000).emit("set_psu_output", { enabled }, (error, result) => {
    pendingOutput = false;
    if (error) {
      // The command may have reached the supply. Stop displaying the previous
      // output state as current; the next telemetry update will confirm it.
      latestPSU = null;
      renderOutputToggle();
      message(
        "psu",
        "Output command confirmation timed out; waiting for actual state",
        true,
      );
      return;
    }
    renderOutputToggle();
    if (result && result.ok)
      message("psu", "Output " + (result.output ? "ON" : "OFF") + " confirmed");
  });
}
function updateThreshold() {
  if ($("cont-limit").reportValidity())
    socket.emit("change_threshold", { value: $("cont-limit").value });
}
for (const key of ["dmm", "psu"])
  $(key + "-connect").addEventListener("click", () => toggleConnect(key));
for (const button of document.querySelectorAll(".refresh-ports"))
  button.addEventListener("click", scanPorts);
for (const [id, handler] of Object.entries({
  "output-toggle": toggleOutput,
  apply: applySettings,
  "load-settings": loadSettings,
  "apply-protection": applyProtection,
  "load-protection": loadProtection,
}))
  $(id).addEventListener("click", handler);
$("cont-limit").addEventListener("change", updateThreshold);
controls();
