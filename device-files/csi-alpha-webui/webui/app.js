/* CSI Alpha paneli. Tum durum cihazda tutulur; bu sayfa yalnizca goruntuler ve komut gonderir. */
"use strict";

const C = window.CsiCharts;
const $ = (id) => document.getElementById(id);
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "--" : Number(v).toFixed(d));

const S = {
  status: null,
  sensing: null,     // /api/sensing (config, schema, scenarios, calibration, profiles)
  rows: [],          // canli karar gecmisi
  frames: { t: [], m: [], a: [] },
  alarms: [],
  marks: [],
  liveSpan: 300,
  paused: false,
  formDirty: false,
  analysis: null,
  analysisView: null,
  selectedRec: null,
  lastMessageTs: null,
};

const OVERALL = {
  normal: ["NORMAL", "İzleniyor. Eşik üstü kanıt K-of-N kararla doğrulanınca alarm üretilir."],
  alarm: ["ALARM", "Aktif alarm var; ayrıntı kartlarda ve alarm akışında."],
  calibrating: ["KALİBRASYON", ""],
  recording: ["PROFİL KAYDI", ""],
  uncalibrated: ["KALİBRE EDİLMEDİ", "Kurulum → Kalibrasyon: ortam normal ve hareketsizken çalıştırın."],
  no_signal: ["SİNYAL YOK", "Geçerli CSI karesi gelmiyor. Kurulum → Radyo kontrolü'ne bakın."],
  idle: ["BEKLİYOR", "Başlat'a basın. İlk kurulumda Kurulum sekmesindeki adımları izleyin."],
};

const ALARM_TEXT = {
  motion: "hareket", static: "statik değişim", static_change: "statik değişim", micro: "mikro-hareket",
  micro_motion: "mikro-hareket", rssi: "RSSI düşüşü", rssi_drop: "RSSI düşüşü", signal: "sinyal kaybı",
  signal_loss: "sinyal kaybı", passage: "geçiş (CNN)", hand_motion: "el hareketi (CNN)",
};
const alarmText = (l) => (l && l.startsWith("state_") ? `durum: ${l.slice(6)}` : ALARM_TEXT[l] || l || "alarm");

/* ------------------------------------------------------------------ API */
function authKey() {
  try { return localStorage.getItem("csiKey") || ""; } catch { return ""; }
}

async function api(path, body) {
  const opts = body === undefined ? { cache: "no-store" } : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSI-Key": authKey() },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  if (res.status === 401) {
    const key = window.prompt("Panel parolası:");
    if (key !== null) {
      try { localStorage.setItem("csiKey", key); } catch { /* yok */ }
      return api(path, body);
    }
    throw new Error("parola gerekli");
  }
  return res.json();
}

