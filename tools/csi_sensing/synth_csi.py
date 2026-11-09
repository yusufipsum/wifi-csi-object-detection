#!/usr/bin/env python3
"""Tek kutu (quasi-monostatik) senaryo icin fiziksel modele dayali sentetik Nexmon CSI ureteci.

Kanal modeli (her alt tasiyici k, frekans f_k = k * 312.5 kHz):

    H_k(t) = g(t) * S_m(k) * [ sum_p a_p exp(-j 2 pi f_k tau_p(t)) ] * exp(j(s(t) k + c(t))) + n_k(t)

  * p = 0           : TX->RX dogrudan sizinti (ayni kutu, guclu, kisa gecikme)
  * p = 1..P        : statik yansiticilar (duvar, kasa, branda ...)
  * hareketli yol   : tau(t) = tau0 + 2 d(t) / c, d(t) rastgele yuruyus (motion araliklarinda)
  * solunum yolu    : tau(t) = tau0 + 2 (A sin(2 pi f_b t)) / c
  * g(t)            : AGC kazanci (kare basina rastgele)
  * S_m(k)          : PHY moduna bagli spektral sekillendirme (gercek veride gozlenen coklu mod)
  * s(t), c(t)      : STO egimi ve ortak faz (kare basina rastgele) -> on-isleme temizlemeli
  * drift           : yavas termal kazanc/sekil kaymasi

Bu ureteci hem birim testleri hem de belgelerdeki davranis gosterimleri kullanir.
Gercek donanim sonuclarinin yerine GECMEZ.
"""

import cmath
import math
import random
import struct

C = 299792458.0
DF = 312.5e3
FC = 5.24e9


