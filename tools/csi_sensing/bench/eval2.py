"""Duzeltilmis olcutler:
- Yakalama: olay penceresinde [a-1, b+5] YENI bir alarm baslangici (yukselen kenar). Olay basinda zaten
  alarmdaysa 'onceden aktif' sayilir ve yakalanmis sayilmaz (her olayi tek bir uzun alarmla ortme tuzagi).
- Bos oda: alarm sayisi (/saat) ve alarmda gecen sure orani.
- T2 icin canli motordaki 2.0 alt esik ('dagitilan') ayrica raporlanir.
"""
import json, glob, numpy as np
from bench import kofn, episodes
from bench_sessions import SESS, GROUP

DEV, IND = ['dev'], ['S2', 'S3', 'S4']
MULTS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]

def on_series(path, mult, floor=0.0):
    d = np.load(path)
    if 'on' in d.files:
        return d['E'], d['on'].astype(bool)
    thr = max(float(d['thr']), floor)
    return d['E'], kofn(np.nan_to_num(d['st'], nan=0.0) > mult * thr)

def score(E, on, pairs):
    eps = episodes(E, on)
    onsets = [x[0] for x in eps]
    res = {}; pre = {}; fa = 0; emin = 0.0; eon = 0.0
    for a, b, lab in pairs:
        g = GROUP.get(lab)
        if g is None: continue
        if g == 'bos':
            a5 = a + 5
            fa += sum(1 for t in onsets if a5 <= t <= b); emin += (b - a5) / 60
            m = (E >= a5) & (E <= b); eon += on[m].sum() * 0.5 / 60
            continue
        i = np.searchsorted(E, a - 1.0)
        already = bool(on[max(0, i - 1)]) if len(on) else False
        hit = any(a - 1.0 <= t <= b + 5.0 for t in onsets)
        res.setdefault(g, []).append(hit); pre.setdefault(g, []).append(already)
    return res, pre, fa, emin, eon

def agg(prefix, mult, sessions, floor=0.0):
    tot = {}; pre = {}; fa = 0; emin = 0.0; eon = 0.0
    for s in sessions:
        path = glob.glob(f'stat_{prefix}_{s}.npz')
        if not path: continue
        E, on = on_series(path[0], mult, floor)
        r, p, f, em, eo = score(E, on, SESS[s]['pairs'])
        for g, v in r.items():
            a = tot.setdefault(g, [0, 0]); a[0] += sum(v); a[1] += len(v)
        for g, v in p.items():
            a = pre.setdefault(g, [0, 0]); a[0] += sum(v); a[1] += len(v)
        fa += f; emin += em; eon += eo
    return tot, pre, fa, emin, eon

def fr(t, g):
    h, n = t.get(g, [0, 0]); return f'{h}/{n}' if n else '–'

def inroom(t):
    return (sum(t.get(g, [0, 0])[0] for g in ('yakin', 'orta', 'uzak')),
            sum(t.get(g, [0, 0])[1] for g in ('yakin', 'orta', 'uzak')))

def empty_txt(fa, emin, eon):
    return f'{fa} ({fa/(emin/60):.1f}/sa, %{100*eon/emin:.1f})' if emin else '–'

NAMES = {'B1': 'B1 Genlik varyansı (klasik)', 'B2x1.0': 'B2 Önceki motor (şekil std + novelty)',
         'B3': 'B3 Arka plan çıkarma (normalize, beyazlatmasız)', 'B4': 'B4 Ortak güç değişimi',
         'T2': 'Önerilen T² (yalnız kalibrasyon kuralı)', 'T2d': '**Önerilen T², dağıtılan (alt eşik 2.0)**',
         'A1': 'A1 − LS izdüşümü (yerine CPE+genlik norm.)', 'A2': 'A2 − kovaryans beyazlatma (skaler)',
         'A3': 'A3 − durum ayrımı (tek D, tek W)', 'A4': 'A4 − yeniden gönderim süzme'}

def row(label, prefix, mult, floor=0.0):
    td, _, fd, ed, eod = agg(prefix, mult, DEV, floor)
    ti, pi, fi, ei, eoi = agg(prefix, mult, IND, floor)
    h, n = inroom(ti); ph, pn = inroom(pi)
    return (f'| {label} | {fr(td,"yakin")} | {fr(td,"uzak")} | {empty_txt(fd,ed,eod)} | {fr(ti,"yakin")} | {fr(ti,"orta")} | '
            f'{fr(ti,"uzak")} | **{h}/{n}** | {ph}/{pn} | {fr(ti,"disari")} | {empty_txt(fi,ei,eoi)} |')

