#!/usr/bin/env python3
"""Tek kutu algilama motoru birim/senaryo testleri.

Calistirma:  python3 -m unittest tools/csi_sensing/test_sensing_engine.py -v
"""

import cmath
import math
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "device-files" / "csi-alpha-webui" / "webui"))

import csi_sensing as cs  # noqa: E402
from synth_csi import Scenario  # noqa: E402


def run(engine, samples, start_cal=None, cal_duration=None, profile=None, ticks=None):
    """Ornekleri motordan gecirir; olaylari (t_rel, event) listesi olarak dondurur."""
    events = []
    t0 = None
    started_cal = False
    started_prof = False
    last = None
    for sample in samples:
        t = sample["ts"] / 1000.0
        if t0 is None:
            t0 = t
        rel = t - t0
        if start_cal is not None and not started_cal and rel >= start_cal:
            engine.start_calibration(cal_duration, t=t)
            started_cal = True
        if profile and not started_prof and rel >= profile[1]:
            engine.start_profile(profile[0], profile[2], t=t)
            started_prof = True
        if ticks:
            while last is not None and t - last > 1.0:
                last += 1.0
                _info, evs = engine.tick(last)
                events.extend((last - t0, e) for e in evs)
        _info, evs = engine.push(sample)
        events.extend((rel, e) for e in evs)
        last = t
    return events, t0


def alarms(events, label=None):
    return [(t, e) for t, e in events if e["kind"] == "alarm" and not e.get("suppressed")
            and (label is None or e["label"] == label)]


class LayoutTests(unittest.TestCase):
    def test_valid_tone_counts(self):
        self.assertEqual(cs.ToneLayout(256, 56).valid_tones, 234)
        self.assertEqual(cs.ToneLayout(128, 56).valid_tones, 108)
        self.assertEqual(cs.ToneLayout(64, 56).valid_tones, 48)

    def test_null_tones_excluded(self):
        layout = cs.ToneLayout(256, 56)
        for bad in (0, 1, 255, 123, 127, 128, 133):
            self.assertNotIn(bad, layout.fft_index)

    def test_phase_sanitization_removes_sto_and_cpe(self):
        nfft = 256
        layout = cs.ToneLayout(nfft, 56)
        shape = {k: 0.8 * math.sin(k / 17.0) for k in range(-128, 128)}
        results = []
        for slope, cpe in ((0.0, 0.0), (0.45, 1.3), (-0.3, -2.5)):
            iq = bytearray()
            for idx in range(nfft):
                k = idx if idx < nfft // 2 else idx - nfft
                h = 800 * cmath.exp(1j * (shape[k] + slope * k + cpe))
                iq += struct.pack("<hh", int(h.real), int(h.imag))
            frame, reason = cs.extract_frame(bytes(iq), nfft, layout)
            self.assertIsNone(reason)
            results.append(frame.phase)
        for other in results[1:]:
            diff = [cs._wrap(a - b) for a, b in zip(results[0], other)]
            self.assertLess(max(abs(d) for d in diff), 0.05)

    def test_agc_gain_invariance(self):
        layout = cs.ToneLayout(64, 48)
        frames = []
        for gain in (300.0, 1500.0):
            iq = bytearray()
            for idx in range(64):
                k = idx if idx < 32 else idx - 64
                h = gain * (1.0 + 0.3 * math.cos(k / 5.0))
                iq += struct.pack("<hh", int(h), 0)
            frames.append(cs.extract_frame(bytes(iq), 64, layout)[0])
        diff = max(abs(a - b) for a, b in zip(frames[0].amp, frames[1].amp))
        self.assertLess(diff, 0.01)