class Scenario:
    def __init__(self, duration_s=120.0, pps=40.0, nfft=256, seed=1, snr_db=34.0,
                 leak_amp=1.0, mode_b_share=0.25, garbage_share=0.15, drift_per_hour=0.15,
                 agc_db=1.5, source_mac="ac:45:ef:1f:50:36", rssi=-38):
        self.duration_s = duration_s
        self.pps = pps
        self.nfft = nfft
        self.rng = random.Random(seed)
        self.snr_db = snr_db
        self.leak_amp = leak_amp
        self.mode_b_share = mode_b_share
        self.garbage_share = garbage_share
        self.drift_per_hour = drift_per_hour
        self.agc_db = agc_db
        self.source_mac = source_mac
        self.rssi = rssi
        rng = random.Random(seed + 101)
        self.static_paths = [(rng.uniform(0.05, 0.3), rng.uniform(8e-9, 60e-9), rng.uniform(0, 2 * math.pi))
                             for _ in range(5)]
        self.motion = []        # (t0, t1, amp, tau0)
        self.steps = []         # (t, kind, (amp, tau, phase)) kind: add/remove
        self.breathing = []     # (t0, t1, amp, tau0, freq_hz, depth_m)
        self.gaps = []          # (t0, t1) kare yok
        self.rssi_steps = []    # (t0, t1, delta_db)

    def add_motion(self, t0, t1, amp=0.10, tau0=20e-9):
        self.motion.append((t0, t1, amp, tau0))
        return self

    def add_step(self, t, kind="remove", path=None):
        if kind == "remove" and path is None:
            path = self.static_paths[1]
        if kind == "add" and path is None:
            path = (0.2, 33e-9, 1.1)
        self.steps.append((t, kind, path))
        return self

    def add_breathing(self, t0, t1, amp=0.08, tau0=15e-9, freq_hz=0.25, depth_m=0.006):
        self.breathing.append((t0, t1, amp, tau0, freq_hz, depth_m))
        return self

    def add_gap(self, t0, t1):
        self.gaps.append((t0, t1))
        return self

    def add_rssi_drop(self, t0, t1, delta_db=15.0):
        self.rssi_steps.append((t0, t1, delta_db))
        return self

    def _paths_at(self, t):
        paths = list(self.static_paths)
        for ts, kind, path in sorted(self.steps):
            if t >= ts:
                if kind == "remove" and path in paths:
                    paths.remove(path)
                elif kind == "add":
                    paths.append(path)
        return paths

    def samples(self, t_start=1_700_000_000.0):
        rng = self.rng
        nfft = self.nfft
        half = nfft // 2
        ks = list(range(nfft))
        k_signed = [k if k < half else k - nfft for k in ks]
        noise_sigma = self.leak_amp * 10 ** (-self.snr_db / 20.0)
        walkers = {}
        static_cache = {}
        t = 0.0
        seq = 0
        dt_mean = 1.0 / self.pps
        while t < self.duration_s:
            t += rng.expovariate(1.0 / dt_mean) * 0.5 + dt_mean * 0.5
            if any(a <= t < b for a, b in self.gaps):
                continue
            seq += 1
            garbage = rng.random() < self.garbage_share
            fc = 0x94 if garbage else 0x88
            paths = self._paths_at(t)
            dyn = []
            for idx, (a, b, amp, tau0) in enumerate(self.motion):
                if a <= t < b:
                    d = walkers.get(idx, 0.0) + rng.gauss(0.0, 0.02)
                    walkers[idx] = d
                    dyn.append((amp, tau0 + 2.0 * d / C, 0.0))
            for (a, b, amp, tau0, fb, depth) in self.breathing:
                if a <= t < b:
                    d = depth * math.sin(2.0 * math.pi * fb * t)
                    dyn.append((amp, tau0 + 2.0 * d / C, 0.4))
            drift = self.drift_per_hour * (t / 3600.0)
            gain = 10 ** (rng.gauss(0.0, self.agc_db / 3.0) / 20.0) * 900.0
            slope = rng.uniform(-0.6, 0.6)
            cpe = rng.uniform(-math.pi, math.pi)
            mode_b = rng.random() < self.mode_b_share
            key = tuple(paths)
            static_sum = static_cache.get(key)
            if static_sum is None:
                static_sum = []
                for k in k_signed:
                    f = k * DF
                    h = self.leak_amp * cmath.exp(-2j * math.pi * f * 1.0e-9)
                    for amp, tau, ph in paths:
                        h += amp * cmath.exp(-2j * math.pi * f * tau + 1j * ph)
                    static_sum.append(h)
                static_cache[key] = static_sum
            out = []
            for k, h0 in zip(k_signed, static_sum):
                f = k * DF
                h = h0
                for amp, tau, ph in dyn:
                    # hareketli yollarda tasiyici terimi belirleyici: faz = -2 pi (f_c + f_k) tau(t)
                    h += amp * cmath.exp(-2j * math.pi * (FC + f) * tau + 1j * ph)
                shape = 1.0 + drift * math.sin(2 * math.pi * k / 90.0)
                if mode_b:
                    shape *= 1.0 + 0.45 * math.cos(2 * math.pi * k / 37.0)
                if garbage:
                    shape *= 1.0 + 0.9 * math.sin(2 * math.pi * k / 11.0 + 0.7)
                h = h * shape * cmath.exp(1j * (slope * k + cpe))
                h += complex(rng.gauss(0, noise_sigma), rng.gauss(0, noise_sigma))
                out.append(h * gain)
            iq = bytearray()
            for idx, h in enumerate(out):
                k = k_signed[idx]
                if k == 0 or abs(k) >= half - 5 or (abs(k) <= 1):
                    # Nexmon'da gozlenen cop degerler
                    re, im = rng.randint(-30000, 30000), rng.randint(-30000, 30000)
                else:
                    re = max(-32768, min(32767, int(round(h.real))))
                    im = max(-32768, min(32767, int(round(h.imag))))
                iq += struct.pack("<hh", re, im)
            rssi = self.rssi
            for a, b, delta in self.rssi_steps:
                if a <= t < b:
                    rssi -= delta
            yield {
                "ts": (t_start + t) * 1000.0,
                "rssi": int(rssi + rng.gauss(0, 1)),
                "frameControl": fc,
                "sourceMac": self.source_mac,
                "seq": seq,
                "nfft": nfft,
                "iq": bytes(iq),
            }


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "device-files" / "csi-alpha-webui" / "webui"))
    import csi_sensing as cs

    ap = argparse.ArgumentParser(description="Sentetik tek-kutu Nexmon pcap uret")
    ap.add_argument("out")
    ap.add_argument("--duration", type=float, default=180)
    ap.add_argument("--pps", type=float, default=40)
    ap.add_argument("--motion", action="append", default=[], help="bas:bit")
    ap.add_argument("--remove-path-at", type=float)
    ap.add_argument("--breathing", action="append", default=[], help="bas:bit")
    args = ap.parse_args()
    sc = Scenario(args.duration, args.pps)
    for item in args.motion:
        a, b = item.split(":")
        sc.add_motion(float(a), float(b))
    for item in args.breathing:
        a, b = item.split(":")
        sc.add_breathing(float(a), float(b))
    if args.remove_path_at is not None:
        sc.add_step(args.remove_path_at, "remove")
    cs.write_pcap(args.out, sc.samples())
    print(args.out)