HDR = ('| Yöntem | Gel. yakın | Gel. uzak | Gel. boş oda: alarm (/sa, süre) | Bğm. yakın | Bğm. orta | Bğm. uzak | **Bğm. oda içi** | önceden aktif | Bğm. kapı dışı ↓ | Bğm. boş oda: alarm (/sa, süre) |\n'
       '|---|---|---|---|---|---|---|---|---|---|---|')
out = []
out.append('### Tablo 1. Karşılaştırma (her yöntemin kendi kalibrasyon eşiği, çarpan 1.0)\n')
out.append(HDR)
for p in ['B1', 'B2x1.0', 'B3', 'B4', 'T2']:
    out.append(row(NAMES[p], p, 1.0))
out.append(row(NAMES['T2d'], 'T2', 1.0, floor=2.0))
out.append('\n### Tablo 2. Ablasyon (alt eşik 2.0, çarpan 1.0)\n')
out.append(HDR)
out.append(row(NAMES['T2d'], 'T2', 1.0, floor=2.0))
for p in ['A1', 'A2', 'A3', 'A4']:
    out.append(row(NAMES[p], p, 1.0, floor=2.0))
out.append('\n### Tablo 3. Eşik taraması: önerilen T² (eşik = çarpan × max(kalibrasyon eşiği, 2.0))\n')
out.append(HDR.replace('Yöntem', 'Çarpan'))
for m in MULTS:
    out.append(row(f'× {m}', 'T2', m, floor=2.0))
out.append('\n### Tablo 4. Eşit yanlış alarm bütçesi: bağımsız boş odada ≤ 4 alarm/saat ve alarm süresi ≤ %1 iken en yüksek oda içi yakalama\n')
out.append('| Yöntem | Çarpan | **Bğm. oda içi** | yakın | orta | uzak | kapı dışı ↓ | boş oda | Gel. yakın / uzak / boş oda |\n|---|---|---|---|---|---|---|---|---|')
cands = [('B1', 0.0), ('B3', 0.0), ('B4', 0.0), ('T2', 2.0), ('A1', 2.0), ('A2', 2.0), ('A3', 2.0), ('A4', 2.0)]
for p, fl in cands:
    best = None
    for m in MULTS:
        ti, pi, fi, ei, eoi = agg(p, m, IND, fl)
        if fi / (ei / 60) > 4.0 or 100 * eoi / ei > 1.0: continue
        h, n = inroom(ti)
        if best is None or (h, -m) > best[0]:
            best = ((h, -m), m)
    lab = NAMES['T2d'] if p == 'T2' else NAMES[p]
    if best is None:
        out.append(f'| {lab} | bütçeye giren eşik yok | | | | | | | |'); continue
    m = best[1]; ti, pi, fi, ei, eoi = agg(p, m, IND, fl); td, _, fd, ed, eod = agg(p, m, DEV, fl); h, n = inroom(ti)
    out.append(f'| {lab} | {m} | **{h}/{n}** | {fr(ti,"yakin")} | {fr(ti,"orta")} | {fr(ti,"uzak")} | {fr(ti,"disari")} | {empty_txt(fi,ei,eoi)} | {fr(td,"yakin")} / {fr(td,"uzak")} / {empty_txt(fd,ed,eod)} |')
# B2: yalniz motionScale taramasi (0.5, 1, 2)
for sc in ['0.5', '1.0', '2.0']:
    ti, pi, fi, ei, eoi = agg(f'B2x{sc}', 1.0, IND); td, _, fd, ed, eod = agg(f'B2x{sc}', 1.0, DEV); h, n = inroom(ti)
    ok = ei and fi / (ei / 60) <= 4.0 and 100 * eoi / ei <= 1.0
    out.append(f'| B2 Önceki motor (motionScale {sc}){"" if ok else " — bütçe dışı"} | – | **{h}/{n}** | {fr(ti,"yakin")} | {fr(ti,"orta")} | {fr(ti,"uzak")} | {fr(ti,"disari")} | {empty_txt(fi,ei,eoi)} | {fr(td,"yakin")} / {fr(td,"uzak")} / {empty_txt(fd,ed,eod)} |')
open('bench_tables.md', 'w').write('\n'.join(out) + '\n')
print('\n'.join(out))