class ConfigTests(unittest.TestCase):
    def test_bounds_and_types(self):
        cfg, errors = cs.normalize_config({"txRatePps": 9999, "motionOnK": 10, "motionOnN": 4,
                                           "scenario": "nope", "armed": "false", "unknownKey": 1})
        self.assertEqual(cfg["txRatePps"], 200.0)
        self.assertEqual(cfg["motionOnK"], 4)
        self.assertIn("scenario", errors)
        self.assertFalse(cfg["armed"])

    def test_scenarios(self):
        cargo = cs.default_config("vehicle_cargo")
        self.assertTrue(cargo["microEnabled"])
        self.assertEqual(cs.resolve_fc_filter("auto", "ax210_ba"), {0x94})
        self.assertEqual(cs.resolve_fc_filter("auto", "ax210_action"), {0xD0})
        self.assertEqual(cs.resolve_fc_filter("0x88,0x08", "ax210_ba"), {0x88, 0x08})
        self.assertEqual(cargo["staticAutoRelearnS"], 0)
        merged = cs.apply_scenario(cs.default_config("room"), "warehouse")
        self.assertEqual(merged["staticWindowS"], 8.0)
        self.assertEqual(merged["scenario"], "warehouse")


class GainStateTests(unittest.TestCase):
    """2026-11-09 gercek testinde hareketi tasiyan kanal: ayrik RSSI durumlari arasi gecis."""

    @staticmethod
    def frames(rssis):
        out = []
        for i, r in enumerate(rssis):
            f = cs.Frame(i * 0.02, [0.0], [1.0], [0.0], float(r), 100.0)
            out.append(f)
        return out

    def test_single_state_with_jitter_is_zero(self):
        self.assertEqual(cs.gain_state_share(self.frames([-24, -24, -25, -23] * 25)), 0.0)

    def test_minority_state_share(self):
        share = cs.gain_state_share(self.frames([-24] * 80 + [-20] * 20))
        self.assertAlmostEqual(share, 0.2)

    def test_far_outliers_and_missing_rssi_ignored(self):
        self.assertEqual(cs.gain_state_share(self.frames([-24] * 95 + [-53] * 5)), 0.0)
        self.assertIsNone(cs.gain_state_share(self.frames([0] * 100)))


class ResidualTests(unittest.TestCase):
    """Sizinti iptali: kare basina kazanc/faz/STO sicramalari istatistigi bozmamali; kucuk yansima degisimi yakalanmali."""

    @staticmethod
    def frames(seconds, change_at=None, seed=1):
        import random
        rnd = random.Random(seed)
        ks = [k for k in range(-26, 27) if 1 <= abs(k) <= 26 and abs(k) not in (7, 21)]
        leak = [complex(600 + 40 * math.cos(0.3 * k), 30 * math.sin(0.2 * k)) for k in ks]
        refl1 = [8 * cmath.exp(-1j * 2 * math.pi * k * 312.5e3 * 20e-9) for k in ks]   # ~-37 dB, 20 ns
        refl2 = [8 * cmath.exp(-1j * (2 * math.pi * k * 312.5e3 * 24e-9 + 2.0)) for k in ks]
        out = []
        for i in range(int(seconds * 50)):
            t = i / 50.0
            refl = refl2 if change_at is not None and t >= change_at else refl1
            g = rnd.choice([1.0, 0.986, 0.972, 0.63]) * cmath.exp(1j * rnd.uniform(-math.pi, math.pi))
            sto = rnd.gauss(0, 0.07)
            h = [g * (d + r) * cmath.exp(-1j * sto * k) + complex(rnd.gauss(0, 1.5), rnd.gauss(0, 1.5))
                 for d, r, k in zip(leak, refl, ks)]
            f = cs.Frame(t, [0.0], [1.0], [0.0], -19.0, 600.0)
            f.mode = 0
            acc = sum(h[j + 1] * h[j].conjugate() for j in range(len(ks) - 1) if ks[j + 1] - ks[j] == 1)
            sl = cmath.phase(acc)
            f.cvec = [x * cmath.exp(-1j * sl * k) for x, k in zip(h, ks)]
            out.append(f)
        return out

    def run_stat(self, frames, model):
        import csi_residual as cr
        det = cr.ResidualDetector(model, 3.0, 20.0, 30.0, 20.0)
        vals = []
        nxt = 0.5
        for f in frames:
            det.push(f.t, f.rssi, f.cvec)
            while f.t >= nxt:
                v = det.statistic(nxt)
                vals.append((nxt, v))
                nxt += 0.5
        return vals

    def test_gain_jumps_cancelled_and_change_detected(self):
        import csi_residual as cr
        if not cr.AVAILABLE:
            self.skipTest("numpy yok")
        model = cr.fit_model(self.frames(30, seed=2))
        self.assertLess(model["residualDb"], -30.0)
        quiet = [v for t, v in self.run_stat(self.frames(60, seed=3), model) if v is not None]
        self.assertLess(max(quiet), 3.0)                       # kazanc sicramalari (%37'ye kadar) alarm uretmez
        moved = self.run_stat(self.frames(60, change_at=40.0, seed=4), model)
        peak = max(v for t, v in moved if v is not None and 40.0 <= t <= 44.0)
        self.assertGreater(peak, 10.0)                         # -37 dB'lik yansima degisimi belirgin


