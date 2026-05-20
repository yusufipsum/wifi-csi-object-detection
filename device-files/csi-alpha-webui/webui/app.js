const state = {
  running: false,
  heat: [],
  motion: [],
  maxRows: 120,
  lastSampleAt: 0,
  frameCount: 0,
  smoothAmp: null,
  smoothMotion: 0,
  latestSample: null,
  latestAmpStats: null,
  latestHeatStats: null,
  drawPending: false,
};

const els = {
  runState: document.getElementById("runState"),
  packetRate: document.getElementById("packetRate"),
  frameCount: document.getElementById("frameCount"),
  classLabel: document.getElementById("classLabel"),
  classMeta: document.getElementById("classMeta"),
  rssi: document.getElementById("rssi"),
  avgAmp: document.getElementById("avgAmp"),
  peakAmp: document.getElementById("peakAmp"),
  motionScore: document.getElementById("motionScore"),
  sourceInfo: document.getElementById("sourceInfo"),
  seqInfo: document.getElementById("seqInfo"),
  ampMeta: document.getElementById("ampMeta"),
  ampScale: document.getElementById("ampScale"),
  heatWindow: document.getElementById("heatWindow"),
  heatMeta: document.getElementById("heatMeta"),
  heatScale: document.getElementById("heatScale"),
  motionMeta: document.getElementById("motionMeta"),
  capturePath: document.getElementById("capturePath"),
  logs: document.getElementById("logs"),
  channel: document.getElementById("channel"),
  sourceMac: document.getElementById("sourceMac"),
  distanceM: document.getElementById("distanceM"),
  labelName: document.getElementById("labelName"),
  note: document.getElementById("note"),
  mlAlert: document.getElementById("mlAlert"),
  mlAlertTitle: document.getElementById("mlAlertTitle"),
  mlAlertMeta: document.getElementById("mlAlertMeta"),
  datasetSamples: document.getElementById("datasetSamples"),
  datasetInfo: document.getElementById("datasetInfo"),
  datasetPath: document.getElementById("datasetPath"),
  refreshFiles: document.getElementById("refreshFiles"),
  filesMeta: document.getElementById("filesMeta"),
  captureFiles: document.getElementById("captureFiles"),
  datasetFiles: document.getElementById("datasetFiles"),
  startBtn: document.getElementById("startBtn"),
  stopBtn: document.getElementById("stopBtn"),
  ampCanvas: document.getElementById("ampCanvas"),
  heatCanvas: document.getElementById("heatCanvas"),
  motionCanvas: document.getElementById("motionCanvas"),
};

function fmt(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return Number(value).toFixed(digits);
}

function setBusy(isBusy) {
  els.startBtn.disabled = isBusy;
  els.stopBtn.disabled = isBusy;
}

async function postJSON(path, body = {}) {
  setBusy(true);
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await res.json();
    updateStatus(payload);
    return payload;
  } finally {
    setBusy(false);
  }
}