function toast(text, kind = "") {
  const el = $("toast");
  el.textContent = text;
  el.className = `toast show ${kind}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.className = "toast"; }, 4200);
}

async function run(fn, okText) {
  try {
    const out = await fn();
    if (out && out.ok === false) toast(out.error || "işlem başarısız", "bad");
    else if (okText) toast(okText, "good");
    return out;
  } catch (e) {
    toast(e.message, "bad");
    return null;
  }
}

/* ------------------------------------------------------------------ sekmeler / tema */
function showTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  try { localStorage.setItem("csiTab", name); } catch { /* yok */ }
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  if (name === "network") loadNetwork();
  if (name === "analysis") loadRecordings();
  if (name === "records") { loadFiles(); loadAlarms(); }
  if (name === "setup") loadSensing(true);
  requestAnimationFrame(redrawAll);
}
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
document.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.goto)));

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  requestAnimationFrame(redrawAll);
}
$("themeBtn").addEventListener("click", () => {
  const cur = document.documentElement.dataset.theme
    || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  try { localStorage.setItem("csiTheme", next); } catch { /* yok */ }
  applyTheme(next);
});

/* ------------------------------------------------------------------ canli grafikler */
const TRACKS = () => [
  { label: "Skorlar", weight: 1.4, min: 0, clampMax: 4, thrValue: 1,
    series: [{ key: "m", label: "hareket", color: "#e1ad41" }, { key: "s", label: "statik", color: "#3fbf8a" }, { key: "mi", label: "mikro", color: "#6aa6e8" }] },
  { label: "Hareket metriği (mod içi std)", weight: 1, min: 0, thrKey: "mt", series: [{ key: "mv", label: "değer", color: "#e1ad41" }] },
  { label: "Sızıntı iptalli T² (değişim)", weight: 1.1, min: 0, thrKey: "rt", series: [{ key: "rc", label: "T²", color: "#4fb3bf" }] },
  { label: "Kazanç durumu payı", weight: 1, min: 0, thrKey: "gt", series: [{ key: "g", label: "azınlık payı", color: "#e07a5f" }] },
  { label: "Statik z-mesafe", weight: 1, min: 0, thrKey: "st", series: [{ key: "sv", label: "mesafe", color: "#3fbf8a" }] },
  { label: "Kare hızı (/sn)", weight: 0.7, min: 0, series: [{ key: "r", label: "geçerli", color: "#a58cf0" }] },
  { label: "RSSI (dBm)", weight: 0.7, series: [{ key: "rssi", label: "rssi", color: "#d58fb8" }] },
];

const liveChart = new C.TimeChart($("liveChart"), { tracks: TRACKS(), tooltip: $("liveTip"), liveSpan: S.liveSpan, zoomable: false });
const anaChart = new C.TimeChart($("anaChart"), {
  tracks: TRACKS(), tooltip: $("anaTip"),
  onZoom: (view) => { S.analysisView = view; drawAnalysisSignals(); },
});

let liveDirty = false;
function drawLiveChart() {
  liveDirty = false;
  if (S.paused || !$("view-live").classList.contains("active")) return;
  liveChart.liveSpan = S.liveSpan;
  liveChart.setData({ rows: S.rows, alarms: S.alarms.map((a) => ({ t: a.ts / 1000, label: a.label })), marks: S.marks });
}
function scheduleLive() {
  if (liveDirty || S.paused) return;
  liveDirty = true;
  requestAnimationFrame(() => { if (liveDirty) drawLiveChart(); });
}
// rAF arka plan sekmelerinde durur: zamanlayici ile yedekle
setInterval(() => { if (liveDirty) drawLiveChart(); }, 600);

function drawLiveSignals() {
  if (S.paused || !$("view-live").classList.contains("active")) return;
  const res = C.liveAnalysis(S.frames, { windowS: 60 });
  if (!res) {
    C.drawMatrix($("liveSpec"), {});
    C.drawMatrix($("liveHeat"), {});
    C.drawLine($("livePc1"), {});
    $("specInfo").textContent = "veri bekleniyor";
    return;
  }
  const view = [res.grid[0], res.grid[res.grid.length - 1]];
  C.drawMatrix($("liveSpec"), { z: res.spec.db, t: res.spec.t, view, palette: "viridis", yLabels: ["0 Hz", `${fmt(res.spec.fMax, 0)} Hz`] });
  C.drawMatrix($("liveHeat"), { z: res.heat.z, t: res.heat.t, view, palette: "diverge", symmetric: true, yLabels: ["grup 1", `grup ${res.groups}`] });
  C.drawLine($("livePc1"), { t: res.grid, x: res.pc1, view, label: `PCA-1 · açıklanan varyans %${fmt(res.explained * 100, 0)}` });
  $("specInfo").textContent = `mod ${res.primary} · ${res.samples} kare · PC1 %${fmt(res.explained * 100, 0)}`;
}
setInterval(drawLiveSignals, 1000);

$("liveRange").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.id === "livePause") {
    S.paused = !S.paused;
    b.classList.toggle("on", S.paused);
    b.textContent = S.paused ? "▶" : "❚❚";
    if (!S.paused) scheduleLive();
    return;
  }
  S.liveSpan = Number(b.dataset.s);
  $("liveRange").querySelectorAll("button[data-s]").forEach((x) => x.classList.toggle("on", x === b));
  scheduleLive();
});

function redrawAll() {
  scheduleLive();
  drawLiveSignals();
  if (S.analysis) { anaChart.draw(); drawAnalysisSignals(); }
}
window.addEventListener("resize", () => requestAnimationFrame(redrawAll));

/* ------------------------------------------------------------------ durum */
function setMeter(card, score) {
  const bar = card.querySelector(".meter i");
  if (!bar) return;
  const v = Number.isFinite(score) ? Math.max(0, Math.min(2, score)) : 0;
  bar.style.width = `${(v / 2) * 100}%`;
  bar.className = score >= 1 ? "hot" : score >= 0.8 ? "warm" : "";
}

function setCard(card, { badge, cls, value, meta, score, active }) {
  card.querySelector(".badge").textContent = badge;
  card.querySelector(".badge").className = `badge ${cls || ""}`;
  card.querySelector(".detValue").textContent = value;
  card.querySelector(".detMeta").textContent = meta;
  card.classList.toggle("active", Boolean(active));
  if (score !== undefined) setMeter(card, score);
}

const pct = (s) => (Number.isFinite(s) ? `${fmt(s * 100, 0)}%` : "--");

function renderSensing(info, status) {
  const overall = info?.overall || (status?.running ? "no_signal" : "idle");
  const [title, hint] = OVERALL[overall] || [overall, ""];
  $("overallState").textContent = title;
  $("overallState").className = `overall ${overall}`;
  const chips = $("activeChips");
  chips.replaceChildren();
  for (const l of info?.activeAlarms || []) {
    const c = document.createElement("span");
    c.className = "chip";
    c.textContent = alarmText(l);
    chips.appendChild(c);
  }
  if (info && info.armed === false) {
    const c = document.createElement("span");
    c.className = "chip muted";
    c.textContent = "alarm devre dışı";
    chips.appendChild(c);
  }
  const ses = info?.session;
  const msg = status?.sensingMessage;
  if (ses) {
    const p = Math.round((ses.progress || 0) * 100);
    $("sensingMessage").textContent = ses.kind === "calibration"
      ? `Kalibrasyon %${p} · kalan ${fmt(ses.remainingS, 0)} sn — ortamı normal ve hareketsiz bırakın`
      : `Profil "${ses.label}" kaydı %${p} · kalan ${fmt(ses.remainingS, 0)} sn`;
    $(ses.kind === "calibration" ? "calProgress" : "profileProgress").style.width = `${p}%`;
  } else {
    const recent = msg && Date.now() - msg.ts < 45000;
    $("sensingMessage").textContent = recent ? msg.text : (status?.error || hint);
    $("calProgress").style.width = info?.calibrated ? "100%" : "0%";
    $("profileProgress").style.width = "0%";
  }
  if (msg && msg.ts !== S.lastMessageTs) {
    S.lastMessageTs = msg.ts;
    loadSensing();
  }
  const calOn = ses?.kind === "calibration";
  $("calPill").textContent = calOn ? "sürüyor" : info?.calibrated ? "hazır" : "yok";
  $("calPill").className = `pill ${calOn || info?.calibrated ? "good" : "idle"}`;

  const sig = info?.signal || {};
  const det = info?.detectors || {};
  $("kpiRate").textContent = Number.isFinite(sig.rate) ? `${fmt(sig.rate, 0)}/sn` : "--";
  $("kpiRateSub").textContent = `ham ${fmt(sig.rawRate, 0)}/sn · nfft ${sig.nfft || "--"}`;
  $("kpiRssi").textContent = Number.isFinite(sig.rssi) ? `${fmt(sig.rssi, 0)} dBm` : "--";
  $("kpiRssiSub").textContent = Number.isFinite(det.rssi?.baseline) ? `kalibrasyon ${fmt(det.rssi.baseline, 0)} dBm` : "kalibrasyon --";
  $("kpiModes").textContent = sig.modesInWindow ?? "--";
  $("kpiModesSub").textContent = `baskın %${Number.isFinite(sig.primaryShare) ? fmt(sig.primaryShare * 100, 0) : "--"} · banka ${(sig.modes || []).length}`;

  const m = det.motion || {};
  setCard($("detMotion"), {
    badge: !m.enabled ? "kapalı" : m.active ? "ALARM" : Number.isFinite(m.score) ? "normal" : "bekliyor",
    cls: m.active ? "hot" : m.enabled ? "" : "off", value: pct(m.score), score: m.score, active: m.active,
    meta: m.thr ? `değer ${fmt(m.value, 4)} / eşik ${fmt(m.thr, 4)} · yenilik %${fmt((m.novelty || 0) * 100, 0)}${Number.isFinite(m.residual) ? ` · T² ${fmt(m.residual, 2)}${m.residualThr ? `/${fmt(m.residualThr, 2)}` : ""}` : ""}${Number.isFinite(m.gain) ? ` · kazanç %${fmt(m.gain * 100, 0)}${m.gainThr ? `/${fmt(m.gainThr * 100, 0)}` : ""}` : ""}` : `değer ${fmt(m.value, 4)} · kalibrasyon gerekli`,
  });
  const st = det.static || {};
  setCard($("detStatic"), {
    badge: !st.enabled ? "kapalı" : st.active ? "ALARM" : st.evaluated ? "normal" : "bekliyor",
    cls: st.active ? "hot" : st.enabled ? "" : "off", value: pct(st.score), score: st.score, active: st.active,
    meta: st.thr ? `z ${fmt(st.value, 2)} / eşik ${fmt(st.thr, 2)} · ${st.evaluated ? `sessiz ${fmt(st.quietS, 0)} sn` : "hareket bitince"} · uyarlanan ${fmt(st.adaptedS, 0)} sn` : "kalibrasyon gerekli",
  });
  const sp = det.state || {};
  const profs = sp.profiles || [];
  setCard($("detState"), {
    badge: !sp.enabled ? "kapalı" : sp.active ? "ALARM" : profs.length ? "aktif" : "profil yok",
    cls: sp.active ? "hot" : sp.enabled ? "" : "off", active: sp.active,
    value: sp.stable ? (sp.stable === "unknown" ? "bilinmeyen" : sp.stable) : "--",
    meta: profs.length ? `anlık: ${sp.label === "unknown" ? "bilinmeyen" : sp.label === "uncertain" ? "belirsiz" : sp.label || "--"} · alarm: ${(sp.alarmLabels || []).join(", ") || "yok"}` : "Kurulum → Durum profilleri",
  });
  const box = $("detState").querySelector(".stateDistances");
  box.replaceChildren();
  for (const [l, d] of Object.entries(sp.distances || {}).sort((a, b) => a[1] - b[1])) {
    const s = document.createElement("span");
    s.textContent = `${l} ${fmt(d, 1)}`;
    if (l === sp.stable) s.className = "best";
    box.appendChild(s);
  }
  const mi = det.micro || {};
  setCard($("detMicro"), {
    badge: !mi.enabled ? "kapalı" : mi.active ? "ALARM" : Number.isFinite(mi.score) ? "normal" : "bekliyor",
    cls: mi.active ? "hot" : mi.enabled ? "" : "off", value: mi.enabled ? pct(mi.score) : "--", score: mi.score, active: mi.active,
    meta: mi.enabled ? `tepe/medyan ${fmt(mi.value, 1)}${mi.thr ? ` / eşik ${fmt(mi.thr, 1)}` : ""} · uzun sessizlik gerekir` : "deneysel · Ayarlar'dan açılır",
  });
  const sg = det.signal || {};
  const rs = det.rssi || {};
  const minRate = S.sensing?.config?.minPacketRate || 5;
  setCard($("detSignal"), {
    badge: sg.active ? "KAYIP" : rs.active ? "RSSI" : Number.isFinite(sg.rate) ? "iyi" : "--",
    cls: sg.active || rs.active ? "hot" : "", active: sg.active || rs.active,
    value: Number.isFinite(sg.rate) ? `${fmt(sg.rate, 0)}/sn` : "--",
    score: Number.isFinite(sg.rate) ? Math.min(2, sg.rate / (minRate * 4)) : undefined,
    meta: `asgari ${fmt(minRate, 0)}/sn${Number.isFinite(rs.dropDb) ? ` · RSSI Δ ${fmt(rs.dropDb, 1)} dB` : ""}${rs.enabled ? "" : " · RSSI alarmı kapalı"}`,
  });
}

function renderStatus(st) {
  S.status = st;
  const running = Boolean(st.running);
  $("runPill").textContent = st.error ? "Hata" : running ? "Canlı" : "Bekliyor";
  $("runPill").className = `pill ${st.error ? "bad" : running ? "good" : "idle"}`;
  const rate = st.sensing?.signal?.rate ?? st.packetRate;
  $("ratePill").textContent = `${fmt(rate, 0)} kare/sn`;
  if (running && st.params?.mode) $("captureMode").value = st.params.mode;
  $("kpiChannel").textContent = running ? (st.params?.channel || "--") : "--";
  $("kpiChannelSub").textContent = st.tx ? `${st.tx.backend} · ${st.tx.iface} · ${fmt(st.tx.ratePps, 0)} pps` : "--";
  renderSensing(st.sensing, st);
  if (st.mode === "motion" || st.params?.mode === "motion") renderMl(st.ml);
  if (st.logs) $("logs").textContent = st.logs.join("\n");
  if (st.reflection) renderReflection(st.reflection);
  if (st.alarms && !S.alarms.length) setAlarms(st.alarms);
  updateArm();
}

function renderMl(ml) {
  if (!ml) return;
  $("mlBox").textContent = ml.model
    ? `CNN/LSTM ${ml.model}: ${ml.label} · güven %${fmt((ml.confidence || 0) * 100, 0)}${ml.alarmSuppressed ? ` · filtre ${ml.alarmSuppressed}` : ""}`
    : `CNN/LSTM: ${ml.error || "model yok"}`;
}

/* ------------------------------------------------------------------ SSE */
function connect() {
  const es = new EventSource("/events");
  es.addEventListener("status", (e) => renderStatus(JSON.parse(e.data)));
  es.addEventListener("row", (e) => {
    const row = JSON.parse(e.data);
    if (!S.rows.length || S.rows[S.rows.length - 1].t < row.t) S.rows.push(row);
    const cut = row.t - 900;
    while (S.rows.length && S.rows[0].t < cut) S.rows.shift();
    scheduleLive();
  });
  es.addEventListener("frames", (e) => {
    const f = JSON.parse(e.data);
    for (let i = 0; i < f.t.length; i += 1) {
      S.frames.t.push(f.t[i]);
      S.frames.m.push(f.m[i]);
      S.frames.a.push(f.a[i]);
    }
    const cut = f.t[f.t.length - 1] - 65;
    let k = 0;
    while (k < S.frames.t.length && S.frames.t[k] < cut) k += 1;
    if (k) { S.frames.t.splice(0, k); S.frames.m.splice(0, k); S.frames.a.splice(0, k); }
  });
  es.addEventListener("sample", (e) => {
    const s = JSON.parse(e.data);
    $("ratePill").textContent = `${fmt(s.sensing ? S.status?.sensing?.signal?.rate ?? s.packetRate : s.packetRate, 0)} kare/sn`;
    if (s.ml) renderMl(s.ml);
  });
  es.addEventListener("alarm", (e) => {
    const a = JSON.parse(e.data);
    setAlarms([a, ...S.alarms.filter((x) => x.id !== a.id)]);
    toast(`ALARM: ${a.text || alarmText(a.label)}`, "bad");
  });
  es.onerror = () => {
    $("runPill").textContent = "Bağlantı";
    $("runPill").className = "pill bad";
  };
}

async function loadHistory() {
  try {
    const h = await api("/api/history?seconds=900");
    S.rows = h.rows || [];
    S.frames = h.frames || { t: [], m: [], a: [] };
    scheduleLive();
  } catch { /* yok */ }
}

/* ------------------------------------------------------------------ calisma / kurulu */
$("startBtn").addEventListener("click", async () => {
  const mode = $("captureMode").value;
  S.rows = [];
  S.frames = { t: [], m: [], a: [] };
  S.marks = [];
  const body = { mode, label: $("labelName").value, note: $("note").value };
  if (mode === "motion") {
    body.channel = $("legacyChannel").value || "48/80";
    body.sourceMac = $("sourceMac").value;
    body.distanceM = Number($("distanceM").value || 2);
  } else {
    body.channel = "auto";
  }
  const st = await run(() => api("/api/start", body));
  if (st) renderStatus(st);
});
$("stopBtn").addEventListener("click", async () => {
  const st = await run(() => api("/api/stop", {}));
  if (st) renderStatus(st);
});
function updateArm() {
  const armed = S.sensing?.config?.armed !== false;
  $("armBtn").textContent = armed ? "Kurulu" : "Devre dışı";
  $("armBtn").classList.toggle("on", armed);
}
$("armBtn").addEventListener("click", async () => {
  const armed = S.sensing?.config?.armed === false;
  await run(() => api("/api/sensing/command", { command: "arm", armed }), armed ? "Alarm kuruldu; statik referans tazelendi" : "Alarm devre dışı");
  loadSensing();
});

/* ------------------------------------------------------------------ olay isaretleme */
document.querySelectorAll("[data-mark]").forEach((b) => b.addEventListener("click", async () => {
  const out = await run(() => api("/api/sensing/command", { command: "mark", kind: b.dataset.mark, label: $("markLabel").value || "olay" }));
  if (out?.ok && out.mark) {
    S.marks.push({ t: out.mark.ts / 1000, kind: out.mark.kind, label: out.mark.label });
    const li = document.createElement("li");
    li.textContent = `${new Date(out.mark.ts).toLocaleTimeString("tr-TR")} · ${out.mark.kind} · ${out.mark.label}`;
    $("markList").prepend(li);
    scheduleLive();
  }
}));

/* ------------------------------------------------------------------ alarmlar */
function setAlarms(list) {
  S.alarms = list || [];
  renderAlarmFeed($("recentAlarms"), S.alarms.slice(0, 8), false);
  renderAlarmFeed($("alarmList"), S.alarms, true);
  scheduleLive();
}

function renderAlarmFeed(el, list, withDelete) {
  el.replaceChildren();
  if (!list.length) {
    el.innerHTML = '<p class="muted small">Alarm yok</p>';
    return;
  }
  for (const a of list) {
    const row = document.createElement("div");
    row.className = "alarmRow";
    const badge = document.createElement("span");
    badge.className = "chip";
    badge.textContent = alarmText(a.label);
    const info = document.createElement("div");
    info.innerHTML = `<b>${new Date(a.ts).toLocaleString("tr-TR")}</b><small>${a.text || ""} · skor ${fmt(a.score, 2)} · ${a.scenario || a.model || ""}</small>`;
    row.append(badge, info);
    if (withDelete) {
      const del = document.createElement("button");
      del.className = "btn small";
      del.textContent = "Sil";
      del.addEventListener("click", async () => {
        const out = await run(() => api("/api/delete-alarm", { id: a.id }));
        if (out) setAlarms(out.alarms || []);
      });
      row.appendChild(del);
    }
    el.appendChild(row);
  }
}

async function loadAlarms() {
  const out = await run(() => api("/api/alarms"));
  if (out) setAlarms(out.alarms || []);
}
$("alarmRefresh").addEventListener("click", loadAlarms);
$("alarmClear").addEventListener("click", async () => {
  if (!window.confirm("Tüm alarm kayıtları silinsin mi?")) return;
  const out = await run(() => api("/api/delete-alarm", { all: true }));
  if (out) setAlarms([]);
});

/* ------------------------------------------------------------------ kurulum: radyo, kalibrasyon, profiller */
function kv(dl, pairs) {
  dl.replaceChildren();
  for (const [k, v] of pairs) {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    dl.append(dt, dd);
  }
}

function notes(ul, items, cls = "") {
  ul.replaceChildren();
  for (const t of items) {
    const li = document.createElement("li");
    li.textContent = t;
    if (cls) li.className = cls;
    ul.appendChild(li);
  }
}

async function loadSensing(withRadio = false) {
  try {
    const meta = await api(`/api/sensing${withRadio ? "?radio=1" : ""}`);
    S.sensing = meta;
    renderScenario(meta);
    if (!S.formDirty) renderSettings(meta);
    // Kalibrasyon suresi kutusu, kullanici elle degistirmedikce ayardaki calibrationS'i gosterir
    if (!S.calDurTouched && meta.config?.calibrationS) $("calDuration").value = meta.config.calibrationS;
    renderCalibration(meta.calibration, meta.status);
    renderProfiles(meta.profiles || {});
    if (meta.radio) renderRadio(meta.radio);
    updateArm();
  } catch (e) {
    $("settingsMeta").textContent = `ayarlar okunamadı: ${e.message}`;
  }
}

function renderRadio(r) {
  kv($("radioSummary"), [
    ["AX210", `${r.iface} · ${r.mac || "MAC yok"} · ${r.ipv4 || "IP yok"}`],
    ["bağlantı", r.connected ? `${r.ssid || "?"} · ${r.freq || "?"} MHz · ${r.width || "?"} MHz` : "bağlı değil"],
    ["AX210 hızı", r.txBitrate || "--"],
    ["Nexmon kanalı", `${r.chanspec20 || "--"} (ax210_ba) · ${r.chanspec || "--"} (ap_udp)`],
    ["ağ geçidi", r.gateway || "--"],
  ]);
  notes($("radioNotes"), [...(r.warnings || []).map((w) => `⚠ ${w}`), ...(r.notes || [])]);
}

function renderCalibration(cal, status) {
  if (!cal) {
    kv($("calSummary"), [["durum", "kalibrasyon yok"]]);
    notes($("calWarnings"), []);
    return;
  }
  const t = cal.thresholds || {};
  kv($("calSummary"), [
    ["tarih", new Date(cal.createdAt).toLocaleString("tr-TR")],
    ["süre / kare", `${fmt(cal.durationS, 0)} sn · ${cal.frames} kare · ${fmt(cal.rate, 1)}/sn`],
    ["PHY modu", String(cal.modes)],
    ["sızıntı iptali", cal.residual ? `artık ${fmt(cal.residual.residualDb, 1)} dB · durum ${cal.residual.state} dBm · ${cal.residual.frames} kare` : "yok (yeniden kalibre edin)"],
    ["eşikler", `hareket ${fmt(t.motion, 4)} · yenilik ${fmt(t.novelty, 2)}${t.gain ? ` · kazanç ${fmt(t.gain, 2)}` : ""}${t.residual ? ` · T² ${fmt(t.residual, 2)}` : ""} · statik ${fmt(t.static, 2)}${t.micro ? ` · mikro ${fmt(t.micro, 1)}` : ""}`],
    ["kanal / kaynak", `${cal.meta?.channel || "--"} · ${cal.meta?.backend || "--"} · ${cal.meta?.txMac || "--"}`],
  ]);
  notes($("calWarnings"), [...(cal.warnings || []), ...((status?.calibration?.issues) || []).map((x) => `Uyumsuzluk: ${x} → yeniden kalibre edin`)], "warn");
}

function renderProfiles(profiles) {
  const el = $("profileList");
  el.replaceChildren();
  const entries = Object.entries(profiles);
  $("profilePill").textContent = String(entries.length);
  for (const [label, p] of entries) {
    const row = document.createElement("div");
    row.className = "listRow";
    row.innerHTML = `<div><b>${label}</b><small>${p.frames || 0} kare · ${p.modes || 0} mod · p99 ${fmt(p.distP99, 2)} · ${new Date(p.createdAt).toLocaleString("tr-TR")}${(p.warnings || []).length ? " · ⚠ " + p.warnings.join(" ") : ""}</small></div>`;
    const del = document.createElement("button");
    del.className = "btn small";
    del.textContent = "Sil";
    del.addEventListener("click", async () => {
      if (!window.confirm(`"${label}" profili silinsin mi?`)) return;
      await run(() => api("/api/sensing/command", { command: "delete_profile", label }));
      loadSensing();
    });
    row.appendChild(del);
    el.appendChild(row);
  }
  if (!entries.length) el.innerHTML = '<p class="muted small">Henüz profil yok</p>';
}

$("radioRefresh").addEventListener("click", () => loadSensing(true));
$("calDuration").addEventListener("input", () => { S.calDurTouched = true; });
$("calStart").addEventListener("click", () => run(() => api("/api/sensing/command", { command: "calibrate", durationS: Number($("calDuration").value || 60) }), "Kalibrasyon başladı"));
$("calCancel").addEventListener("click", () => run(() => api("/api/sensing/command", { command: "cancel" })));
$("baselineBtn").addEventListener("click", () => run(() => api("/api/sensing/command", { command: "accept_baseline" }), "Statik referans tazelendi"));
$("profileStart").addEventListener("click", () => run(() => api("/api/sensing/command", { command: "profile", label: $("profileLabel").value, durationS: Number($("profileDuration").value || 30) }), "Profil kaydı başladı"));

/* ------------------------------------------------------------------ ayarlar */
function renderScenario(meta) {
  const sel = $("scenarioSelect");
  if (sel.options.length !== Object.keys(meta.scenarios || {}).length) {
    sel.replaceChildren();
    for (const [k, v] of Object.entries(meta.scenarios || {})) {
      const o = document.createElement("option");
      o.value = k;
      o.textContent = v.label;
      sel.appendChild(o);
    }
  }
  sel.value = meta.config.scenario;
  $("scenarioDesc").textContent = meta.scenarios?.[meta.config.scenario]?.description || "";
}

function renderSettings(meta) {
  const form = $("settingsForm");
  form.replaceChildren();
  const groups = new Map();
  for (const it of meta.schema || []) {
    if (!groups.has(it.group)) groups.set(it.group, []);
    groups.get(it.group).push(it);
  }
  for (const [g, items] of groups) {
    const fs = document.createElement("fieldset");
    fs.innerHTML = `<legend>${g}</legend>`;
    for (const it of items) {
      const lab = document.createElement("label");
      lab.className = `setting t-${it.type}`;
      lab.dataset.search = `${it.key} ${it.label} ${it.help || ""}`.toLowerCase();
      const name = document.createElement("span");
      name.textContent = it.label + (it.live ? "" : " ⟳");
      let input;
      const v = meta.config[it.key];
      if (it.type === "bool") {
        input = document.createElement("input");
        input.type = "checkbox";
        input.checked = Boolean(v);
      } else if (it.type === "enum") {
        input = document.createElement("select");
        for (const opt of it.options || []) {
          const o = document.createElement("option");
          o.value = opt;
          o.textContent = it.key === "scenario" ? meta.scenarios?.[opt]?.label || opt : opt;
          input.appendChild(o);
        }
        input.value = v;
      } else {
        input = document.createElement("input");
        input.type = it.type === "str" ? "text" : "number";
        if (it.min !== null && it.min !== undefined) input.min = it.min;
        if (it.max !== null && it.max !== undefined) input.max = it.max;
        input.step = it.type === "int" ? 1 : "any";
        input.value = v ?? "";
      }
      input.dataset.key = it.key;
      input.dataset.type = it.type;
      input.addEventListener("input", () => { S.formDirty = true; $("settingsMeta").textContent = "Kaydedilmemiş değişiklik var"; });
      const def = meta.scenarios?.[meta.config.scenario]?.overrides?.[it.key] ?? it.default;
      const small = document.createElement("small");
      small.textContent = `varsayılan ${def}${it.help ? " · " + it.help : ""}`;
      lab.append(name, input, small);
      fs.appendChild(lab);
    }
    form.appendChild(fs);
  }
  $("settingsMeta").textContent = "⟳ = Başlat'ta etkili · diğerleri anında uygulanır";
  filterSettings();
}

function filterSettings() {
  const q = $("settingsSearch").value.trim().toLowerCase();
  document.querySelectorAll("#settingsForm .setting").forEach((el) => { el.hidden = q && !el.dataset.search.includes(q); });
  document.querySelectorAll("#settingsForm fieldset").forEach((fs) => { fs.hidden = q && !fs.querySelector(".setting:not([hidden])"); });
}
$("settingsSearch").addEventListener("input", filterSettings);

$("settingsSave").addEventListener("click", async () => {
  const config = {};
  document.querySelectorAll("#settingsForm [data-key]").forEach((i) => {
    config[i.dataset.key] = i.dataset.type === "bool" ? i.checked : (i.dataset.type === "int" || i.dataset.type === "float") ? Number(i.value) : i.value;
  });
  S.formDirty = false;
  const meta = await run(() => api("/api/sensing/config", { config }), "Ayarlar kaydedildi");
  if (meta?.config) { S.sensing = meta; renderSettings(meta); renderScenario(meta); }
});
$("settingsReset").addEventListener("click", async () => {
  const sc = S.sensing?.config?.scenario || "room";
  if (!window.confirm(`Tüm ayarlar "${sc}" senaryosunun varsayılanlarına dönsün mü?`)) return;
  S.formDirty = false;
  const meta = await run(() => api("/api/sensing/config", { reset: true, scenario: sc }), "Varsayılanlar yüklendi");
  if (meta?.config) { S.sensing = meta; renderSettings(meta); renderScenario(meta); }
});
$("scenarioSelect").addEventListener("change", async () => {
  S.formDirty = false;
  const meta = await run(() => api("/api/sensing/config", { config: {}, applyScenario: $("scenarioSelect").value }), "Senaryo uygulandı");
  if (meta?.config) { S.sensing = meta; renderSettings(meta); renderScenario(meta); }
});

/* ------------------------------------------------------------------ ag */
async function loadNetwork() {
  const n = await run(() => api("/api/network"));
  if (!n) return;
  const mg = n.mgmt || {};
  kv($("netSummary"), [
    ["cihaz", `${n.hostname} (${n.mdns})`],
    ["yönetim (AX210)", mg.hotspot ? `HOTSPOT · ${n.config?.hotspotSsid}` : mg.connection ? `${mg.ssid || mg.connection}` : "bağlı değil"],
    ["IP", (mg.ipv4 || []).join(", ") || "--"],
    ["sinyal / frekans", `${mg.signal || "--"} · ${mg.freq || "--"} MHz`],
    ["hız", mg.txBitrate || "--"],
    ["Ethernet", (n.ethernet?.ipv4 || []).join(", ") || "takılı değil"],
  ]);
  $("netMdns").textContent = `http://${n.mdns}:8080`;
  $("deviceLine").textContent = `${n.mdns} · ${(mg.ipv4 || [])[0] || "IP yok"} · ${mg.hotspot ? "hotspot" : mg.ssid || "ağ yok"}`;
  $("netUrls").replaceChildren(...(n.panelUrls || []).map((u) => Object.assign(document.createElement("code"), { textContent: u })));
  $("hotspotPill").textContent = mg.hotspot ? "açık" : "kapalı";
  $("hotspotPill").className = `pill ${mg.hotspot ? "good" : "ghost"}`;
  const cfg = n.config || {};
  $("hsSsid").value = cfg.hotspotSsid || "";
  $("hsPass").value = cfg.hotspotPassword || "";
  $("hsAuto").checked = Boolean(cfg.autoHotspot);
  $("hsAfter").value = cfg.autoHotspotAfterS || 180;
  $("panelPass").placeholder = cfg.panelPasswordSet ? "ayarlı (değiştirmek için yazın)" : "boş = parolasız";
  $("netLog").textContent = (n.logTail || []).join("\n");
  const saved = $("savedNets");
  saved.replaceChildren();
  for (const s of n.saved || []) {
    const row = document.createElement("div");
    row.className = `listRow ${s.active ? "current" : ""}`;
    row.innerHTML = `<div><b>${s.ssid || s.name}</b><small>${s.name} · öncelik ${s.priority} · ${s.autoconnect ? "otomatik" : "elle"}${s.active ? " · BAĞLI" : ""}</small></div>`;
    const prio = Object.assign(document.createElement("input"), { type: "number", value: s.priority, className: "mini", title: "öncelik" });
    prio.addEventListener("change", () => run(() => api("/api/network/priority", { name: s.name, priority: Number(prio.value), autoconnect: true }), "Öncelik güncellendi"));
    const up = Object.assign(document.createElement("button"), { className: "btn small", textContent: "Bağlan", disabled: s.active });
    up.addEventListener("click", async () => {
      if (!window.confirm(`"${s.ssid || s.name}" ağına geçilsin mi? Panel bağlantısı kopacak.`)) return;
      await run(() => api("/api/network/activate", { name: s.name }), "Geçiş başladı; cihaz yeni ağda csi-alpha.local");
    });
    const del = Object.assign(document.createElement("button"), { className: "btn small danger", textContent: "Unut", disabled: s.active });
    del.addEventListener("click", async () => {
      if (!window.confirm(`"${s.name}" kaydı silinsin mi?`)) return;
      await run(() => api("/api/network/forget", { name: s.name }), "Silindi");
      loadNetwork();
    });
    row.append(prio, up, del);
    saved.appendChild(row);
  }
}
$("netRefresh").addEventListener("click", loadNetwork);
$("netScan").addEventListener("click", async () => {
  $("scanList").innerHTML = '<p class="muted small">Taranıyor…</p>';
  const out = await run(() => api("/api/network/scan", {}));
  const el = $("scanList");
  el.replaceChildren();
  for (const n of out?.networks || []) {
    const row = document.createElement("button");
    row.className = `listRow scanRow ${n.inUse ? "current" : ""}`;
    row.innerHTML = `<div><b>${n.ssid}</b><small>${n.security} · kanal ${n.channel} · ${n.freq}</small></div><span class="sig" style="--s:${n.signal}">${n.signal}%</span>`;
    row.addEventListener("click", () => { $("netSsid").value = n.ssid; $("netPass").focus(); });
    el.appendChild(row);
  }
  if (!el.children.length) el.innerHTML = `<p class="muted small">${out?.error || "Ağ bulunamadı"}</p>`;
});
$("netConnect").addEventListener("click", async () => {
  const ssid = $("netSsid").value.trim();
  if (!ssid) return toast("SSID girin", "bad");
  if (!window.confirm(`"${ssid}" ağına bağlanılsın mı? Panel bu ağdan kopacak; cihaz yeni ağda http://csi-alpha.local:8080 olur.`)) return;
  await run(() => api("/api/network/connect", { ssid, password: $("netPass").value, hidden: $("netHidden").checked, priority: Number($("netPrio").value || 50) }), "Bağlanılıyor… yeni ağda csi-alpha.local:8080");
});
$("netSaveCfg").addEventListener("click", async () => {
  const config = { hotspotSsid: $("hsSsid").value, hotspotPassword: $("hsPass").value, autoHotspot: $("hsAuto").checked, autoHotspotAfterS: Number($("hsAfter").value || 180) };
  if ($("panelPass").value) config.panelPassword = $("panelPass").value;
  const out = await run(() => api("/api/network/config", { config }), "Ağ ayarları kaydedildi");
  if (out?.ok && config.panelPassword !== undefined) { try { localStorage.setItem("csiKey", config.panelPassword); } catch { /* yok */ } }
  $("panelPass").value = "";
  loadNetwork();
});
$("hsOn").addEventListener("click", async () => {
  if (!window.confirm("Hotspot açılınca cihaz mevcut ağdan ayrılır. Devam?")) return;
  await run(() => api("/api/network/hotspot", { on: true }), "Hotspot açılıyor: 10.42.0.1:8080");
});
$("hsOff").addEventListener("click", () => run(() => api("/api/network/hotspot", { on: false }), "Hotspot kapatılıyor; kayıtlı ağa dönülüyor"));

