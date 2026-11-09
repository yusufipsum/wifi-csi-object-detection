#!/usr/bin/env python3
"""Zaman serisi analizi: canli gecmis tamponu ve kayitli oturumlarin yeniden oynatilmasi.

- history_row(): motorun karar ciktisini panel grafikleri icin kompakt satira cevirir.
- analyze_capture(): bir pcap'i CANLI motorla ayni kodla yeniden oynatir; skor/metrik zaman
  serileri, alarmlar, yer gercegi isaretleri, alt tasiyici-grup isi haritasi, PCA birinci
  bilesen (PC1) sinyali ve STFT spektrogrami uretir. numpy varsa PC1/spektrogram hesaplanir
  (Pi'de mevcut), yoksa bu kisimlar atlanir.
"""

import collections
import json
import math
from pathlib import Path

import csi_sensing as cs

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


def _r(value, digits=5):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return round(value, digits)


def history_row(info):
    det = info.get("detectors") or {}
    m = det.get("motion") or {}
    st = det.get("static") or {}
    mi = det.get("micro") or {}
    sp = det.get("state") or {}
    sig = info.get("signal") or {}
    return {
        "t": info.get("t"),
        "o": info.get("overall"),
        "m": _r(m.get("score"), 4),
        "mv": _r(m.get("value"), 6),
        "mt": _r(m.get("thr"), 6),
        "n": _r(m.get("novelty"), 4),
        "g": _r(m.get("gain"), 4),
        "rc": _r(m.get("residual"), 3),
        "rt": _r(m.get("residualThr"), 3),
        "gt": _r(m.get("gainThr"), 4),
        "s": _r(st.get("score"), 4),
        "sv": _r(st.get("value"), 4),
        "st": _r(st.get("thr"), 4),
        "se": bool(st.get("evaluated")),
        "mi": _r(mi.get("score"), 4),
        "r": _r(sig.get("rate"), 2),
        "rssi": _r(sig.get("rssi"), 1),
        "lab": sp.get("stable"),
        "a": info.get("activeAlarms") or [],
    }


def detect_backend(samples):
    counts = collections.Counter(s.get("frameControl") for s in samples[:600])
    data = counts.get(0x88, 0) + counts.get(0x08, 0)
    best = max((counts.get(0xD0, 0), "ax210_action"), (counts.get(0x94, 0), "ax210_ba"), (data, "ap_udp"))
    return best[1] if best[0] > 0 else "ap_udp"


def load_marks(events_path):
    marks = []
    alarms = []
    path = Path(events_path) if events_path else None
    if not path or not path.exists():
        return marks, alarms
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") == "gt":
                marks.append({"t": float(row.get("ts") or 0) / 1000.0, "kind": row.get("kind"),
                              "label": row.get("label"), "note": row.get("note")})
            elif row.get("kind") == "alarm" and not row.get("suppressed"):
                alarms.append({"t": float(row.get("t") or 0), "label": row.get("label"), "score": row.get("score")})
    return marks, alarms


def gt_intervals(marks):
    open_marks = {}
    out = []
    for mk in sorted(marks, key=lambda x: x["t"]):
        if mk["kind"] == "start":
            open_marks[mk["label"]] = mk["t"]
        elif mk["kind"] == "end" and mk["label"] in open_marks:
            out.append((open_marks.pop(mk["label"]), mk["t"], mk["label"]))
        elif mk["kind"] == "instant":
            out.append((mk["t"], mk["t"] + 3.0, mk["label"]))
    return out


