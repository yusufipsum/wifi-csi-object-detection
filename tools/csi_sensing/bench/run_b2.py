"""B2: onceki motor (mod ici sekil std + novelty), gercek motor replay'i; T2 ve G kapali."""
import json, sys, time, numpy as np
sys.path.insert(0, '/Users/lordsploit/Documents/msc/device-files/csi-alpha-webui/webui')
import csi_timeseries as TS, csi_sensing as S
from bench import gt_pairs, cal_done, episodes
from bench_sessions import SESS, GROUP, P

base = dict(json.load(open('cfg_live.json')))
base.update(residualEnabled=False, gainStateEnabled=False, txBackend='ax210_action')
out = {}
samples = {}
def samp(tag):
    if tag not in samples:
        samples[tag] = list(S.iter_pcap(P(tag) + '.pcap'))
    return samples[tag]

for scale in [0.5, 1.0, 2.0]:
    cfg = S.normalize_config(dict(base, motionScale=scale))[0]
    cals = {}
    for sname in ['dev', 'S2', 'S3', 'S4']:
        s = SESS[sname]; t0 = time.time()
        if s['cal_from']:
            cal = cals[s['cal_from']]
        else:
            sm = samp(s['tag']); first = sm[0]['ts'] / 1000
            cal = TS._calibrate(cfg, sm, s['ce'] - s['cal_s'] - first, s['ce'] - first)
            cals[sname] = cal
        r = TS.analyze_capture(s['pcap'], cfg, calibration=cal, max_seconds=6000)
        E = np.array([x['t'] for x in r['rows']]); on = np.array(['motion' in (x.get('a') or []) for x in r['rows']])
        np.savez(f'stat_B2x{scale}_{sname}.npz', E=E, on=on)
        eps = episodes(E, on); res = {}; fa = 0; emin = 0.0
        for a, b, lab in s['pairs']:
            g = GROUP.get(lab)
            if g is None: continue
            if g == 'bos':
                fa += sum(1 for x in eps if a + 5 <= x[0] <= b); emin += (b - a - 5) / 60
            else:
                res.setdefault(g, []).append(any(x[0] <= b + 5 and x[1] >= a - 1 for x in eps))
        out.setdefault(str(scale), {})[sname] = dict(res={k: [int(sum(v)), len(v)] for k, v in res.items()}, fa=fa, empty_min=round(emin, 2))
        print('B2 scale', scale, sname, out[str(scale)][sname], f'({time.time()-t0:.0f}s)', flush=True)
json.dump(out, open('bench_b2.json', 'w'), indent=1)