class PcapTests(unittest.TestCase):
    def test_roundtrip(self):
        samples = list(Scenario(duration_s=3, pps=20, nfft=64, seed=3).samples())
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.pcap")
            cs.write_pcap(path, samples)
            back = list(cs.iter_pcap(path))
        self.assertEqual(len(back), len(samples))
        self.assertEqual(back[5]["iq"], samples[5]["iq"])
        self.assertEqual(back[5]["nfft"], 64)
        self.assertAlmostEqual(back[5]["ts"], samples[5]["ts"], delta=1.0)


class ScenarioTests(unittest.TestCase):
    def make_engine(self, **overrides):
        # sentetik uretec veri karelerini 0x88, cop kareleri 0x94 ile isaretler
        overrides.setdefault("fcFilter", "0x88")
        cfg = cs.normalize_config(overrides, base=cs.default_config("room"))[0]
        return cs.SensingEngine(cfg)

    def test_motion_detected_quickly_and_no_false_alarm_before(self):
        sc = Scenario(duration_s=150, pps=30, nfft=256, seed=7).add_motion(100, 115)
        engine = self.make_engine()
        events, _ = run(engine, sc.samples(), start_cal=8, cal_duration=60)
        self.assertTrue(any(e["kind"] == "calibration_done" for _, e in events))
        motion = alarms(events, "motion")
        self.assertTrue(motion, "hareket alarmi bekleniyordu")
        self.assertFalse([t for t, _ in alarms(events) if t < 99], "hareketten once alarm olmamali")
        latency = motion[0][0] - 100
        self.assertLess(latency, 4.0)

    def test_long_static_with_drift_modes_and_garbage_no_alarm(self):
        sc = Scenario(duration_s=900, pps=20, nfft=64, seed=11, drift_per_hour=0.3,
                      mode_b_share=0.3, garbage_share=0.25)
        engine = self.make_engine()
        events, _ = run(engine, sc.samples(), start_cal=8, cal_duration=90)
        self.assertEqual(alarms(events), [], "uzun statik kayitta yanlis alarm")

    def test_garbage_frames_without_fc_filter_are_isolated_by_mode_bank(self):
        sc = Scenario(duration_s=400, pps=25, nfft=64, seed=12, garbage_share=0.25)
        engine = self.make_engine(fcFilter="")
        events, _ = run(engine, sc.samples(), start_cal=8, cal_duration=90)
        self.assertEqual(alarms(events), [])
        self.assertGreaterEqual(len(engine.calibration["references"]), 2)

    def test_static_change_detected_and_persists(self):
        sc = Scenario(duration_s=200, pps=30, nfft=64, seed=5).add_step(130, "remove")
        engine = self.make_engine(staticAutoRelearnS=0)
        events, _ = run(engine, sc.samples(), start_cal=8, cal_duration=80)
        static = alarms(events, "static_change")
        self.assertTrue(static, "statik degisim alarmi bekleniyordu")
        self.assertGreater(static[0][0], 129)
        self.assertLess(static[0][0] - 130, 15.0)
        self.assertTrue(engine.det["static"].active, "statik alarm kalici olmali")
        self.assertFalse([t for t, _ in alarms(events) if t < 129])

    def test_accept_baseline_clears_static(self):
        sc = Scenario(duration_s=200, pps=30, nfft=64, seed=5).add_step(130, "remove")
        engine = self.make_engine(staticAutoRelearnS=20)
        events, _ = run(engine, sc.samples(), start_cal=8, cal_duration=80)
        self.assertTrue(any(e["kind"] == "info" and e["label"] == "baseline_relearned" for _, e in events))
        self.assertFalse(engine.det["static"].active)

    def test_signal_loss(self):
        sc = Scenario(duration_s=140, pps=30, nfft=64, seed=9).add_gap(100, 112)
        engine = self.make_engine()
        events, _ = run(engine, sc.samples(), start_cal=8, cal_duration=60, ticks=True)
        loss = alarms(events, "signal_loss")
        self.assertTrue(loss)
        self.assertGreaterEqual(loss[0][0], 104)
        self.assertLess(loss[0][0], 108)

    def test_rssi_drop(self):
        sc = Scenario(duration_s=140, pps=30, nfft=64, seed=19).add_rssi_drop(100, 130, 18)
        engine = self.make_engine(rssiDropEnabled=True)
        events, _ = run(engine, sc.samples(), start_cal=8, cal_duration=60)
        self.assertTrue(alarms(events, "rssi_drop"))

    def test_disarmed_suppresses_alarms(self):
        sc = Scenario(duration_s=130, pps=30, nfft=64, seed=7).add_motion(100, 115)
        engine = self.make_engine(armed=False)
        events, _ = run(engine, sc.samples(), start_cal=8, cal_duration=60)
        self.assertEqual(alarms(events), [])
        self.assertTrue([e for _, e in events if e["kind"] == "alarm" and e["suppressed"] == "disarmed"])

    def test_state_profiles_and_open_set(self):
        # 0-60 kapali (yansitici yol var), 60-120 acik (yol yok), 120-180 kapali, 180-240 bilinmeyen durum
        sc = Scenario(duration_s=240, pps=30, nfft=64, seed=21)
        closed_path = sc.static_paths[1]
        sc.add_step(60, "remove", closed_path)
        sc.add_step(120, "add", closed_path)
        sc.add_step(180, "add", (0.35, 41e-9, 2.0))
        cfg = cs.normalize_config({"stateAlarmLabels": "acik", "fcFilter": "0x88"}, base=cs.default_config("door"))[0]
        engine = cs.SensingEngine(cfg)
        plan = [(6, "cal", 20), (30, "kapali", 23), (66, "acik", 45)]
        done = set()
        events = []
        t0 = None
        for s in sc.samples():
            t = s["ts"] / 1000.0
            t0 = t if t0 is None else t0
            rel = t - t0
            for start, what, dur in plan:
                if rel >= start and start not in done:
                    done.add(start)
                    if what == "cal":
                        engine.start_calibration(dur, t=t)
                    else:
                        engine.start_profile(what, dur, t=t)
            _i, evs = engine.push(s)
            events.extend((rel, e) for e in evs)
        self.assertIn("kapali", engine.profiles)
        self.assertIn("acik", engine.profiles)
        states = [(t, e["label"]) for t, e in events if e["kind"] == "state"]
        self.assertIn("acik", [lab for t, lab in states if 111 < t < 120])
        self.assertIn("kapali", [lab for t, lab in states if 120 < t < 178])
        self.assertIn("unknown", [lab for t, lab in states if t > 180])
        self.assertTrue(alarms(events, "state_acik"), "acik durumuna gecis alarm uretmeli")

    def test_micro_motion_breathing(self):
        sc = Scenario(duration_s=260, pps=25, nfft=64, seed=31).add_breathing(150, 260, amp=0.12, depth_m=0.008)
        engine = self.make_engine(microEnabled=True)
        events, _ = run(engine, sc.samples(), start_cal=8, cal_duration=100)
        micro = alarms(events, "micro_motion")
        self.assertFalse([t for t, _ in alarms(events) if t < 149], "nefesten once alarm olmamali")
        self.assertTrue(micro, "mikro-hareket (solunum) alarmi bekleniyordu")


if __name__ == "__main__":
    unittest.main()
