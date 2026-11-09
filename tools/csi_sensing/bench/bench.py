"""Tek kutu CSI: karsilastirma (baseline), ablasyon ve esik taramasi.

Tum yontemler ayni karar katmani (0.5 sn adim, 3/4 histerezis, 4 sakin adimla birakma) ve ayni
esik kurali (kalibrasyon istatistiginden max(med + 8*1.4826*MAD, 1.2*P99.5)) ile degerlendirilir.
"""
import json, os, sys, math, collections
import numpy as np
sys.path.insert(0, '/Users/lordsploit/Documents/msc/device-files/csi-alpha-webui/webui')
import csi_sensing as S
from csi_residual import ledoit_wolf, assign_state

HOP = 0.5
W_S, B_S, TAU, SETTLE = 3.0, 20.0, 30.0, 20.0
INIT_N, COV_N = 100, 1000

# ---------------------------------------------------------------- veri
def load(pcap):
    cache = pcap.replace('.pcap', '.bench.npz')
    if os.path.exists(cache):
        d = np.load(cache)
        return {k: d[k] for k in d.files}
    lay = S.ToneLayout(64, 56)
    T, H, R, Q = [], [], [], []
    for f in S.iter_pcap(pcap):
        if f['frameControl'] != 0xD0 or f['nfft'] != 64:
            continue
        v = np.frombuffer(f['iq'][:256], dtype='<i2').astype(float)
        c = v[0::2] + 1j * v[1::2]
        T.append(f['ts'] / 1000); H.append(c[lay.fft_index]); R.append(f['rssi']); Q.append(f['seq'])
    d = dict(T=np.array(T), H=np.array(H), R=np.array(R, float), Q=np.array(Q), ks=np.array(lay.ks))
    np.savez(cache, **d)
    return d

def sanitize(d):
    H, ks = d['H'], d['ks']
    adj = np.diff(ks) == 1
    acc = (H[:, 1:] * np.conj(H[:, :-1]))[:, adj].sum(1)
    Hs = H * np.exp(-1j * np.angle(acc)[:, None] * ks[None, :])
    return Hs

def gt_pairs(events_path):
    ev = [json.loads(l) for l in open(events_path)]
    out, cur = [], None
    for e in ev:
        if e.get('type') != 'gt':
            continue
        if e['kind'] == 'start':
            cur = (e['ts'] / 1000, e['label'])
        elif e['kind'] == 'end' and cur:
            out.append((cur[0], e['ts'] / 1000, cur[1])); cur = None
    return out

def cal_done(events_path):
    return [json.loads(l)['t'] for l in open(events_path) if '"calibration_done"' in l]

# ---------------------------------------------------------------- karar
def kofn(active, k=3, n=4, off=4):
    out = np.zeros(len(active), bool); on = False; q = 0
    for i in range(len(active)):
        if not on and active[max(0, i - n + 1):i + 1].sum() >= k:
            on = True; q = 0
        elif on:
            q = 0 if active[i] else q + 1
            if q >= off:
                on = False
        out[i] = on
    return out

def episodes(E, on):
    eps = []; s = None
    for e, o in zip(E, on):
        if o and s is None: s = e
        if not o and s is not None: eps.append((s, e)); s = None
    if s is not None: eps.append((s, E[-1]))
    return eps

def thr_rule(v):
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return np.inf
    med = np.median(v); mad = 1.4826 * np.median(np.abs(v - med))
    return max(med + 8 * mad, 1.2 * np.percentile(v, 99.5))

