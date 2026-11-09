import json, sys, time, numpy as np
from bench import *
from bench_sessions import SESS, GROUP, P

METHODS = {
    'B1 genlik varyansi':            ('fn', amp_variance),
    'B3 arka plan cikarma':          ('t2', dict(proj='norm', perstate=False, white='scalar', retry=False)),
    'B4 ortak guc degisimi':         ('fn', common_power),
    'T2 (tam)':                      ('t2', dict()),
    'A1 -LS (normalizasyon)':        ('t2', dict(proj='norm')),
    'A2 -kovaryans (skaler)':        ('t2', dict(white='scalar')),
    'A3 -durum ayrimi':              ('t2', dict(perstate=False)),
    'A4 -yeniden gonderim suzme':    ('t2', dict(retry=False)),
}
MULTS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]

def evaluate(E, stat, thr, pairs, mult):
    on = kofn(np.nan_to_num(stat, nan=0.0) > mult * thr)
    eps = episodes(E, on)
    res = {}
    empty_min = 0.0; fa = 0
    for a, b, lab in pairs:
        g = GROUP.get(lab)
        if g is None: continue
        if g == 'bos':
            fa += sum(1 for x in eps if a + 5 <= x[0] <= b); empty_min += (b - a - 5) / 60
        else:
            res.setdefault(g, []).append(any(x[0] <= b + 5 and x[1] >= a - 1 for x in eps))
    return res, fa, empty_min, float(np.mean(on)) if len(on) else 0.0

data = {}
def get(tag):
    if tag not in data:
        d = load(P(tag) + '.pcap'); data[tag] = (d, sanitize(d))
    return data[tag]

results = {}
t_all = time.time()
for mname, (kind, spec) in METHODS.items():
    fitted = {}
    for sname in ['dev', 'S2', 'S3', 'S4']:
        s = SESS[sname]; d, Hs = get(s['tag'])
        if s['cal_from']:
            obj, thr, = fitted[s['cal_from']]
        else:
            cm = (d['T'] >= s['ce'] - s['cal_s']) & (d['T'] <= s['ce'])
            if kind == 't2':
                obj = T2Variant(**spec).fit(d, Hs, cm)
                _, cst = obj.run(d, Hs, cm)
            else:
                obj = spec
                _, cst = spec(d, Hs, cm)
            thr = thr_rule(cst)
            fitted[sname] = (obj, thr)
        t0 = time.time()
        E, st = obj.run(d, Hs) if kind == 't2' else obj(d, Hs)
        np.savez(f'stat_{mname.split()[0]}_{sname}.npz', E=E, st=st, thr=thr)
        for m in MULTS:
            res, fa, emin, onfrac = evaluate(E, st, thr, s['pairs'], m)
            results.setdefault(mname, {}).setdefault(str(m), {})[sname] = dict(
                res={k: [int(sum(v)), len(v)] for k, v in res.items()}, fa=fa, empty_min=round(emin, 2), thr=float(thr))
        print(f'{mname:30s} {sname:4s} thr {thr:9.4f}  ({time.time()-t0:.0f}s)', flush=True)
    json.dump(results, open('bench_results.json', 'w'), indent=1)
print('bitti', round(time.time() - t_all), 'sn')
