#!/usr/bin/env python3
"""Tek kutu algilama motorunu pcap kayitlari uzerinde yeniden oynatir ve olcer.

Canli servis (csi_web.py) ile AYNI motor kodu (csi_sensing.py) kullanilir; boylece
tezde raporlanan sayilar sahada calisan algoritmanin sayilaridir.

Ornek:
  python tools/csi_sensing/replay_eval.py \
    --calib data/csi/captures_phase_20260530/csi-alpha-web-20260529-235132.pcap@0:300 \
    --session empty=data/csi/captures_phase_20260530/csi-alpha-web-20260529-235132.pcap@300: \
    --session passage=data/csi/captures_phase_20260530/csi-alpha-web-20260530-002230.pcap \
    --negative empty --preset room \
    --json data/csi/reports/single_box_eval.json --md data/csi/reports/single_box_eval.md

Segment sozdizimi:  yol.pcap@BASLANGIC:BITIS  (saniye, dosya basina gore; bos = sinir yok)

Yer gercegi (opsiyonel): yol.pcap.events.ndjson (panelin "olay isaretle" kayitlari) veya
--gt yol.pcap=BAS:BIT,BAS:BIT (dosya basina gore saniye) verilirse olay-seviyesi
yakalama orani ve tespit gecikmesi hesaplanir.
"""

import argparse
import collections
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "device-files" / "csi-alpha-webui" / "webui"))

import csi_sensing as cs  # noqa: E402

DETECTORS = ("motion", "static", "micro")


def parse_segment(text):
    path, _, rng = text.partition("@")
    start = end = None
    if rng:
        a, _, b = rng.partition(":")
        start = float(a) if a.strip() else None
        end = float(b) if b.strip() else None
    return path, start, end


def parse_set(values):
    out = {}
    for item in values or []:
        key, _, value = item.partition("=")
        out[key.strip()] = value.strip()
    return out


def detect_backend(path, limit=600):
    """pcap icerigine gore TX yontemi: cogunluk BlockAck (0x94) ise ax210_ba, degilse ap_udp."""
    counts = collections.Counter()
    for i, sample in enumerate(cs.iter_pcap(path)):
        counts[sample.get("frameControl")] += 1
        if i >= limit:
            break
    if not counts:
        return "ap_udp"
    data = counts.get(0x88, 0) + counts.get(0x08, 0)
    best = max((counts.get(0xD0, 0), "ax210_action"), (counts.get(0x94, 0), "ax210_ba"), (data, "ap_udp"))
    return best[1] if best[0] > 0 else "ap_udp"


def load_samples(path, start, end):
    return list(cs.iter_pcap(path, start, end))


def calibrate(config, path, start, end, log):
    samples = load_samples(path, start, end)
    if not samples:
        raise SystemExit(f"kalibrasyon segmenti bos: {path}")
    engine = cs.SensingEngine(config, meta={"source": os.path.basename(path)})
    t0 = samples[0]["ts"] / 1000.0
    t_end = samples[-1]["ts"] / 1000.0
    warm = max(config["motionWindowS"], config["staticWindowS"]) + 1.5
    duration = (t_end - t0) - warm - 1.0
    if duration < 10:
        raise SystemExit("kalibrasyon segmenti cok kisa (>= ~15 sn gerekli)")
    started = False
    result = None
    for sample in samples:
        t = sample["ts"] / 1000.0
        if not started and t - t0 >= warm:
            engine.start_calibration(duration, t=t)
            started = True
        _info, events = engine.push(sample)
        for ev in events:
            if ev["kind"] == "calibration_done":
                result = ev["calibration"]
            elif ev["kind"] == "calibration_failed":
                raise SystemExit(f"kalibrasyon basarisiz: {ev.get('reason')}")
    if result is None:
        _info, events = engine.tick(t_end + duration)
        for ev in events:
            if ev["kind"] == "calibration_done":
                result = ev["calibration"]
    if result is None:
        raise SystemExit("kalibrasyon tamamlanamadi")
    log(f"kalibrasyon: {result['frames']} kare, {result['rate']} kare/sn, "
        f"{len(result['references'])} mod, esikler={result['thresholds']}")
    for w in result.get("warnings") or []:
        log(f"  uyari: {w}")
    return result