# ---------------------------------------------------------------- T2 ailesi
class T2Variant:
    def __init__(self, proj='ls', perstate=True, white='lw', retry=True):
        self.proj, self.perstate, self.white, self.retry = proj, perstate, white, retry

    def _vec(self, Hs):
        if self.proj == 'ls':
            return Hs
        # CPE kaldir + genlik normalizasyonu (klasik on isleme)
        cpe = np.angle(Hs.sum(1))
        Hn = Hs * np.exp(-1j * cpe)[:, None]
        return Hn / np.abs(Hn).mean(1, keepdims=True)

    def _keys(self, R, centers):
        if not self.perstate:
            return np.zeros(len(R), int)
        return np.array([assign_state(centers, r) for r in R])

    def fit(self, d, Hs, mask):
        keep = self._keep(d) & mask
        X = self._vec(Hs[keep]); R = d['R'][keep]
        centers = []
        if self.perstate:
            for v, _ in collections.Counter(np.round(R).astype(int)).most_common():
                assign_state(centers, v)
        keys = self._keys(R, centers)
        dom = collections.Counter(keys).most_common(1)[0][0]
        Xd = X[keys == dom]
        D = Xd.mean(0)
        if self.proj == 'ls':
            for _ in range(3):
                g = (Xd @ np.conj(D)) / np.vdot(D, D).real
                D = (Xd / g[:, None]).mean(0)
            g = (Xd @ np.conj(D)) / np.vdot(D, D).real
            r = Xd / g[:, None] - D
        else:
            r = Xd - D
        Xr = np.concatenate([r.real, r.imag], 1)
        self.model = dict(D={dom: D}, W={dom: self._whiten(Xr)}, centers=centers, dom=dom)
        return self

    def _whiten(self, Xr):
        mu = Xr.mean(0)
        if self.white == 'scalar':
            s = np.sqrt(np.mean((Xr - mu) ** 2))
            return (mu, None, s)
        C, _ = ledoit_wolf(Xr - mu)
        w, V = np.linalg.eigh(C); w = np.maximum(w, 1e-3 * w.mean())
        return (mu, (V * w ** -0.5) @ V.T, None)

    def _keep(self, d):
        if not self.retry:
            return np.ones(len(d['T']), bool)
        q = d['Q']
        return np.r_[True, q[1:] != q[:-1]] | (q == 0xFFFF)

    def run(self, d, Hs, mask=None):
        """Model ile akis: (E, T2) dondurur. mask: kullanilacak kareler (zaman araligi)."""
        m = self.model
        keep = self._keep(d) if mask is None else (self._keep(d) & mask)
        T = d['T'][keep]; R = d['R'][keep]; X = self._vec(Hs[keep])
        centers = list(m['centers'])
        keys = self._keys(R, centers)
        Ds = {k: v.copy() for k, v in m['D'].items()}
        Ws = dict(m['W'])
        pend, covp, last = {}, {}, {}
        n = len(T); dim = 2 * X.shape[1]
        Z = np.full((n, dim), np.nan)
        for i in range(n):
            k = keys[i]; h = X[i]
            if k not in Ds:
                pend.setdefault(k, []).append(h)
                if len(pend[k]) >= INIT_N:
                    P = np.array(pend.pop(k)); D = P.mean(0)
                    if self.proj == 'ls':
                        for _ in range(3):
                            g = (P @ np.conj(D)) / np.vdot(D, D).real; D = (P / g[:, None]).mean(0)
                    Ds[k] = D; last[k] = T[i]
                continue
            D = Ds[k]
            if self.proj == 'ls':
                g = np.vdot(D, h) / np.vdot(D, D).real
                x = h / g
            else:
                x = h
            r = x - D
            dt = max(0.0, T[i] - last.get(k, T[i])); last[k] = T[i]
            Ds[k] = D + (1 - math.exp(-dt / TAU)) * r
            xr = np.concatenate([r.real, r.imag])
            if k not in Ws:
                covp.setdefault(k, []).append(xr)
                if len(covp[k]) >= COV_N:
                    Ws[k] = self._whiten(np.array(covp.pop(k)))
                continue
            mu, Wm, s = Ws[k]
            Z[i] = (xr - mu) / s if Wm is None else Wm @ (xr - mu)
        ok = np.isfinite(Z[:, 0])
        T, Z, keys = T[ok], Z[ok], keys[ok]
        E = np.arange(np.ceil((T[0] + B_S + W_S) / HOP) * HOP, T[-1], HOP)
        out = np.full(len(E), np.nan)
        cs = {}; ts = {}
        for k in np.unique(keys):
            sel = keys == k
            ts[k] = T[sel]; cs[k] = np.vstack([np.zeros(dim), np.cumsum(Z[sel], 0)])
        prev = None; settle_until = -1e18
        for j, e in enumerate(E):
            if self.perstate:
                a2 = np.searchsorted(T, e - 2.0); b = np.searchsorted(T, e, 'right')
                if b > a2:
                    vv, cc = np.unique(keys[a2:b], return_counts=True); dk = vv[cc.argmax()]
                    if prev is not None and dk != prev:
                        settle_until = e + SETTLE
                    prev = dk
                if e < settle_until:
                    continue
                a0 = np.searchsorted(T, e - B_S, 'right')
                vv, cc = np.unique(keys[a0:b], return_counts=True)
                if len(vv) == 0: continue
                k = vv[cc.argmax()]
            else:
                k = keys[0]
            tk = ts[k]
            i0 = np.searchsorted(tk, e - B_S, 'right'); i1 = np.searchsorted(tk, e - W_S, 'right')
            i2 = np.searchsorted(tk, e, 'right')
            nw, nb = i2 - i1, i1 - i0
            if nw < 15 or nb < 100: continue
            mw = (cs[k][i2] - cs[k][i1]) / nw; mb = (cs[k][i1] - cs[k][i0]) / nb
            out[j] = (nw * nb / (nw + nb)) * np.sum((mw - mb) ** 2) / dim
        return E, out