function formatBytes(bytes) {
  const units = ["B", "KB", "MB", "GB"];
  let value = Number(bytes || 0);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 10 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

function formatDate(ms) {
  if (!ms) return "--";
  return new Date(ms).toLocaleString("tr-TR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statsFor(values) {
  const nums = (values || []).map(Number).filter((value) => Number.isFinite(value));
  if (!nums.length) {
    return {
      count: 0,
      min: null,
      max: null,
      avg: null,
      p90: null,
    };
  }
  let min = Infinity;
  let max = -Infinity;
  let total = 0;
  for (const value of nums) {
    if (value < min) min = value;
    if (value > max) max = value;
    total += value;
  }
  const sorted = nums.slice().sort((a, b) => a - b);
  const p90Index = Math.min(sorted.length - 1, Math.floor(0.9 * (sorted.length - 1)));
  return {
    count: nums.length,
    min,
    max,
    avg: total / nums.length,
    p90: sorted[p90Index],
  };
}

function normalizeWithStats(values) {
  const logs = (values || []).map((value) => Math.log10(Math.max(1, Number(value) || 0)));
  if (!logs.length) {
    return {
      values: [],
      stats: { count: 0, logMin: null, logMax: null, logSpan: null },
    };
  }
  let min = Infinity;
  let max = -Infinity;
  for (const value of logs) {
    if (value < min) min = value;
    if (value > max) max = value;
  }
  const span = Math.max(0.001, max - min);
  return {
    values: logs.map((value) => (value - min) / span),
    stats: {
      count: logs.length,
      logMin: min,
      logMax: max,
      logSpan: span,
    },
  };
}

function updateChartMeta(sample) {
  const ampStats = state.latestAmpStats || statsFor(sample?.amps || []);
  const heatStats = state.latestHeatStats || { count: 0, logMin: null, logMax: null, logSpan: null };
  const visibleTones = ampStats.count || sample?.amps?.length || 0;
  const nfft = Number(sample?.nfft || visibleTones || 0);
  const omitted = visibleTones && nfft > visibleTones ? ` · ${nfft - visibleTones} tone özetlendi` : "";
  const seq = sample?.seq ?? "--";
  const chanspec = sample?.chanspec || "--";
  const sourceMac = sample?.sourceMac || "--";

  els.ampMeta.textContent =
    `Görünen tone/subcarrier bin: ${visibleTones || "--"} · Orijinal NFFT: ${nfft || "--"}${omitted} · ` +
    `min ${fmt(ampStats.min, 0)} / ort ${fmt(ampStats.avg, 0)} / p90 ${fmt(ampStats.p90, 0)} / peak ${fmt(ampStats.max, 0)}`;
  els.ampScale.textContent =
    `seq ${seq} · chanspec ${chanspec} · kaynak ${sourceMac} · x ekseni 0-${Math.max(0, visibleTones - 1)} tone, y ekseni log10 amplitüdün anlık min-max ölçeği`;

  const rows = state.heat.length;
  const cols = state.heat[0]?.length || visibleTones || 0;
  els.heatWindow.textContent = `son ${state.maxRows} kesit`;
  els.heatMeta.textContent =
    `Matris: ${rows}/${state.maxRows} zaman satırı × ${cols || "--"} tone sütunu · ` +
    `son satır log10 aralığı ${fmt(heatStats.logMin, 2)}-${fmt(heatStats.logMax, 2)} (span ${fmt(heatStats.logSpan, 2)})`;
  els.heatScale.textContent =
    `Renk ölçeği: koyu düşük, kırmızı yüksek enerji. Her satır kendi min/max değerine göre normalize edilir; bu yüzden desen dalgalanmasını vurgular.`;

  const ml = sample?.ml || {};
  const windowReady = ml.windowReady ?? state.motion.length;
  const windowSize = ml.window || "--";
  els.motionMeta.textContent =
    `Son ${state.motion.length} hareket ölçümü · anlık skor ${fmt(state.smoothMotion, 3)} · ML penceresi ${windowReady}/${windowSize}`;
}

function updateStatus(payload) {
  state.running = Boolean(payload.running);
  if (payload.frames !== undefined) state.frameCount = payload.frames || 0;
  els.runState.textContent = payload.error ? "Hata" : state.running ? "Canli" : "Bekliyor";
  els.runState.className = `pill ${payload.error ? "error" : state.running ? "live" : "idle"}`;
  els.packetRate.textContent = `${fmt(payload.packetRate, 0)} pkt/s`;
  els.frameCount.textContent = `${state.frameCount} frame`;
  els.motionScore.textContent = fmt(payload.motionScore, 3);
  els.capturePath.textContent = payload.capturePath ? payload.capturePath.split("/").pop() : "pcap --";
  els.datasetPath.textContent = payload.datasetPath ? `dataset ${payload.datasetPath}` : "dataset --";
  els.datasetSamples.textContent = payload.datasetSamples || 0;
  els.datasetInfo.textContent = payload.params?.label || "etiket yok";
  if (payload.logs) {
    els.logs.textContent = payload.logs.join("\n");
    if (els.logs.textContent) els.logs.scrollTop = els.logs.scrollHeight;
  }

  if (payload.latest) {
    const latest = payload.latest;
    els.rssi.textContent = latest.rssi ?? "--";
    els.avgAmp.textContent = fmt(latest.avgAmp, 0);
    els.peakAmp.textContent = `peak ${fmt(latest.peakAmp, 0)}`;
    els.seqInfo.textContent = `seq ${latest.seq ?? "--"} / ${latest.nfft ?? "--"} tone`;
    els.sourceInfo.textContent = latest.sourceMac || "--";
    updateClass(latest.classification);
    updateMl(latest.ml || payload.ml);
    if (!latest.amps && !state.latestSample) updateChartMeta(latest);
  } else if (payload.ml) {
    updateMl(payload.ml);
  }
}

function updateClass(info) {
  if (!info) return;
  els.classLabel.textContent = info.label || "Bekliyor";
  els.classMeta.textContent = `${info.model || "model"} ${fmt((info.confidence || 0) * 100, 0)}%`;
  els.classLabel.style.color =
    info.kind === "active" ? "#b33a3a" :
    info.kind === "motion" ? "#b66b00" :
    info.kind === "stable" ? "#187a55" : "#18201d";
}

function updateMl(info) {
  if (!info) return;
  if (!info.model) {
    els.mlAlert.className = "mlAlert idle";
    els.mlAlertTitle.textContent = "ML modeli yok";
    els.mlAlertMeta.textContent = "hand_motion modeli yuklenmedi";
    return;
  }
  const probability = Number(info.handMotionProbability || 0);
  const pct = `${fmt(probability * 100, 0)}%`;
  if (info.label === "ısınıyor") {
    els.mlAlert.className = "mlAlert idle";
    els.mlAlertTitle.textContent = "ML modeli hazırlanıyor";
    els.mlAlertMeta.textContent = `${info.windowReady || 0}/${info.window || "--"} pencere`;
    return;
  }
  if (info.active) {
    els.mlAlert.className = "mlAlert active";
    els.mlAlertTitle.textContent = "El hareketi algılandı";
    els.mlAlertMeta.textContent = `${info.model} · olasılık ${pct}`;
  } else {
    els.mlAlert.className = "mlAlert clear";
    els.mlAlertTitle.textContent = "El hareketi yok";
    els.mlAlertMeta.textContent = `${info.model} · hand_motion ${pct}`;
  }
}

function renderFileList(container, files) {
  container.replaceChildren();
  if (!files.length) {
    const empty = document.createElement("div");
    empty.className = "fileItem";
    empty.textContent = "Henüz kayıt yok";
    container.appendChild(empty);
    return;
  }
  for (const file of files) {
    const row = document.createElement("div");
    row.className = "fileItem";

    const info = document.createElement("div");
    const name = document.createElement("div");
    name.className = "fileName";
    name.textContent = file.name;
    const meta = document.createElement("div");
    meta.className = "fileMeta";
    meta.textContent = `${formatBytes(file.size)} · ${formatDate(file.modifiedAt)}`;
    info.appendChild(name);
    info.appendChild(meta);

    const link = document.createElement("a");
    link.className = "downloadLink";
    link.href = file.downloadUrl;
    link.download = file.name;
    link.textContent = "İndir";

    const remove = document.createElement("button");
    remove.className = "deleteButton";
    remove.type = "button";
    remove.textContent = "Sil";
    remove.addEventListener("click", () => {
      deleteFile(file);
    });

    const actions = document.createElement("div");
    actions.className = "fileActions";
    actions.appendChild(link);
    actions.appendChild(remove);

    row.appendChild(info);
    row.appendChild(actions);
    container.appendChild(row);
  }
}

async function deleteFile(file) {
  const ok = window.confirm(`${file.name} silinsin mi?`);
  if (!ok) return;
  const res = await fetch("/api/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: file.kind, name: file.name }),
  });
  if (!res.ok) {
    els.filesMeta.textContent = `${file.name} silinemedi`;
    return;
  }
  await loadFiles();
}

async function loadFiles() {
  try {
    const res = await fetch("/api/files", { cache: "no-store" });
    const payload = await res.json();
    const captures = payload.captures || [];
    const datasets = payload.datasets || [];
    renderFileList(els.captureFiles, captures);
    renderFileList(els.datasetFiles, datasets);
    const totalBytes = [...captures, ...datasets].reduce((sum, file) => sum + Number(file.size || 0), 0);
    els.filesMeta.textContent = `${captures.length} CSI kaydı, ${datasets.length} ML dataset · toplam ${formatBytes(totalBytes)}`;
  } catch (error) {
    els.filesMeta.textContent = `kayıtlar okunamadı: ${error.message}`;
  }
}

function normalize(values) {
  return normalizeWithStats(values).values;
}

function colorRamp(t) {
  const x = Math.max(0, Math.min(1, t));
  const stops = [
    [18, 22, 19],
    [25, 92, 157],
    [24, 122, 85],
    [225, 173, 65],
    [179, 58, 58],
  ];
  const scaled = x * (stops.length - 1);
  const idx = Math.min(stops.length - 2, Math.floor(scaled));
  const local = scaled - idx;
  const a = stops[idx];
  const b = stops[idx + 1];
  const r = Math.round(a[0] + (b[0] - a[0]) * local);
  const g = Math.round(a[1] + (b[1] - a[1]) * local);
  const bl = Math.round(a[2] + (b[2] - a[2]) * local);
  return `rgb(${r},${g},${bl})`;
}

function drawAmp(values) {
  const canvas = els.ampCanvas;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#101613";
  ctx.fillRect(0, 0, w, h);
  drawGrid(ctx, w, h);

  const norm = normalize(values);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#4ec18e";
  ctx.beginPath();
  norm.forEach((v, i) => {
    const x = 18 + (i / Math.max(1, norm.length - 1)) * (w - 36);
    const y = h - 22 - v * (h - 44);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.fillStyle = "#cfe0d6";
  ctx.font = "12px ui-monospace, monospace";
  ctx.fillText("tone 0", 18, h - 8);
  ctx.fillText(`tone ${Math.max(0, norm.length - 1)}`, w - 92, h - 8);
  ctx.fillText("log10 amplitude", 18, 18);
  ctx.fillText("yüksek", w - 62, 18);
  ctx.fillText("düşük", w - 54, h - 24);
}

function drawHeat() {
  const canvas = els.heatCanvas;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.fillStyle = "#101613";
  ctx.fillRect(0, 0, w, h);

  const rows = state.heat;
  if (!rows.length) {
    ctx.fillStyle = "#cfe0d6";
    ctx.font = "12px ui-monospace, monospace";
    ctx.fillText("heatmap bekleniyor", 14, 22);
    return;
  }
  const rowH = h / state.maxRows;
  const colW = w / rows[0].length;
  for (let r = 0; r < rows.length; r += 1) {
    const row = rows[rows.length - 1 - r];
    const y = h - (r + 1) * rowH;
    for (let c = 0; c < row.length; c += 1) {
      ctx.fillStyle = colorRamp(row[c]);
      ctx.fillRect(c * colW, y, Math.ceil(colW), Math.ceil(rowH) + 1);
    }
  }
  ctx.fillStyle = "rgba(207,224,214,0.92)";
  ctx.font = "12px ui-monospace, monospace";
  ctx.fillText(`zaman satırı ${rows.length}/${state.maxRows}`, 14, 22);
  ctx.fillText(`${rows[0].length} tone`, w - 82, h - 12);
}

function drawMotion() {
  const canvas = els.motionCanvas;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.fillStyle = "#101613";
  ctx.fillRect(0, 0, w, h);
  drawGrid(ctx, w, h);

  const values = state.motion;
  if (!values.length) {
    ctx.fillStyle = "#cfe0d6";
    ctx.font = "12px ui-monospace, monospace";
    ctx.fillText("hareket skoru bekleniyor", 14, 22);
    return;
  }
  ctx.strokeStyle = "#e1ad41";
  ctx.lineWidth = 2;
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = 16 + (i / Math.max(1, values.length - 1)) * (w - 32);
    const y = h - 18 - Math.max(0, Math.min(1, v)) * (h - 36);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = "#cfe0d6";
  ctx.font = "12px ui-monospace, monospace";
  ctx.fillText("0", 14, h - 8);
  ctx.fillText("1", 14, 18);
}

function drawGrid(ctx, w, h) {
  ctx.strokeStyle = "rgba(207,224,214,0.12)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 5; i += 1) {
    const y = (h / 5) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }
}

function smoothSeries(previous, next, alpha = 0.22) {
  if (!previous || previous.length !== next.length) return next.slice();
  return next.map((value, idx) => previous[idx] + (value - previous[idx]) * alpha);
}

function scheduleDraw() {
  if (state.drawPending) return;
  state.drawPending = true;
  requestAnimationFrame(() => {
    state.drawPending = false;
    if (!state.latestSample) return;
    drawAmp(state.smoothAmp || []);
    drawHeat();
    drawMotion();
  });
}

function handleSample(sample) {
  state.lastSampleAt = Date.now();
  state.latestSample = sample;
  state.frameCount += 1;
  state.smoothMotion = state.smoothMotion + ((sample.motionScore || 0) - state.smoothMotion) * 0.18;
  state.smoothAmp = smoothSeries(state.smoothAmp, sample.amps || []);
  state.latestAmpStats = statsFor(state.smoothAmp || []);
  const heatRow = normalizeWithStats(state.smoothAmp || []);
  state.latestHeatStats = heatRow.stats;
  state.heat.push(heatRow.values);
  while (state.heat.length > state.maxRows) state.heat.shift();
  state.motion.push(state.smoothMotion);
  while (state.motion.length > 180) state.motion.shift();
  updateStatus({
    running: true,
    packetRate: sample.packetRate,
    motionScore: state.smoothMotion,
    latest: sample,
  });
  updateClass(sample.classification);
  updateChartMeta(sample);
  scheduleDraw();
}

function connectEvents() {
  const events = new EventSource("/events");
  events.addEventListener("sample", (event) => {
    handleSample(JSON.parse(event.data));
  });
  events.addEventListener("status", (event) => {
    const payload = JSON.parse(event.data);
    updateStatus(payload);
  });
  events.onerror = () => {
    els.runState.textContent = "Baglanti";
    els.runState.className = "pill error";
  };
}

els.startBtn.addEventListener("click", () => {
  state.heat = [];
  state.motion = [];
  state.smoothAmp = null;
  state.smoothMotion = 0;
  state.latestAmpStats = null;
  state.latestHeatStats = null;
  state.frameCount = 0;
  updateChartMeta(null);
  postJSON("/api/start", {
    channel: els.channel.value.trim() || "48/80",
    sourceMac: els.sourceMac.value.trim() || "88:a2:9e:5d:4e:a6",
    distanceM: Number(els.distanceM.value || 2),
    label: els.labelName.value,
    note: els.note.value.trim(),
  });
});

els.stopBtn.addEventListener("click", () => {
  postJSON("/api/stop").then(loadFiles).catch(() => {});
});

els.refreshFiles.addEventListener("click", () => {
  loadFiles();
});

drawAmp([]);
drawHeat();
drawMotion();
updateChartMeta(null);
connectEvents();
fetch("/api/status").then((res) => res.json()).then(updateStatus).catch(() => {});
loadFiles();