def load_gt(path, explicit):
    """Yer gercegi araliklari (dosya basina gore saniye ya da mutlak ms)."""
    intervals = []
    if explicit:
        for part in explicit.split(","):
            a, _, b = part.partition(":")
            intervals.append({"start": float(a), "end": float(b), "label": "gt", "relative": True})
        return intervals
    events_path = Path(str(path) + ".events.ndjson")
    if not events_path.exists():
        alt = Path(str(path).replace(".pcap", ".events.ndjson"))
        events_path = alt if alt.exists() else None
    if not events_path:
        return intervals
    open_marks = {}
    with open(events_path, "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            label = row.get("label") or "olay"
            ts = float(row.get("ts") or 0) / 1000.0
            kind = row.get("kind")
            if kind == "start":
                open_marks[label] = ts
            elif kind == "end" and label in open_marks:
                intervals.append({"start": open_marks.pop(label), "end": ts, "label": label, "relative": False})
            elif kind == "instant":
                intervals.append({"start": ts, "end": ts + 3.0, "label": label, "relative": False})
    return intervals


def run_session(config, calibration, profiles, label, path, start, end, gt_explicit=None, timeline=None):
    samples = load_samples(path, start, end)
    if not samples:
        return None
    engine = cs.SensingEngine(config, calibration=calibration, profiles=profiles,
                              meta={"source": os.path.basename(path)})
    first_file_t = None
    for s in cs.iter_pcap(path):
        first_file_t = s["ts"] / 1000.0
        break
    infos = []
    alarms = []
    suppressed = collections.Counter()
    for sample in samples:
        info, events = engine.push(sample)
        if info is not None:
            infos.append(info)
        for ev in events:
            if ev["kind"] == "alarm":
                if ev.get("suppressed"):
                    suppressed[ev["suppressed"]] += 1
                else:
                    alarms.append(ev)
    t0 = samples[0]["ts"] / 1000.0
    t1 = samples[-1]["ts"] / 1000.0
    duration = max(1e-6, t1 - t0)
    usable = [i for i in infos if i["overall"] not in ("uncalibrated", "calibrating", "recording")
              and not (i.get("signal") or {}).get("rate") is None]
    active_frac = {}
    for name in DETECTORS:
        vals = [bool(((i.get("detectors") or {}).get(name) or {}).get("active")) for i in usable]
        active_frac[name] = (sum(vals) / len(vals)) if vals else None
    alarm_counts = collections.Counter(a["label"] for a in alarms)
    motion_values = [((i.get("detectors") or {}).get("motion") or {}).get("value") for i in usable]
    motion_scores = [((i.get("detectors") or {}).get("motion") or {}).get("score") for i in usable]
    static_scores = [((i.get("detectors") or {}).get("static") or {}).get("score") for i in usable]
    result = {
        "label": label,
        "pcap": str(path),
        "segment": [start, end],
        "durationS": round(duration, 1),
        "evaluations": len(usable),
        "frames": engine.counters.get("total", 0),
        "accepted": engine.counters.get("accepted", 0),
        "acceptRatio": round(engine.counters.get("accepted", 0) / max(1, engine.counters.get("total", 0)), 3),
        "rejected": {k: v for k, v in engine.counters.items() if k.startswith("rejected")},
        "activeFraction": {k: (round(v, 4) if v is not None else None) for k, v in active_frac.items()},
        "alarms": dict(alarm_counts),
        "alarmsPerHour": {k: round(v * 3600.0 / duration, 2) for k, v in alarm_counts.items()},
        "suppressed": dict(suppressed),
        "motionValue": _dist([v for v in motion_values if v is not None]),
        "motionScore": _dist([v for v in motion_scores if v is not None]),
        "staticScore": _dist([v for v in static_scores if v is not None]),
        "alarmTimesS": [round(a["t"] - (first_file_t or t0), 1) for a in alarms][:200],
        "_motionValues": [v for v in motion_values if v is not None],
        "_motionScores": [v if v is not None else 0.0 for v in motion_scores],
        "_times": [i["t"] for i in usable],
    }
    gt = load_gt(path, gt_explicit)
    if gt:
        result["groundTruth"] = event_metrics(gt, alarms, usable, first_file_t or t0, t0, t1)
    if timeline:
        Path(timeline).mkdir(parents=True, exist_ok=True)
        out = Path(timeline) / (Path(path).stem + f"_{label}.csv")
        with open(out, "w", encoding="utf-8") as handle:
            handle.write("t_rel_s,overall,motion_value,motion_score,motion_active,static_score,static_active,"
                         "micro_score,micro_active,rate,state\n")
            for i in usable:
                d = i.get("detectors") or {}
                handle.write(",".join(str(x) for x in [
                    round(i["t"] - (first_file_t or t0), 2), i["overall"],
                    _fmt(d.get("motion", {}).get("value")), _fmt(d.get("motion", {}).get("score")),
                    int(bool(d.get("motion", {}).get("active"))),
                    _fmt(d.get("static", {}).get("score")), int(bool(d.get("static", {}).get("active"))),
                    _fmt(d.get("micro", {}).get("score")), int(bool(d.get("micro", {}).get("active"))),
                    _fmt((i.get("signal") or {}).get("rate")), (d.get("state") or {}).get("stable") or "",
                ]) + "\n")
        result["timelineCsv"] = str(out)
    return result


def event_metrics(gt, alarms, infos, file_t0, t0, t1):
    """Olay-seviyesi: her GT araligi icin [bas-1, bit+5] sn icinde alarm var mi, gecikme; aralik disi alarmlar = yanlis."""
    intervals = []
    for g in gt:
        a = g["start"] + (file_t0 if g.get("relative") else 0.0)
        b = g["end"] + (file_t0 if g.get("relative") else 0.0)
        if b < t0 or a > t1:
            continue
        intervals.append((a, b, g.get("label")))
    hits = 0
    latencies = []
    for a, b, _label in intervals:
        inside = [al["t"] for al in alarms if a - 1.0 <= al["t"] <= b + 5.0]
        if inside:
            hits += 1
            latencies.append(max(0.0, min(inside) - a))
    false_alarms = [al for al in alarms if not any(a - 1.0 <= al["t"] <= b + 5.0 for a, b, _ in intervals)]
    gt_time = sum(b - a for a, b, _ in intervals)
    neg_hours = max(1e-6, (t1 - t0 - gt_time) / 3600.0)
    return {
        "events": len(intervals),
        "detected": hits,
        "recall": round(hits / len(intervals), 4) if intervals else None,
        "latencyS": _dist(latencies),
        "falseAlarms": len(false_alarms),
        "falseAlarmsPerHour": round(len(false_alarms) / neg_hours, 2),
    }


def _fmt(v):
    return "" if v is None else f"{v:.6g}"


def _dist(values):
    if not values:
        return None
    return {
        "n": len(values),
        "p50": round(cs._percentile(values, 50), 6),
        "p90": round(cs._percentile(values, 90), 6),
        "p99": round(cs._percentile(values, 99), 6),
        "max": round(max(values), 6),
    }


def auc(neg, pos):
    """Mann-Whitney U ile ROC-AUC."""
    if not neg or not pos:
        return None
    allv = sorted([(v, 0) for v in neg] + [(v, 1) for v in pos])
    ranks = [0.0] * len(allv)
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = r
        i = j + 1
    r_pos = sum(r for r, (_, lab) in zip(ranks, allv) if lab == 1)
    n1 = len(pos)
    n0 = len(neg)
    return (r_pos - n1 * (n1 + 1) / 2.0) / (n0 * n1)


def threshold_sweep(neg_sessions, pos_sessions, scales=(0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0)):
    """Esik carpanina gore pencere-seviyesi yanlis pozitif / aktif oran (ROC noktalari)."""
    rows = []
    neg = [s for r in neg_sessions for s in r["_motionScores"]]
    pos = [s for r in pos_sessions for s in r["_motionScores"]]
    for scale in scales:
        fp = sum(1 for s in neg if s >= scale) / len(neg) if neg else None
        tp = sum(1 for s in pos if s >= scale) / len(pos) if pos else None
        rows.append({"scale": scale, "negWindowRate": _r(fp), "posWindowRate": _r(tp)})
    return rows


def _r(v):
    return None if v is None else round(v, 4)


def markdown(report):
    lines = []
    lines.append(f"# Tek Kutu Algılama — Replay Değerlendirmesi ({report['createdAt']})")
    lines.append("")
    lines.append(f"Motor: `{report['engine']}` · senaryo: `{report['config']['scenario']}` · "
                 f"kalibrasyon: `{report['calibration']['source']}` ({report['calibration']['durationS']} sn, "
                 f"{report['calibration']['frames']} kare, {report['calibration']['modes']} PHY modu)")
    lines.append("")
    lines.append("Eşikler (kalibrasyondan, ölçek çarpanı uygulanmadan): `"
                 + json.dumps(report["calibration"]["thresholds"]) + "`")
    lines.append("")
    lines.append("| Oturum | Etiket | Süre (sn) | Kabul | Hareket aktif % | Statik aktif % | Alarm (adet) | Alarm/saat | Hareket değeri p50 / p99 |")
    lines.append("|---|---|---:|---:|---:|---:|---|---|---|")
    for s in report["sessions"]:
        af = s["activeFraction"]
        mv = s.get("motionValue") or {}
        lines.append(
            f"| `{Path(s['pcap']).name}` {_seg(s['segment'])} | {s['label']} | {s['durationS']} | "
            f"{s['acceptRatio']:.2f} | {_pct(af.get('motion'))} | {_pct(af.get('static'))} | "
            f"{_kv(s['alarms'])} | {_kv(s['alarmsPerHour'])} | {mv.get('p50', '--')} / {mv.get('p99', '--')} |")
    lines.append("")
    summ = report.get("summary") or {}
    if summ:
        lines.append("## Özet")
        lines.append("")
        for key, value in summ.items():
            lines.append(f"- **{key}**: {value}")
        lines.append("")
    if report.get("sweep"):
        lines.append("## Eşik çarpanı taraması (pencere seviyesi, hareket skoru)")
        lines.append("")
        lines.append("| motionScale | Negatif oturum pencere oranı | Pozitif oturum pencere oranı |")
        lines.append("|---:|---:|---:|")
        for row in report["sweep"]:
            lines.append(f"| {row['scale']} | {_pct(row['negWindowRate'])} | {_pct(row['posWindowRate'])} |")
        lines.append("")
    gts = [s for s in report["sessions"] if s.get("groundTruth")]
    if gts:
        lines.append("## Yer gerçeği (işaretlenmiş olaylar)")
        lines.append("")
        lines.append("| Oturum | Olay | Yakalanan | Recall | Gecikme p50 / p90 (sn) | Yanlış alarm | YA/saat |")
        lines.append("|---|---:|---:|---:|---|---:|---:|")
        for s in gts:
            g = s["groundTruth"]
            lat = g.get("latencyS") or {}
            lines.append(f"| `{Path(s['pcap']).name}` | {g['events']} | {g['detected']} | {g['recall']} | "
                         f"{lat.get('p50', '--')} / {lat.get('p90', '--')} | {g['falseAlarms']} | {g['falseAlarmsPerHour']} |")
        lines.append("")
    lines.append("Notlar:")
    lines.append("")
    lines.append("- 'Aktif %' pencere (karar) seviyesidir; pozitif etiketli oturumlar boşta geçen aralar da içerdiği "
                 "için %100 olmaları beklenmez (oturum-seviyesi etiket gürültüsü).")
    lines.append("- AUC, oturum etiketleriyle pencere seviyesinde hesaplanır; aynı nedenle alt sınır niteliğindedir.")
    return "\n".join(lines) + "\n"


def _seg(seg):
    a, b = seg
    if a is None and b is None:
        return ""
    return f"@{'' if a is None else int(a)}:{'' if b is None else int(b)}"


def _pct(v):
    return "--" if v is None else f"{v * 100:.1f}"


def _kv(d):
    return ", ".join(f"{k}: {v}" for k, v in sorted(d.items())) or "0"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calib", required=False, help="kalibrasyon segmenti yol.pcap@bas:bit")
    ap.add_argument("--calibration-json", help="hazir kalibrasyon dosyasi (sensing_calibration.json)")
    ap.add_argument("--profiles-json", help="durum profilleri dosyasi (sensing_profiles.json)")
    ap.add_argument("--session", action="append", default=[], help="etiket=yol.pcap@bas:bit")
    ap.add_argument("--negative", action="append", default=[], help="normal (alarm olmamali) etiketler")
    ap.add_argument("--gt", action="append", default=[], help="yol.pcap=bas:bit,bas:bit (yer gercegi)")
    ap.add_argument("--preset", default="room", choices=list(cs.SCENARIOS.keys()))
    ap.add_argument("--set", action="append", default=[], help="config anahtar=deger")
    ap.add_argument("--json", help="JSON rapor yolu")
    ap.add_argument("--md", help="Markdown rapor yolu")
    ap.add_argument("--timeline-dir", help="oturum basina karar zaman cizelgesi CSV klasoru")
    ap.add_argument("--save-calibration", help="uretilen kalibrasyonu bu yola yaz")
    args = ap.parse_args()

    def log(msg):
        print(msg, file=sys.stderr, flush=True)

    overrides = parse_set(args.set)
    if "txBackend" not in overrides:
        first = parse_segment(args.calib)[0] if args.calib else parse_segment(args.session[0].partition("=")[2])[0]
        overrides["txBackend"] = detect_backend(first)
        log(f"TX yontemi (pcap'ten): {overrides['txBackend']}")
    config = cs.normalize_config(overrides, base=cs.default_config(args.preset))[0]
    if args.calibration_json:
        calibration = cs.load_json(args.calibration_json)
        cal_source = args.calibration_json
    elif args.calib:
        path, a, b = parse_segment(args.calib)
        calibration = calibrate(config, path, a, b, log)
        cal_source = args.calib
    else:
        raise SystemExit("--calib veya --calibration-json gerekli")
    if args.save_calibration:
        Path(args.save_calibration).write_text(json.dumps(calibration, indent=1) + "\n", encoding="utf-8")
    profiles = cs.load_json(args.profiles_json, {}) if args.profiles_json else {}
    gt_map = {}
    for item in args.gt:
        p, _, rng = item.partition("=")
        gt_map[os.path.abspath(p)] = rng

    sessions = []
    for item in args.session:
        label, _, seg = item.partition("=")
        path, a, b = parse_segment(seg)
        started = time.time()
        res = run_session(config, calibration, profiles, label, path, a, b,
                          gt_map.get(os.path.abspath(path)), args.timeline_dir)
        if res is None:
            log(f"atlandi (bos): {item}")
            continue
        log(f"{label:10s} {Path(path).name} {res['durationS']:7.1f}s  hareket aktif "
            f"{_pct(res['activeFraction']['motion'])}%  statik aktif {_pct(res['activeFraction']['static'])}%  "
            f"alarm {res['alarms']}  ({time.time() - started:.1f}s)")
        sessions.append(res)

    neg = [s for s in sessions if s["label"] in args.negative]
    pos = [s for s in sessions if s["label"] not in args.negative]
    summary = {}
    if neg:
        hours = sum(s["durationS"] for s in neg) / 3600.0
        motion_alarms = sum(s["alarms"].get("motion", 0) for s in neg)
        all_alarms = sum(sum(s["alarms"].values()) for s in neg)
        summary["Negatif (normal) süre"] = f"{hours * 60:.1f} dk"
        summary["Negatif oturumlarda hareket alarmı"] = f"{motion_alarms} ({motion_alarms / max(hours, 1e-6):.2f}/saat)"
        summary["Negatif oturumlarda tüm alarmlar"] = f"{all_alarms} ({all_alarms / max(hours, 1e-6):.2f}/saat)"
        w = [s["activeFraction"]["motion"] for s in neg if s["activeFraction"]["motion"] is not None]
        summary["Negatif pencere yanlış-aktif oranı (hareket)"] = f"%{100 * sum(w) / len(w):.2f}" if w else "--"
    if pos:
        w = [s["activeFraction"]["motion"] for s in pos if s["activeFraction"]["motion"] is not None]
        summary["Pozitif oturum pencere aktif oranı (hareket, ort.)"] = f"%{100 * sum(w) / len(w):.1f}" if w else "--"
        detected = sum(1 for s in pos if s["alarms"].get("motion", 0) > 0)
        summary["Hareket alarmı üreten pozitif oturum"] = f"{detected}/{len(pos)}"
    if neg and pos:
        a = auc([v for s in neg for v in s["_motionValues"]], [v for s in pos for v in s["_motionValues"]])
        summary["Pencere-seviyesi ROC-AUC (hareket değeri, oturum etiketleri)"] = f"{a:.4f}" if a is not None else "--"
    report = {
        "createdAt": time.strftime("%Y-%m-%d %H:%M"),
        "engine": cs.ENGINE_VERSION,
        "config": config,
        "calibration": {
            "source": cal_source,
            "durationS": calibration.get("durationS"),
            "frames": calibration.get("frames"),
            "modes": len(calibration.get("references") or []),
            "thresholds": calibration.get("thresholds"),
            "stats": calibration.get("stats"),
            "warnings": calibration.get("warnings"),
        },
        "sessions": [{k: v for k, v in s.items() if not k.startswith("_")} for s in sessions],
        "summary": summary,
        "sweep": threshold_sweep(neg, pos) if neg and pos else None,
    }
    for key, value in summary.items():
        log(f"{key}: {value}")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(markdown(report), encoding="utf-8")
    if not args.json and not args.md:
        print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
