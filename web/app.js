const state = {
  targets: [],
  packages: [],
  selected: null,
  portInfo: null,
};

const els = {
  connectButton: document.querySelector("#connect-button"),
  refreshButton: document.querySelector("#refresh-button"),
  targetSelect: document.querySelector("#target-select"),
  installButton: document.querySelector("#install-button"),
  detailsButton: document.querySelector("#details-button"),
  details: document.querySelector("#details"),
  detailsOutput: document.querySelector("#details-output"),
  downloads: document.querySelector("#downloads"),
  downloadGrid: document.querySelector("#download-grid"),
  deviceStatus: document.querySelector("#device-status"),
  firmwareStatus: document.querySelector("#firmware-status"),
  flashStatus: document.querySelector("#flash-status"),
  supportStatus: document.querySelector("#support-status"),
  supportDot: document.querySelector("#support-dot"),
  summaryStatus: document.querySelector("#summary-status"),
  panels: {
    device: document.querySelector("#device-panel"),
    firmware: document.querySelector("#firmware-panel"),
    flash: document.querySelector("#flash-panel"),
  },
};

function setActive(step) {
  Object.entries(els.panels).forEach(([name, panel]) => {
    panel.classList.toggle("active", name === step);
  });
}

function setSupport() {
  const hasSerial = "serial" in navigator;
  els.supportDot.classList.toggle("ready", hasSerial);
  els.supportStatus.textContent = hasSerial
    ? "WebSerial ready"
    : "WebSerial is unavailable in this browser";
  if (!hasSerial) {
    els.deviceStatus.textContent =
      "Use Chrome or Edge over HTTPS or localhost. Safari and Firefox do not expose WebSerial.";
  }
  els.connectButton.disabled = !hasSerial;
}

function targetLabel(target) {
  return target.name;
}

function renderTargets() {
  els.targetSelect.innerHTML = "";
  state.targets.forEach((target) => {
    const option = document.createElement("option");
    option.value = target.id;
    option.textContent = targetLabel(target);
    els.targetSelect.append(option);
  });

  if (!state.selected && state.targets.length) {
    state.selected = state.targets[0];
  }
  if (state.selected) {
    els.targetSelect.value = state.selected.id;
    updateSelectedTarget(state.selected.id);
  }
}

function updateSelectedTarget(targetId) {
  const target = state.targets.find((item) => item.id === targetId);
  if (!target) return;
  state.selected = target;
  els.installButton.setAttribute("manifest", target.manifest);
  els.firmwareStatus.textContent = `${target.name} · ${target.chip_family}`;
  if (state.portInfo) {
    els.installButton.removeAttribute("disabled");
    els.flashStatus.textContent = "Ready to flash.";
    setActive("flash");
  } else {
    els.installButton.setAttribute("disabled", "");
    els.flashStatus.textContent = "Detect device first.";
    setActive("device");
  }
  els.summaryStatus.textContent = `Selected: ${target.name} (${target.chip_family})`;
  renderDetails();
}

function renderDetails() {
  if (!state.selected) return;
  const lines = [
    `target: ${state.selected.id}`,
    `name: ${state.selected.name}`,
    `chip: ${state.selected.chip}`,
    `chipFamily: ${state.selected.chip_family}`,
    `manifest: ${state.selected.manifest}`,
    "",
    "flash:",
    ...state.selected.flash.map(
      (entry) =>
        `  - ${entry.offset.padEnd(8)} ${entry.file}${entry.optional ? "  optional" : ""}`,
    ),
    "",
    "files:",
    ...state.selected.firmware_files.map((file) => `  - ${file.path} (${file.size} bytes)`),
  ];
  els.detailsOutput.textContent = lines.join("\n");
}

function scoreTargetForPort(target, info) {
  if (!info) return 0;
  const usbRules = target.usb || [];
  let score = 0;
  for (const rule of usbRules) {
    if (rule.usb_vendor_id && rule.usb_vendor_id === info.usbVendorId) score += 10;
    if (rule.usb_product_id && rule.usb_product_id === info.usbProductId) score += 20;
  }
  return score;
}

function detectTargetFromPortInfo(info) {
  const scored = state.targets
    .map((target) => ({ target, score: scoreTargetForPort(target, info) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);

  if (scored.length) return scored[0].target;
  return null;
}

async function connectAndDetect() {
  if (!("serial" in navigator)) return;
  try {
    const port = await navigator.serial.requestPort();
    state.portInfo = port.getInfo();
    const vendor = state.portInfo.usbVendorId
      ? `VID 0x${state.portInfo.usbVendorId.toString(16).padStart(4, "0")}`
      : "unknown VID";
    const product = state.portInfo.usbProductId
      ? `PID 0x${state.portInfo.usbProductId.toString(16).padStart(4, "0")}`
      : "unknown PID";
    els.deviceStatus.textContent = `${vendor}, ${product}`;

    const detected = detectTargetFromPortInfo(state.portInfo);
    if (detected) {
      els.firmwareStatus.textContent = `Matched: ${targetLabel(detected)}`;
      updateSelectedTarget(detected.id);
    } else {
      els.firmwareStatus.textContent =
        "Choose firmware manually.";
      els.installButton.removeAttribute("disabled");
      els.flashStatus.textContent = "Ready to flash.";
      setActive("firmware");
    }
  } catch (error) {
    els.deviceStatus.textContent =
      error?.name === "NotFoundError"
        ? "No port selected."
        : `Could not open the port chooser: ${error.message || error}`;
  }
}

function renderDownloads() {
  els.downloadGrid.innerHTML = "";
  els.downloads.hidden = !state.packages.length;
  for (const pkg of state.packages) {
    const card = document.createElement("article");
    card.className = "download-card";
    const title = document.createElement("strong");
    title.textContent = pkg.name;
    const description = document.createElement("p");
    const file = pkg.files?.[0];
    const size = file ? `${Math.round(file.size / 1024)} KB` : "";
    description.textContent = [pkg.family, size, pkg.notes].filter(Boolean).join(" · ");
    card.append(title, description);
    for (const item of pkg.files || []) {
      const link = document.createElement("a");
      link.href = item.path;
      link.download = item.file;
      link.textContent = item.label || item.file;
      card.append(link);
    }
    els.downloadGrid.append(card);
  }
}

async function loadTargets() {
  try {
    const response = await fetch("generated/targets.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.targets = data.targets || [];
    state.packages = data.packages || [];
    els.summaryStatus.textContent = `Loaded ${state.targets.length} firmware targets.`;
    renderTargets();
    renderDownloads();
  } catch (error) {
    els.summaryStatus.textContent =
      "Generated web assets are missing. Run: python3 ultimate.py export-web --sync";
    els.firmwareStatus.textContent = `Could not load generated/targets.json: ${
      error.message || error
    }`;
    els.installButton.setAttribute("disabled", "");
  }
}

els.connectButton.addEventListener("click", connectAndDetect);
els.refreshButton.addEventListener("click", () => window.location.reload());
els.targetSelect.addEventListener("change", (event) => updateSelectedTarget(event.target.value));
els.detailsButton.addEventListener("click", () => {
  els.details.hidden = !els.details.hidden;
  renderDetails();
});

setSupport();
loadTargets();