# ---------------------------------------------------------------- klasik baz yontemler
def amp_variance(d, Hs, mask=None, W=2.0):
    """B1: klasik CSI hareket olcutu - log-genligin ton basina zamansal std'si (ortalama)."""
    T = d['T']; L = np.log10(np.abs(d['H']) + 1.0)
    if mask is not None: T, L = T[mask], L[mask]
    E = np.arange(np.ceil((T[0] + W) / HOP) * HOP, T[-1], HOP); out = np.full(len(E), np.nan)
    c1 = np.vstack([np.zeros(L.shape[1]), np.cumsum(L, 0)]); c2 = np.vstack([np.zeros(L.shape[1]), np.cumsum(L ** 2, 0)])
    for j, e in enumerate(E):
        a, b = np.searchsorted(T, [e - W, e])
        n = b - a
        if n < 15: continue
        m = (c1[b] - c1[a]) / n; v = (c2[b] - c2[a]) / n - m ** 2
        out[j] = np.mean(np.sqrt(np.maximum(v, 0)))
    return E, out

def common_power(d, Hs, mask=None):
    """B4: ortak guc degisimi |mean_W P - mean_B P| (dB), baskin RSSI durumu, yeniden gonderim atilmis."""
    q = d['Q']; keep = np.r_[True, q[1:] != q[:-1]]
    if mask is not None: keep &= mask
    T = d['T'][keep]; R = d['R'][keep]; P = 20 * np.log10(np.abs(d['H'][keep]).mean(1))
    E = np.arange(np.ceil((T[0] + B_S + W_S) / HOP) * HOP, T[-1], HOP); out = np.full(len(E), np.nan)
    for j, e in enumerate(E):
        a0, a1, b = np.searchsorted(T, [e - B_S, e - W_S, e])
        if b - a1 < 15 or a1 - a0 < 100: continue
        rr = R[a0:b]; vv, cc = np.unique(rr, return_counts=True); dom = vv[cc.argmax()]
        pw = P[a1:b][R[a1:b] == dom]; pb = P[a0:a1][R[a0:a1] == dom]
        if len(pw) < 10 or len(pb) < 50: continue
        out[j] = abs(pw.mean() - pb.mean())
    return E, out