/* ------------------------------------------------------------------ analiz */
async function loadRecordings() {
  const out = await run(() => api("/api/recordings"));
  const el = $("recList");
  el.replaceChildren();
  for (const r of out?.recordings || []) {
    const b = document.createElement("button");
    b.className = `listRow recRow ${S.selectedRec === r.name ? "current" : ""}`;
    const d = new Date(r.modifiedAt);
    b.innerHTML = `<div><b>${d.toLocaleString("tr-TR")}</b><small>${r.name} · ${(r.size / 1048576).toFixed(1)} MB${r.active ? " · KAYITTA" : ""}</small></div>${r.marks ? `<span class="chip info">${r.marks} olay</span>` : ""}`;
    b.addEventListener("click", () => {
      S.selectedRec = r.name;
      $("anaRun").disabled = false;
      el.querySelectorAll(".recRow").forEach((x) => x.classList.toggle("current", x === b));
      $("anaStatus").textContent = `Seçili: ${r.name}`;
    });
    el.appendChild(b);
  }
  if (!el.children.length) el.innerHTML = '<p class="muted small">Kayıt yok</p>';
}
$("recRefresh").addEventListener("click", loadRecordings);
$("anaCalMode").addEventListener("change", () => { $("anaCalSeg").hidden = $("anaCalMode").value !== "segment"; });
$("anaRun").addEventListener("click", async () => {
  if (!S.selectedRec) return;
  $("anaRun").disabled = true;
  $("anaStatus").textContent = "Analiz ediliyor… (uzun kayıtlarda birkaç saniye)";
  const body = { name: S.selectedRec, maxSeconds: Number($("anaMax").value || 3600) };
  if ($("anaCalMode").value === "segment") {
    body.calibrateFrom = Number($("anaCalFrom").value || 0);
    body.calibrateTo = Number($("anaCalTo").value || 60);
  }
  const out = await run(() => api("/api/analyze", body));
  $("anaRun").disabled = false;
  if (!out?.ok) { $("anaStatus").textContent = out?.error || "analiz başarısız"; return; }
  S.analysis = out.analysis;
  S.analysisView = null;
  $("anaStatus").textContent = `Tamam · ${out.analysis.elapsedS} sn sürdü`;
  renderAnalysis();
});

