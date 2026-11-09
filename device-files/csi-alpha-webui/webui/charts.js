/* CSI Alpha - bagimsiz canvas grafik ve zaman serisi yardimcilari (kutuphane yok). */
(function (global) {
  "use strict";

  function css(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function fitCanvas(canvas) {
    const dpr = Math.max(1, Math.min(2.5, window.devicePixelRatio || 1));
    const cssW = canvas.clientWidth || canvas.parentElement.clientWidth || 600;
    const cssH = Number(canvas.getAttribute("height")) || 200;
    if (!canvas.dataset.cssH) canvas.dataset.cssH = String(cssH);
    const h = Number(canvas.dataset.cssH);
    canvas.style.height = `${h}px`;
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w: cssW, h };
  }

  function fmtTime(t, span) {
    const d = new Date(t * 1000);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const ss = String(d.getSeconds()).padStart(2, "0");
    return span > 3 * 3600 ? `${hh}:${mm}` : `${hh}:${mm}:${ss}`;
  }

  function niceStep(span, target) {
    const raw = span / Math.max(1, target);
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 5, 10]) if (raw <= m * pow) return m * pow;
    return 10 * pow;
  }

  function fmtNum(v) {
    if (v === null || v === undefined || !Number.isFinite(v)) return "--";
    if (v === 0) return "0";
    const a = Math.abs(v);
    if (a >= 100) return v.toFixed(0);
    if (a >= 10) return v.toFixed(1);
    if (a >= 1) return v.toFixed(2);
    if (a >= 0.01) return v.toFixed(3);
    return v.toExponential(1);
  }

  /* ------------------------------------------------------------------ */
  /* Cok izli zaman serisi grafigi                                        */
  /* ------------------------------------------------------------------ */
  class TimeChart {
    constructor(canvas, opts) {
      this.canvas = canvas;
      this.tip = opts.tooltip || null;
      this.tracks = opts.tracks;
      this.rows = [];
      this.alarms = [];        // [{t, label}]
      this.bands = [];         // [{t0, t1}]
      this.marks = [];         // [{t, label, kind}]
      this.intervals = [];     // [{start, end, label}]
      this.view = null;        // [t0, t1] (null = tum veri / canli pencere)
      this.liveSpan = opts.liveSpan || null;
      this.onZoom = opts.onZoom || null;
      this.zoomable = opts.zoomable !== false;
      this.hoverX = null;
      this.drag = null;
      this.pad = { l: 56, r: 14, t: 8, b: 22 };
      this._bind();
    }

    setData({ rows, alarms, marks, intervals }) {
      if (rows) this.rows = rows;
      if (alarms) this.alarms = alarms;
      if (marks) this.marks = marks;
      if (intervals) this.intervals = intervals;
      this.bands = alarmBands(this.rows);
      this.draw();
    }

    domain() {
      if (this.view) return this.view;
      if (!this.rows.length) return [Date.now() / 1000 - (this.liveSpan || 60), Date.now() / 1000];
      const t1 = this.rows[this.rows.length - 1].t;
      if (this.liveSpan) return [t1 - this.liveSpan, t1];
      return [this.rows[0].t, t1];
    }

    setView(view) {
      this.view = view;
      this.draw();
    }

    _bind() {
      const c = this.canvas;
      c.addEventListener("mousemove", (e) => {
        const r = c.getBoundingClientRect();
        this.hoverX = e.clientX - r.left;
        if (this.drag) this.drag.x1 = this.hoverX;
        this.draw();
      });
      c.addEventListener("mouseleave", () => {
        this.hoverX = null;
        this.drag = null;
        if (this.tip) this.tip.style.display = "none";
        this.draw();
      });
      if (!this.zoomable) return;
      c.addEventListener("mousedown", (e) => {
        const r = c.getBoundingClientRect();
        this.drag = { x0: e.clientX - r.left, x1: e.clientX - r.left };
      });
      c.addEventListener("mouseup", () => {
        if (!this.drag) return;
        const { x0, x1 } = this.drag;
        this.drag = null;
        if (Math.abs(x1 - x0) > 8) {
          const [a, b] = [Math.min(x0, x1), Math.max(x0, x1)].map((x) => this.xToT(x));
          this.view = [a, b];
          if (this.onZoom) this.onZoom(this.view);
        }
        this.draw();
      });
      c.addEventListener("dblclick", () => {
        this.view = null;
        if (this.onZoom) this.onZoom(null);
        this.draw();
      });
    }

    xToT(x) {
      const [t0, t1] = this.domain();
      const w = this._w - this.pad.l - this.pad.r;
      return t0 + ((x - this.pad.l) / Math.max(1, w)) * (t1 - t0);
    }

    draw() {
      const { ctx, w, h } = fitCanvas(this.canvas);
      this._w = w;
      const bg = css("--chart-bg", "#0f1513");
      const grid = css("--chart-grid", "rgba(255,255,255,.07)");
      const ink = css("--chart-ink", "#c9d6cf");
      const muted = css("--chart-muted", "#7b8a83");
      const alarm = css("--chart-alarm", "rgba(229,72,77,.18)");
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, w, h);
      const [t0, t1] = this.domain();
      const span = Math.max(1e-6, t1 - t0);
      const L = this.pad.l;
      const R = w - this.pad.r;
      const x = (t) => L + ((t - t0) / span) * (R - L);
      const totalWeight = this.tracks.reduce((s, tr) => s + (tr.weight || 1), 0);
      const usable = h - this.pad.t - this.pad.b;
      let top = this.pad.t;
      const visible = this.rows.filter((r) => r.t >= t0 - 1 && r.t <= t1 + 1);
      const geom = [];
      ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
      for (const tr of this.tracks) {
        const th = (usable * (tr.weight || 1)) / totalWeight;
        const y0 = top + 4;
        const y1 = top + th - 6;
        geom.push({ tr, y0, y1 });
        // y araligi
        let lo = tr.min !== undefined ? tr.min : Infinity;
        let hi = tr.max !== undefined ? tr.max : -Infinity;
        if (tr.min === undefined || tr.max === undefined) {
          for (const r of visible) {
            for (const s of tr.series) {
              const v = r[s.key];
              if (v === null || v === undefined || !Number.isFinite(v)) continue;
              if (tr.min === undefined) lo = Math.min(lo, v);
              if (tr.max === undefined) hi = Math.max(hi, v);
            }
            if (tr.thrKey && Number.isFinite(r[tr.thrKey]) && tr.max === undefined) hi = Math.max(hi, r[tr.thrKey] * 1.25);
          }
          if (!Number.isFinite(lo)) lo = 0;
          if (!Number.isFinite(hi)) hi = 1;
          if (tr.clampMax !== undefined) hi = Math.min(hi, tr.clampMax);
          if (hi - lo < 1e-9) hi = lo + 1;
          const padv = (hi - lo) * 0.08;
          if (tr.min === undefined) lo -= padv;
          if (tr.max === undefined) hi += padv;
        }
        const y = (v) => y1 - ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * (y1 - y0);
        geom[geom.length - 1].y = y;
        geom[geom.length - 1].lo = lo;
        geom[geom.length - 1].hi = hi;
        // alarm bantlari
        ctx.fillStyle = alarm;
        for (const b of this.bands) {
          if (b.t1 < t0 || b.t0 > t1) continue;
          ctx.fillRect(x(Math.max(t0, b.t0)), y0, Math.max(1.5, x(Math.min(t1, b.t1)) - x(Math.max(t0, b.t0))), y1 - y0);
        }
        // yer gercegi araliklari
        ctx.fillStyle = css("--chart-gt", "rgba(106,166,232,.14)");
        for (const g of this.intervals) {
          if (g.end < t0 || g.start > t1) continue;
          ctx.fillRect(x(Math.max(t0, g.start)), y0, Math.max(1.5, x(Math.min(t1, g.end)) - x(Math.max(t0, g.start))), y1 - y0);
        }
        // izgara
        ctx.strokeStyle = grid;
        ctx.lineWidth = 1;
        for (let k = 0; k <= 2; k += 1) {
          const yy = y0 + ((y1 - y0) * k) / 2;
          ctx.beginPath();
          ctx.moveTo(L, yy);
          ctx.lineTo(R, yy);
          ctx.stroke();
        }
        ctx.fillStyle = muted;
        ctx.fillText(fmtNum(hi), 4, y0 + 9);
        ctx.fillText(fmtNum(lo), 4, y1);
        // esik
        if (tr.thrValue !== undefined) {
          ctx.strokeStyle = css("--chart-thr", "#e5484d");
          ctx.setLineDash([5, 4]);
          ctx.beginPath();
          ctx.moveTo(L, y(tr.thrValue));
          ctx.lineTo(R, y(tr.thrValue));
          ctx.stroke();
          ctx.setLineDash([]);
        }
        if (tr.thrKey) {
          ctx.strokeStyle = css("--chart-thr", "#e5484d");
          ctx.setLineDash([5, 4]);
          ctx.beginPath();
          let started = false;
          for (const r of visible) {
            const v = r[tr.thrKey];
            if (!Number.isFinite(v)) { started = false; continue; }
            if (!started) { ctx.moveTo(x(r.t), y(v)); started = true; } else ctx.lineTo(x(r.t), y(v));
          }
          ctx.stroke();
          ctx.setLineDash([]);
        }
        // seriler
        for (const s of tr.series) {
          ctx.strokeStyle = s.color;
          ctx.lineWidth = s.width || 1.6;
          ctx.beginPath();
          let started = false;
          let lastT = null;
          for (const r of visible) {
            const v = r[s.key];
            if (v === null || v === undefined || !Number.isFinite(v) || (lastT !== null && r.t - lastT > 3)) {
              started = false;
              lastT = r.t;
              if (v === null || v === undefined || !Number.isFinite(v)) continue;
            }
            if (!started) { ctx.moveTo(x(r.t), y(v)); started = true; } else ctx.lineTo(x(r.t), y(v));
            lastT = r.t;
          }
          ctx.stroke();
        }
        ctx.fillStyle = ink;
        ctx.fillText(tr.label, L + 6, y0 + 11);
        let lx = L + 12 + ctx.measureText(tr.label).width;
        for (const s of tr.series) {
          ctx.fillStyle = s.color;
          ctx.fillRect(lx, y0 + 5, 10, 3);
          ctx.fillStyle = muted;
          ctx.fillText(s.label, lx + 13, y0 + 11);
          lx += 22 + ctx.measureText(s.label).width;
        }
        top += th;
      }
      // isaretler ve alarmlar
      for (const m of this.marks) {
        if (m.t < t0 || m.t > t1) continue;
        ctx.strokeStyle = css("--chart-mark", "#6aa6e8");
        ctx.setLineDash(m.kind === "end" ? [3, 3] : []);
        ctx.beginPath();
        ctx.moveTo(x(m.t), this.pad.t);
        ctx.lineTo(x(m.t), h - this.pad.b);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      for (const a of this.alarms) {
        if (a.t < t0 || a.t > t1) continue;
        ctx.fillStyle = css("--chart-thr", "#e5484d");
        ctx.beginPath();
        ctx.moveTo(x(a.t), h - this.pad.b);
        ctx.lineTo(x(a.t) - 5, h - this.pad.b + 8);
        ctx.lineTo(x(a.t) + 5, h - this.pad.b + 8);
        ctx.fill();
      }
      // zaman ekseni
      ctx.fillStyle = muted;
      const step = niceStep(span, Math.max(2, Math.floor((R - L) / 110)));
      for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
        const xx = x(t);
        ctx.fillText(fmtTime(t, span), xx - 22, h - 6);
        ctx.strokeStyle = grid;
        ctx.beginPath();
        ctx.moveTo(xx, this.pad.t);
        ctx.lineTo(xx, h - this.pad.b);
        ctx.stroke();
      }
      // surukleme secimi
      if (this.drag) {
        ctx.fillStyle = "rgba(106,166,232,.18)";
        ctx.fillRect(Math.min(this.drag.x0, this.drag.x1), this.pad.t, Math.abs(this.drag.x1 - this.drag.x0), h - this.pad.t - this.pad.b);
      }
      // hover
      if (this.hoverX !== null && this.hoverX >= L && this.hoverX <= R && visible.length) {
        const tt = this.xToT(this.hoverX);
        let best = visible[0];
        for (const r of visible) if (Math.abs(r.t - tt) < Math.abs(best.t - tt)) best = r;
        ctx.strokeStyle = ink;
        ctx.globalAlpha = 0.5;
        ctx.beginPath();
        ctx.moveTo(x(best.t), this.pad.t);
        ctx.lineTo(x(best.t), h - this.pad.b);
        ctx.stroke();
        ctx.globalAlpha = 1;
        if (this.tip) {
          const lines = [`<b>${fmtTime(best.t, 0)}</b> · ${best.o || ""}`];
          for (const { tr } of geom) {
            for (const s of tr.series) lines.push(`<i style="background:${s.color}"></i>${s.label}: ${fmtNum(best[s.key])}`);
            if (tr.thrKey) lines.push(`<i class="thr"></i>eşik: ${fmtNum(best[tr.thrKey])}`);
          }
          if (best.a && best.a.length) lines.push(`alarm: ${best.a.join(", ")}`);
          if (best.lab) lines.push(`durum: ${best.lab}`);
          this.tip.innerHTML = lines.join("<br>");
          this.tip.style.display = "block";
          const left = x(best.t) + 14;
          this.tip.style.left = `${left + 190 > w ? left - 210 : left}px`;
          this.tip.style.top = "10px";
        }
      } else if (this.tip) {
        this.tip.style.display = "none";
      }
    }
  }

  function alarmBands(rows) {
    const bands = [];
    let cur = null;
    for (const r of rows) {
      const on = r.o === "alarm";
      if (on && !cur) cur = { t0: r.t, t1: r.t };
      else if (on && cur) cur.t1 = r.t;
      else if (!on && cur) { cur.t1 = r.t; bands.push(cur); cur = null; }
    }
    if (cur) bands.push(cur);
    return bands;
  }

  /* ------------------------------------------------------------------ */
  /* Renk haritalari                                                      */
  /* ------------------------------------------------------------------ */
  const VIRIDIS = [[68, 1, 84], [65, 68, 135], [42, 120, 142], [34, 168, 132], [122, 209, 81], [253, 231, 37]];
  const DIVERGE = [[33, 102, 172], [103, 169, 207], [209, 229, 240], [247, 247, 247], [253, 219, 199], [239, 138, 98], [178, 24, 43]];

  function ramp(stops, t) {
    const v = Math.max(0, Math.min(1, t)) * (stops.length - 1);
    const i = Math.min(stops.length - 2, Math.floor(v));
    const f = v - i;
    const a = stops[i];
    const b = stops[i + 1];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  }

  function percentile(values, q) {
    if (!values.length) return 0;
    const s = values.slice().sort((a, b) => a - b);
    return s[Math.min(s.length - 1, Math.max(0, Math.round((s.length - 1) * q)))];
  }

  /* Matris (satir x sutun) -> canvas; zaman ekseni opsiyonel domain ile hizalanir */
  function drawMatrix(canvas, { z, t, view, palette, symmetric, yLabels, label }) {
    const { ctx, w, h } = fitCanvas(canvas);
    const L = 56;
    const R = w - 14;
    const T = 6;
    const B = h - 20;
    ctx.fillStyle = css("--chart-bg", "#0f1513");
    ctx.fillRect(0, 0, w, h);
    if (!z || !z.length || !z[0].length || !t || !t.length) {
      ctx.fillStyle = css("--chart-muted", "#7b8a83");
      ctx.font = "12px ui-monospace, monospace";
      ctx.fillText("veri bekleniyor", L, 22);
      return;
    }
    const rows = z.length;
    const cols = z[0].length;
    const flat = [];
    for (let r = 0; r < rows; r += Math.max(1, Math.floor(rows / 24))) {
      for (let c = 0; c < cols; c += Math.max(1, Math.floor(cols / 200))) flat.push(z[r][c]);
    }
    let lo;
    let hi;
    if (symmetric) {
      const m = percentile(flat.map(Math.abs), 0.98) || 1e-6;
      lo = -m;
      hi = m;
    } else {
      lo = percentile(flat, 0.05);
      hi = percentile(flat, 0.995);
      if (hi - lo < 1e-9) hi = lo + 1;
    }
    const [t0, t1] = view || [t[0], t[t.length - 1]];
    const span = Math.max(1e-6, t1 - t0);
    const img = ctx.createImageData(Math.max(1, Math.round(R - L)), Math.max(1, Math.round(B - T)));
    const W = img.width;
    const H = img.height;
    // her piksel sutunu icin en yakin zaman sutunu
    const colIdx = new Int32Array(W);
    let j = 0;
    for (let px = 0; px < W; px += 1) {
      const tt = t0 + (px / Math.max(1, W - 1)) * span;
      while (j + 1 < t.length && t[j + 1] <= tt) j += 1;
      colIdx[px] = tt < t[0] || tt > t[t.length - 1] ? -1 : j;
    }
    for (let py = 0; py < H; py += 1) {
      const r = rows - 1 - Math.min(rows - 1, Math.floor((py / H) * rows));
      const rowData = z[r];
      for (let px = 0; px < W; px += 1) {
        const ci = colIdx[px];
        const o = (py * W + px) * 4;
        if (ci < 0) {
          img.data[o + 3] = 0;
          continue;
        }
        const c = ramp(palette === "diverge" ? DIVERGE : VIRIDIS, (rowData[ci] - lo) / (hi - lo));
        img.data[o] = c[0];
        img.data[o + 1] = c[1];
        img.data[o + 2] = c[2];
        img.data[o + 3] = 255;
      }
    }
    // putImageData donusumu yok sayar; gecici canvas uzerinden olcekli cizilir
    const tmp = document.createElement("canvas");
    tmp.width = W;
    tmp.height = H;
    tmp.getContext("2d").putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(tmp, L, T, R - L, B - T);
    ctx.fillStyle = css("--chart-muted", "#7b8a83");
    ctx.font = "11px ui-monospace, monospace";
    if (yLabels) {
      ctx.fillText(yLabels[1], 4, T + 10);
      ctx.fillText(yLabels[0], 4, B);
    }
    if (label) ctx.fillText(label, L + 6, T + 12);
    const step = niceStep(span, Math.max(2, Math.floor((R - L) / 110)));
    for (let tt = Math.ceil(t0 / step) * step; tt <= t1; tt += step) {
      const xx = L + ((tt - t0) / span) * (R - L);
      ctx.fillText(fmtTime(tt, span), xx - 22, h - 6);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Sinyal isleme (tarayicida canli spektrogram icin)                    */
  /* ------------------------------------------------------------------ */
  function fftMag(re) {
    // radix-2 FFT, giris uzunlugu 2^k; donus: |X|^2 (0..N/2)
    const n = re.length;
    const xr = Float64Array.from(re);
    const xi = new Float64Array(n);
    for (let i = 1, j = 0; i < n; i += 1) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) {
        [xr[i], xr[j]] = [xr[j], xr[i]];
      }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const ang = (-2 * Math.PI) / len;
      for (let i = 0; i < n; i += len) {
        for (let k = 0; k < len / 2; k += 1) {
          const wr = Math.cos(ang * k);
          const wi = Math.sin(ang * k);
          const ar = xr[i + k + len / 2] * wr - xi[i + k + len / 2] * wi;
          const ai = xr[i + k + len / 2] * wi + xi[i + k + len / 2] * wr;
          xr[i + k + len / 2] = xr[i + k] - ar;
          xi[i + k + len / 2] = xi[i + k] - ai;
          xr[i + k] += ar;
          xi[i + k] += ai;
        }
      }
    }
    const out = new Float64Array(n / 2 + 1);
    for (let k = 0; k <= n / 2; k += 1) out[k] = xr[k] * xr[k] + xi[k] * xi[k];
    return out;
  }

  /* frames: {t:[], m:[], a:[[G]]} -> birincil mod, duzgun izgara, sapma matrisi, PC1, STFT */
  function liveAnalysis(frames, { fs = 20, nperseg = 64, hop = 8, windowS = 60 } = {}) {
    const n = frames.t.length;
    if (n < 40) return null;
    const tEnd = frames.t[n - 1];
    const counts = new Map();
    for (let i = 0; i < n; i += 1) if (frames.t[i] >= tEnd - windowS && frames.m[i] >= 0) counts.set(frames.m[i], (counts.get(frames.m[i]) || 0) + 1);
    let primary = -1;
    let best = 0;
    for (const [m, c] of counts) if (c > best) { best = c; primary = m; }
    const T = [];
    const A = [];
    for (let i = 0; i < n; i += 1) {
      if (frames.t[i] >= tEnd - windowS && frames.m[i] === primary) {
        T.push(frames.t[i]);
        A.push(frames.a[i]);
      }
    }
    if (T.length < 40) return null;
    const G = A[0].length;
    const grid = [];
    for (let t = T[0]; t <= T[T.length - 1]; t += 1 / fs) grid.push(t);
    if (grid.length < nperseg) return null;
    // lineer interpolasyon
    const X = grid.map(() => new Float64Array(G));
    let j = 0;
    for (let k = 0; k < grid.length; k += 1) {
      const tt = grid[k];
      while (j + 1 < T.length && T[j + 1] < tt) j += 1;
      const a = A[j];
      const b = A[Math.min(j + 1, A.length - 1)];
      const ta = T[j];
      const tb = T[Math.min(j + 1, T.length - 1)];
      const w = tb > ta ? Math.max(0, Math.min(1, (tt - ta) / (tb - ta))) : 0;
      for (let g = 0; g < G; g += 1) X[k][g] = a[g] * (1 - w) + b[g] * w;
    }
    // ortalama cikar (sapma)
    const mean = new Float64Array(G);
    for (const row of X) for (let g = 0; g < G; g += 1) mean[g] += row[g];
    for (let g = 0; g < G; g += 1) mean[g] /= X.length;
    for (const row of X) for (let g = 0; g < G; g += 1) row[g] -= mean[g];
    // kovaryans ve guc yinelemesi (PC1)
    const C = Array.from({ length: G }, () => new Float64Array(G));
    for (const row of X) for (let a = 0; a < G; a += 1) { const ra = row[a]; for (let b = a; b < G; b += 1) C[a][b] += ra * row[b]; }
    let trace = 0;
    for (let a = 0; a < G; a += 1) { trace += C[a][a]; for (let b = 0; b < a; b += 1) C[a][b] = C[b][a]; }
    let v = new Float64Array(G).fill(1 / Math.sqrt(G));
    let lambda = 0;
    for (let it = 0; it < 40; it += 1) {
      const nv = new Float64Array(G);
      for (let a = 0; a < G; a += 1) { let s = 0; for (let b = 0; b < G; b += 1) s += C[a][b] * v[b]; nv[a] = s; }
      const norm = Math.sqrt(nv.reduce((s, x) => s + x * x, 0)) || 1;
      lambda = norm;
      v = nv.map((x) => x / norm);
    }
    const pc1 = X.map((row) => row.reduce((s, x, g) => s + x * v[g], 0));
    // STFT
    const win = Array.from({ length: nperseg }, (_, i) => 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (nperseg - 1)));
    const specT = [];
    const cols = [];
    for (let s0 = 0; s0 + nperseg <= pc1.length; s0 += hop) {
      const seg = new Array(nperseg);
      for (let i = 0; i < nperseg; i += 1) seg[i] = pc1[s0 + i] * win[i];
      const p = fftMag(seg);
      cols.push(Array.from(p, (x) => 10 * Math.log10(x + 1e-9)));
      specT.push(grid[s0 + Math.floor(nperseg / 2)]);
    }
    const F = cols.length ? cols[0].length : 0;
    const db = Array.from({ length: F }, (_, f) => cols.map((c) => c[f]));
    // isi haritasi: grup x zaman (10 Hz'e seyrelt)
    const step = Math.max(1, Math.round(fs / 10));
    const heatT = [];
    const heat = Array.from({ length: G }, () => []);
    for (let k = 0; k < X.length; k += step) {
      heatT.push(grid[k]);
      for (let g = 0; g < G; g += 1) heat[g].push(X[k][g]);
    }
    return {
      grid,
      pc1,
      explained: trace > 0 ? lambda / trace : 0,
      spec: { t: specT, db, fMax: fs / 2 },
      heat: { t: heatT, z: heat },
      groups: G,
      primary,
      samples: T.length,
    };
  }

  /* Basit cizgi (PC1 gibi) */
  function drawLine(canvas, { t, x, view, color, label }) {
    const { ctx, w, h } = fitCanvas(canvas);
    const L = 56;
    const R = w - 14;
    ctx.fillStyle = css("--chart-bg", "#0f1513");
    ctx.fillRect(0, 0, w, h);
    if (!t || !t.length) return;
    const [t0, t1] = view || [t[0], t[t.length - 1]];
    let lo = Infinity;
    let hi = -Infinity;
    for (let i = 0; i < t.length; i += 1) if (t[i] >= t0 && t[i] <= t1) { lo = Math.min(lo, x[i]); hi = Math.max(hi, x[i]); }
    if (!Number.isFinite(lo)) return;
    if (hi - lo < 1e-9) hi = lo + 1;
    ctx.strokeStyle = color || css("--chart-accent", "#e1ad41");
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    let started = false;
    const stride = Math.max(1, Math.floor(t.length / ((R - L) * 2)));
    for (let i = 0; i < t.length; i += stride) {
      if (t[i] < t0 || t[i] > t1) continue;
      const xx = L + ((t[i] - t0) / (t1 - t0)) * (R - L);
      const yy = h - 6 - ((x[i] - lo) / (hi - lo)) * (h - 14);
      if (!started) { ctx.moveTo(xx, yy); started = true; } else ctx.lineTo(xx, yy);
    }
    ctx.stroke();
    ctx.fillStyle = css("--chart-muted", "#7b8a83");
    ctx.font = "11px ui-monospace, monospace";
    if (label) ctx.fillText(label, L + 6, 14);
  }

  global.CsiCharts = { TimeChart, drawMatrix, drawLine, liveAnalysis, fmtNum, fmtTime, alarmBands, fitCanvas };
})(window);