def event_metrics(intervals, alarms, t0, t1):
    intervals = [(a, b, lab) for a, b, lab in intervals if b >= t0 and a <= t1]
    if not intervals:
        return None
    hits = 0
    latencies = []
    for a, b, _lab in intervals:
        inside = [al["t"] for al in alarms if a - 1.0 <= al["t"] <= b + 5.0]
        if inside:
            hits += 1
            latencies.append(max(0.0, min(inside) - a))
    false_alarms = [al for al in alarms if not any(a - 1.0 <= al["t"] <= b + 5.0 for a, b, _ in intervals)]
    gt_time = sum(b - a for a, b, _ in intervals)
    neg_hours = max(1e-6, (t1 - t0 - gt_time) / 3600.0)
    lat = sorted(latencies)
    return {
        "events": len(intervals),
        "detected": hits,
        "recall": round(hits / len(intervals), 4),
        "latencyP50": round(lat[len(lat) // 2], 2) if lat else None,
        "latencyP90": round(lat[min(len(lat) - 1, int(0.9 * len(lat)))], 2) if lat else None,
        "falseAlarms": len(false_alarms),
        "falseAlarmsPerHour": round(len(false_alarms) / neg_hours, 2),
    }


def _calibrate(config, samples, start_s, end_s):
    t_first = samples[0]["ts"] / 1000.0
    seg = [s for s in samples if start_s <= s["ts"] / 1000.0 - t_first < end_s]
    if len(seg) < 100:
        raise ValueError("kalibrasyon araligi cok kisa")
    engine = cs.SensingEngine(config)
    t0 = seg[0]["ts"] / 1000.0
    t_end = seg[-1]["ts"] / 1000.0
    warm = max(config["motionWindowS"], config["staticWindowS"]) + 1.5
    duration = (t_end - t0) - warm - 1.0
    if duration < 8:
        raise ValueError("kalibrasyon araligi cok kisa (>= ~15 sn)")
    started = False
    for sample in seg:
        t = sample["ts"] / 1000.0
        if not started and t - t0 >= warm:
            engine.start_calibration(duration, t=t)
            started = True
        _info, events = engine.push(sample)
        for ev in events:
            if ev["kind"] == "calibration_done":
                return ev["calibration"]
            if ev["kind"] == "calibration_failed":
                raise ValueError(ev.get("reason"))
    _info, events = engine.tick(t_end + duration)
    for ev in events:
        if ev["kind"] == "calibration_done":
            return ev["calibration"]
    raise ValueError("kalibrasyon tamamlanamadi")


def analyze_capture(pcap_path, config, calibration=None, profiles=None, cal_segment=None,
                    events_path=None, max_seconds=3600, heat_cols=600, spec_cols=900, fs=20.0):
    samples = list(cs.iter_pcap(pcap_path, None, max_seconds))
    if not samples:
        raise ValueError("pcap bos veya CSI karesi yok")
    config = dict(config)
    detected = detect_backend(samples)
    config["txBackend"] = detected
    config = cs.normalize_config(config)[0]
    if cal_segment:
        calibration = _calibrate(config, samples, float(cal_segment[0]), float(cal_segment[1]))
    engine = cs.SensingEngine(config, calibration=calibration, profiles=profiles or {},
                              meta={"source": Path(pcap_path).name})
    rows = []
    alarms = []
    frames_t = []
    frames_amp = []
    frames_mode = []
    last_frame = None
    for sample in samples:
        info, events = engine.push(sample)
        if info is not None:
            rows.append(history_row(info))
        for ev in events:
            if ev["kind"] == "alarm" and not ev.get("suppressed"):
                alarms.append({"t": ev["t"], "label": ev["label"], "score": ev.get("score")})
        fr = engine.last_frame()
        if fr is not None and fr is not last_frame:
            last_frame = fr
            if len(frames_t) < 200000:
                frames_t.append(fr.t)
                frames_amp.append(fr.amp)
                frames_mode.append(fr.mode)
    t0 = samples[0]["ts"] / 1000.0
    t1 = samples[-1]["ts"] / 1000.0
    marks, _logged_alarms = load_marks(events_path)
    intervals = gt_intervals(marks)
    usable = [r for r in rows if r["o"] not in ("uncalibrated", "calibrating", "recording")]
    summary = {
        "durationS": round(t1 - t0, 1),
        "frames": engine.counters.get("total", 0),
        "accepted": engine.counters.get("accepted", 0),
        "ratePerS": round(engine.counters.get("accepted", 0) / max(1e-6, t1 - t0), 2),
        "backend": detected,
        "nfft": engine.nfft,
        "calibrated": engine.calibrated,
        "evaluations": len(usable),
        "motionActivePct": _pct([("motion" in r["a"]) for r in usable]),
        "staticActivePct": _pct([("static" in r["a"]) for r in usable]),
        "alarms": dict(collections.Counter(a["label"] for a in alarms)),
        "alarmsPerHour": round(len(alarms) * 3600.0 / max(1.0, t1 - t0), 2),
        "modes": engine.bank.summary()[:6],
        "groundTruth": event_metrics(intervals, alarms, t0, t1),
        "thresholds": (engine.calibration or {}).get("thresholds"),
    }
    result = {
        "pcap": Path(pcap_path).name,
        "t0": t0,
        "t1": t1,
        "summary": summary,
        "rows": rows,
        "alarms": alarms,
        "marks": marks,
        "intervals": [{"start": a, "end": b, "label": lab} for a, b, lab in intervals],
        "calibration": {k: calibration.get(k) for k in ("createdAt", "frames", "rate", "thresholds", "warnings")}
        if calibration else None,
    }
    result.update(spectral_analysis(frames_t, frames_amp, frames_mode, heat_cols, spec_cols, fs))
    return result


def _pct(flags):
    return round(100.0 * sum(flags) / len(flags), 2) if flags else None


def spectral_analysis(times, amps, modes, heat_cols=600, spec_cols=900, fs=20.0, nperseg=64, hop=8):
    """Birincil moddaki grup genliklerinden: isi haritasi, PC1 sinyali, STFT spektrogrami."""
    out = {"heatmap": None, "pc1": None, "spectrogram": None}
    if np is None or len(times) < 64:
        return out
    modes_arr = np.asarray(modes)
    vals, counts = np.unique(modes_arr[modes_arr >= 0], return_counts=True)
    if not len(vals):
        return out
    primary = vals[np.argmax(counts)]
    sel = modes_arr == primary
    t = np.asarray(times)[sel]
    X = np.asarray(amps, dtype=float)[sel]
    if len(t) < 64:
        return out
    # duzgun zaman izgarasina yeniden ornekleme
    grid = np.arange(t[0], t[-1], 1.0 / fs)
    if len(grid) < nperseg:
        return out
    Xu = np.empty((len(grid), X.shape[1]))
    for g in range(X.shape[1]):
        Xu[:, g] = np.interp(grid, t, X[:, g])
    # yavas drift'i bastir: 10 sn hareketli ortalama cikar (yuksek geciren)
    k = int(fs * 10)
    if len(grid) > k:
        # kenarda normalize edilen hareketli ortalama (sifir dolgusu kenar artefakti uretmesin)
        kernel = np.ones(k)
        norm = np.convolve(np.ones(len(grid)), kernel, mode="same")
        trend = np.vstack([np.convolve(Xu[:, g], kernel, mode="same") / norm for g in range(Xu.shape[1])]).T
        D = Xu - trend
    else:
        D = Xu - Xu.mean(axis=0)
    # isi haritasi (gruplar x zaman, sapma)
    step = max(1, len(grid) // heat_cols)
    H = D[: (len(grid) // step) * step].reshape(-1, step, D.shape[1]).mean(axis=1)
    out["heatmap"] = {
        "t": [round(float(x), 2) for x in grid[: H.shape[0] * step: step]],
        "z": np.round(H.T, 4).tolist(),
        "groups": int(D.shape[1]),
    }
    # PCA birinci bilesen (hareketin en guclu ortak dogrultusu)
    try:
        _u, s, vt = np.linalg.svd(D - D.mean(axis=0), full_matrices=False)
        pc1 = D @ vt[0]
        explained = float(s[0] ** 2 / max(1e-12, float(np.sum(s ** 2))))
    except np.linalg.LinAlgError:
        return out
    pstep = max(1, len(grid) // 4000)
    out["pc1"] = {
        "t": [round(float(x), 2) for x in grid[::pstep]],
        "x": np.round(pc1[::pstep], 4).tolist(),
        "explained": round(explained, 4),
        "fs": fs,
    }
    # STFT spektrogrami (Hann, dB)
    win = np.hanning(nperseg)
    starts = np.arange(0, len(pc1) - nperseg, hop)
    if len(starts) == 0:
        return out
    if len(starts) > spec_cols:
        starts = starts[np.linspace(0, len(starts) - 1, spec_cols).astype(int)]
    frames = np.stack([pc1[s0:s0 + nperseg] * win for s0 in starts])
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    db = 10.0 * np.log10(spec + 1e-9)
    freqs = np.fft.rfftfreq(nperseg, 1.0 / fs)
    out["spectrogram"] = {
        "t": [round(float(grid[s0 + nperseg // 2]), 2) for s0 in starts],
        "f": [round(float(f), 3) for f in freqs],
        "db": np.round(db.T, 1).tolist(),
        "nperseg": nperseg,
        "fs": fs,
    }
    return out