function renderAnalysis() {
  const a = S.analysis;
  const s = a.summary;
  const gt = s.groundTruth;
  const tiles = [
    ["Süre", `${fmt(s.durationS / 60, 1)} dk`],
    ["Geçerli kare", `${fmt(s.ratePerS, 1)}/sn`],
    ["Hareket aktif", `%${fmt(s.motionActivePct, 1)}`],
    ["Statik aktif", `%${fmt(s.staticActivePct, 1)}`],
    ["Alarm", `${Object.values(s.alarms).reduce((x, y) => x + y, 0)} (${fmt(s.alarmsPerHour, 1)}/sa)`],
    ["Recall", gt ? `${gt.detected}/${gt.events} (%${fmt(gt.recall * 100, 0)})` : "olay yok"],
    ["Gecikme p50/p90", gt && gt.latencyP50 !== null ? `${fmt(gt.latencyP50, 1)} / ${fmt(gt.latencyP90, 1)} sn` : "--"],
    ["Yanlış alarm", gt ? `${gt.falseAlarms} (${fmt(gt.falseAlarmsPerHour, 1)}/sa)` : "--"],
  ];
  $("anaKpis").replaceChildren(...tiles.map(([k, v]) => {
    const t = document.createElement("article");
    t.className = "card kpi";
    t.innerHTML = `<span>${k}</span><strong>${v}</strong>`;
    return t;
  }));
  $("anaTitle").textContent = `${a.pcap} · ${s.backend} · nfft ${s.nfft || "--"}${s.calibrated ? "" : " · KALİBRASYON YOK"}`;
  const cw = a.calibration?.warnings || [];
  if (cw.length) toast(`Kalibrasyon uyarısı: ${cw.join(" ")}`, "bad");
  anaChart.view = null;
  anaChart.setData({ rows: a.rows, alarms: a.alarms, marks: a.marks, intervals: a.intervals });
  drawAnalysisSignals();
  const ev = [
    ...a.alarms.map((x) => ({ t: x.t, kind: "alarm", label: alarmText(x.label), extra: `skor ${fmt(x.score, 2)}` })),
    ...a.marks.map((x) => ({ t: x.t, kind: `olay ${x.kind}`, label: x.label, extra: x.note || "" })),
  ].sort((p, q) => p.t - q.t);
  $("anaEvents").innerHTML = ev.length
    ? `<table><thead><tr><th>Zaman</th><th>+sn</th><th>Tür</th><th>Etiket</th><th></th></tr></thead><tbody>${ev.map((e) => `<tr class="${e.kind === "alarm" ? "al" : "gt"}"><td>${new Date(e.t * 1000).toLocaleTimeString("tr-TR")}</td><td>${fmt(e.t - a.t0, 1)}</td><td>${e.kind}</td><td>${e.label}</td><td>${e.extra}</td></tr>`).join("")}</tbody></table>`
    : '<p class="muted small">Alarm veya işaretli olay yok</p>';
  $("anaCsv").disabled = false;
  $("anaJson").disabled = false;
}

function drawAnalysisSignals() {
  const a = S.analysis;
  if (!a) return;
  const view = S.analysisView || [a.t0, a.t1];
  if (a.spectrogram) C.drawMatrix($("anaSpec"), { z: a.spectrogram.db, t: a.spectrogram.t, view, palette: "viridis", yLabels: ["0 Hz", `${fmt(a.spectrogram.fs / 2, 0)} Hz`] });
  else C.drawMatrix($("anaSpec"), {});
  if (a.heatmap) C.drawMatrix($("anaHeat"), { z: a.heatmap.z, t: a.heatmap.t, view, palette: "diverge", symmetric: true, yLabels: ["grup 1", `grup ${a.heatmap.groups}`] });
  else C.drawMatrix($("anaHeat"), {});
  if (a.pc1) {
    C.drawLine($("anaPc1"), { t: a.pc1.t, x: a.pc1.x, view });
    $("anaPcInfo").textContent = `açıklanan varyans %${fmt(a.pc1.explained * 100, 0)} · ${a.pc1.fs} Hz yeniden örnekleme`;
  }
}
$("anaReset").addEventListener("click", () => { S.analysisView = null; anaChart.setView(null); drawAnalysisSignals(); });

function download(name, text, type) {
  const blob = new Blob([text], { type });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
$("anaCsv").addEventListener("click", () => {
  const a = S.analysis;
  const keys = ["t", "o", "m", "mv", "mt", "n", "s", "sv", "st", "se", "mi", "r", "rssi", "lab"];
  const lines = [["t_rel_s", ...keys, "active"].join(",")];
  for (const r of a.rows) lines.push([fmt(r.t - a.t0, 2), ...keys.map((k) => (r[k] ?? "")), (r.a || []).join("|")].join(","));
  download(a.pcap.replace(".pcap", "_analiz.csv"), lines.join("\n"), "text/csv");
});
$("anaJson").addEventListener("click", () => download(S.analysis.pcap.replace(".pcap", "_analiz.json"), JSON.stringify(S.analysis), "application/json"));

/* ------------------------------------------------------------------ dosyalar */
async function loadFiles() {
  const out = await run(() => api("/api/files"));
  if (!out) return;
  const all = [...(out.captures || []), ...(out.datasets || [])].sort((a, b) => b.modifiedAt - a.modifiedAt);
  const el = $("fileList");
  el.replaceChildren();
  let total = 0;
  for (const f of all) {
    total += f.size;
    const row = document.createElement("div");
    row.className = "listRow";
    row.innerHTML = `<div><b>${f.name}</b><small>${(f.size / 1048576).toFixed(2)} MB · ${new Date(f.modifiedAt).toLocaleString("tr-TR")}</small></div>`;
    const dl = Object.assign(document.createElement("a"), { className: "btn small", href: f.downloadUrl, textContent: "İndir" });
    dl.download = f.name;
    const del = Object.assign(document.createElement("button"), { className: "btn small danger", textContent: "Sil" });
    del.addEventListener("click", async () => {
      if (!window.confirm(`${f.name} silinsin mi?`)) return;
      await run(() => api("/api/delete", { kind: f.kind, name: f.name }));
      loadFiles();
    });
    row.append(dl, del);
    el.appendChild(row);
  }
  $("filesMeta").textContent = `${all.length} dosya · ${(total / 1048576).toFixed(0)} MB`;
}
$("filesRefresh").addEventListener("click", loadFiles);

/* ------------------------------------------------------------------ eski: sureli yansima */
function renderReflection(r) {
  $("reflectionState").textContent = r.error ? "Hata" : r.running ? "Ölçümde" : "Hazır";
  $("reflectionState").className = `pill ${r.error ? "bad" : r.running ? "good" : "ghost"}`;
  const an = r.analysis;
  $("reflectionMeta").textContent = an?.ready
    ? `karar ${an.decision} · güven %${fmt((an.confidence || 0) * 100, 0)} · en yakın ${an.closestLabel}`
    : `${r.label || ""} ${r.running ? "ölçülüyor" : ""} · profil ${r.profile?.ready ? "hazır" : "yok"}`;
  if (r.logs) $("reflectionLogs").textContent = r.logs.join("\n");
}
$("reflectionStart").addEventListener("click", () => run(() => api("/api/reflection/start", {
  durationSecs: Number($("reflectionDuration").value || 300), label: $("reflectionLabel").value,
  channel: $("legacyChannel").value || "48/80", txRatePps: Number($("reflectionPps").value || 20),
})));
$("reflectionStop").addEventListener("click", () => run(() => api("/api/reflection/stop", {})));
$("reflectionProfile").addEventListener("click", () => run(() => api("/api/reflection/build-profile", {}), "Profil oluşturuldu"));

/* ------------------------------------------------------------------ baslangic */
(function init() {
  let theme = null;
  let tab = "live";
  try { theme = localStorage.getItem("csiTheme"); tab = localStorage.getItem("csiTab") || "live"; } catch { /* yok */ }
  const hashTab = location.hash.replace("#", "");
  if (document.getElementById(`view-${hashTab}`)) tab = hashTab;
  applyTheme(theme);
  connect();
  loadHistory();
  loadSensing(true);
  loadAlarms();
  loadNetwork();
  api("/api/status").then(renderStatus).catch(() => {});
  showTab(tab);
  setInterval(() => { if (!S.status?.running) loadSensing(); }, 20000);
})();
