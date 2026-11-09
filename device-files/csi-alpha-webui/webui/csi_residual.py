"""Sizinti iptalli, bozucu-beyazlatilmis degisim dedektoru (tek kutu CSI).

Model (alt tasiyici k, kare t):
    H_k(t) = g(t) e^{-j k theta(t)} D_k^{(s)}  +  sum_i a_i(t) e^{-j 2 pi f_k tau_i(t)}  +  n_k(t)

D^{(s)} : dogrudan sizinti + statik yansimalar (verici durumu s basina; s = RSSI durumu),
g, theta: kare basina karmasik kazanc (AGC, CPE, guc durumu) ve STO egimi.

Adimlar
1. STO egimi komsu ton carpimlarinin acisiyla kaldirilir (extract_frame -> cvec).
2. En kucuk kareler izdusumu: g = D^H h / D^H D,  r = h/g - D.  r, D'ye diktir; kazanc ne kadar
   sicrarsa sicrasin sizinti tamamen yok edilir. D^{(s)} her durum icin EWMA ile izlenir (tau).
3. x = [Re r, Im r] (2K boyut) bos-oda kalibrasyonunun kovaryansiyla beyazlatilir: z = W (x - mu).
   Donanim bozucusunun (clutter) yuksek varyansli yonleri bastirilir (Mahalanobis).
4. Degisim istatistigi (Hotelling turu, iki pencere):
       C(t) = n_w || mean_{[t-W,t]} z  -  mean_{[t-B,t-W]} z ||^2 / dim
   H0 altinda ~ chi2_dim/dim (beklenen ~1). Hareket/yeni yansima C'yi buyutur.
5. Baskin verici durumu degisince (AX210 guc/anten durumu) B sn yerlesme: o sure C hesaplanmaz.

2026-11-09 kaydi (ax210_action, oda): yakin 8/8, uzak (4-5 m kapi/insan) 10/12, 15.5 dk bos odada 1
yanlis kume (W=3, B=20, tau=30). Ayni kayitta eski sekil metrigi 0/12 uzak.
"""
import collections
import math

try:
    import numpy as np
except ImportError:  # numpy yoksa kanal devre disi
    np = None

AVAILABLE = np is not None


MAX_GROUPS = 64


def group_index(n_tones, groups=MAX_GROUPS):
    """Ardisik tonlari en fazla `groups` gruba boler (80 MHz: 234 ton -> 64 grup). Komsu tonlarda
    yansimanin fazi neredeyse ayni oldugundan karmasik ortalama bilgiyi korur, boyutu dusurur."""
    g = min(int(n_tones), int(groups))
    return [list(map(int, a)) for a in np.array_split(np.arange(n_tones), g)]


def _group(H, idx):
    H = np.asarray(H)
    if H.ndim == 1:
        return np.array([H[i].mean() for i in idx])
    return np.stack([H[:, i].mean(1) for i in idx], axis=1)


def ledoit_wolf(X):
    """Ledoit-Wolf buzultmeli kovaryans (X: n x p, merkezlenmis). Az ornek / yuksek boyutta
    kalibrasyona asiri uyumu (dusuk varyansli yonlerin sisirilmesi) engeller."""
    n, p = X.shape
    S = X.T @ X / n
    m = np.trace(S) / p
    d2 = np.sum((S - m * np.eye(p)) ** 2)
    b2 = (np.sum(np.sum(X ** 2, axis=1) ** 2) / n - np.sum(S ** 2)) / n
    alpha = 0.0 if d2 <= 0 else float(min(1.0, max(0.0, b2 / d2)))
    return alpha * m * np.eye(p) + (1 - alpha) * S, alpha


STATE_TOL_DB = 1.0


def assign_state(centers, rssi, tol=STATE_TOL_DB):
    """RSSI'yi verici kazanc durumuna esle: en yakin merkez +-tol dB icindeyse o, degilse yeni merkez.
    Ayrik durumlar (AX210: -19/-21/-23) ayrilir; sureklilik gosteren RSSI titresimi (+-1 dB) tek durumda kalir."""
    r = float(rssi)
    best = None
    for c in centers:
        d = abs(c - r)
        if d <= tol and (best is None or d < abs(best - r)):
            best = c
    if best is None:
        best = int(round(r))
        centers.append(best)
    return best


def whitener(X, reg=1e-3):
    """X (n x p) artik vektorleri -> (mu, W, buzulme). W = C^{-1/2}, C Ledoit-Wolf."""
    mu = X.mean(0)
    C, shrink = ledoit_wolf(X - mu)
    w, V = np.linalg.eigh(C)
    w = np.maximum(w, reg * max(w.mean(), 1e-12))
    return mu, (V * (w ** -0.5)) @ V.T, shrink


def _ls(h, D, dd):
    g = np.vdot(D, h) / dd
    if g == 0:
        return None
    return h / g


def fit_model(frames, reg=1e-3, min_frames=300):
    """Kalibrasyon karelerinden (Frame.cvec, Frame.rssi) model: baskin durum D, mu, W."""
    if not AVAILABLE:
        return None
    centers = []
    ordered = sorted((f for f in frames if getattr(f, "cvec", None) is not None),
                     key=lambda f: f.rssi)
    counts = collections.Counter(int(round(f.rssi)) for f in ordered)
    for value, _n in counts.most_common():          # en sik degerler merkez olur
        assign_state(centers, value)
    rows = [((int(getattr(f, "mode", 0)), assign_state(centers, f.rssi)), f.cvec)
            for f in frames if getattr(f, "cvec", None) is not None and getattr(f, "mode", 0) >= 0]
    if len(rows) < min_frames:
        return None
    states = collections.Counter(s for s, _ in rows)
    dom = states.most_common(1)[0][0]
    n_tones = len(rows[0][1])
    idx = group_index(n_tones)
    Hs = _group(np.array([v for s, v in rows if s == dom and len(v) == n_tones], dtype=complex), idx)
    if len(Hs) < min_frames:
        return None
    D = Hs.mean(0)
    for _ in range(3):
        g = (Hs @ np.conj(D)) / np.vdot(D, D).real
        D = (Hs / g[:, None]).mean(0)
    g = (Hs @ np.conj(D)) / np.vdot(D, D).real
    R = Hs / g[:, None] - D[None, :]
    X = np.concatenate([R.real, R.imag], axis=1)
    mu, W, shrink = whitener(X, reg)
    leak_db = 10 * math.log10(max(1e-12, float(np.mean(np.abs(R) ** 2) / np.mean(np.abs(D) ** 2))))
    return {
        "state": int(dom[1]),
        "mode": int(dom[0]),
        "centers": centers,
        "tones": int(n_tones),
        "groups": len(idx),
        "shrinkage": round(shrink, 4),
        "dim": int(X.shape[1]),
        "D": [[float(z.real), float(z.imag)] for z in D],
        "mu": [float(v) for v in mu],
        "W": [float(v) for v in W.ravel()],
        "residualDb": round(leak_db, 2),
        "frames": int(len(Hs)),
    }


class ResidualDetector:
    """Akan kareler -> beyazlatilmis artik z(t) -> degisim istatistigi C(t)."""

    def __init__(self, model, window_s=3.0, base_s=20.0, tau_s=30.0, settle_s=20.0, init_frames=100,
                 cov_frames=1000):
        self.ok = AVAILABLE and bool(model)
        self.window_s = float(window_s)
        self.base_s = float(base_s)
        self.tau_s = float(tau_s)
        self.settle_s = float(settle_s)
        self.init_frames = int(init_frames)
        self.cov_frames = int(cov_frames)
        self.buf = collections.deque()       # (t, z, state)
        self.white = {}                      # durum -> (mu, W): durum basina bozucu kovaryansi
        self.cov_pending = {}
        self.Ds = {}
        self.dd = {}
        self.last_t = {}
        self.pending = {}
        self.prev_dom = None
        self.settle_until = -1e18
        if not self.ok:
            return
        D = np.array([complex(a, b) for a, b in model["D"]])
        self.dim = int(model["dim"])
        key = (int(model.get("mode", 0)), int(model["state"]))
        self.white[key] = (np.array(model["mu"]), np.array(model["W"]).reshape(self.dim, self.dim))
        self.n_tones = int(model.get("tones") or len(D))
        self.idx = group_index(self.n_tones, len(D))
        self.centers = [int(c) for c in (model.get("centers") or [model["state"]])]
        self._set_state(key, D)

    def configure(self, window_s, base_s, tau_s, settle_s):
        self.window_s, self.base_s, self.tau_s, self.settle_s = map(float, (window_s, base_s, tau_s, settle_s))

    def _set_state(self, s, D):
        self.Ds[s] = D
        self.dd[s] = float(np.vdot(D, D).real)

    def push(self, t, rssi, cvec, mode=0):
        """mode: motorun sekil-mod bankasi kimligi (PHY modu); -1 (yeni/bilinmeyen) kareler atlanir."""
        if not self.ok or cvec is None or len(cvec) != self.n_tones or mode is None or mode < 0:
            return
        s = (int(mode), assign_state(self.centers, rssi))
        h = _group(np.asarray(cvec, dtype=complex), self.idx)
        if s not in self.Ds:
            lst = self.pending.setdefault(s, [])
            lst.append(h)
            if len(lst) >= self.init_frames:
                Hs = np.array(self.pending.pop(s))
                D = Hs.mean(0)
                for _ in range(3):
                    g = (Hs @ np.conj(D)) / np.vdot(D, D).real
                    D = (Hs / g[:, None]).mean(0)
                self._set_state(s, D)
                self.last_t[s] = t
            return
        D = self.Ds[s]
        x = _ls(h, D, self.dd[s])
        if x is None:
            return
        r = x - D
        dt = max(0.0, t - self.last_t.get(s, t))
        self.last_t[s] = t
        a = 1.0 - math.exp(-dt / self.tau_s) if self.tau_s > 0 else 0.0
        if a > 0:
            self._set_state(s, D + a * r)
        xr = np.concatenate([r.real, r.imag])
        white = self.white.get(s)
        if white is None:
            # Yeni verici durumu: gurultu yapisi farkli (17:46 gecisinden sonra eski W ile bos odada
            # T2 medyani 64.7). Kendi kovaryansi ogrenilene kadar bu durumdan T2 uretilmez.
            lst = self.cov_pending.setdefault(s, [])
            lst.append(xr)
            if len(lst) >= self.cov_frames:
                mu, W, _shrink = whitener(np.array(self.cov_pending.pop(s)))
                self.white[s] = (mu, W)
            return
        mu, W = white
        z = W @ (xr - mu)
        self.buf.append((t, z, s))
        keep = self.base_s + self.window_s + 1.0
        while self.buf and self.buf[0][0] < t - keep:
            self.buf.popleft()

    def statistic(self, t):
        """C(t) veya None (yetersiz veri / yerlesme)."""
        if not self.ok or not self.buf:
            return None
        recent = [s for (tt, _z, s) in self.buf if tt > t - 2.0]
        if recent:
            dom = collections.Counter(recent).most_common(1)[0][0]
            if self.prev_dom is not None and dom != self.prev_dom:
                self.settle_until = t + self.base_s
            self.prev_dom = dom
        if t < self.settle_until:
            return None
        w_lo = t - self.window_s
        b_lo = t - self.base_s
        # Yalniz baskin durumun kareleri: durumlar arasi karisim orani oynadiginda her durumun kucuk
        # sapmasi pencere ortalamasina sizmasin (ax210_action 17:46 sonrasi / sentetik PHY modlari).
        span = [s for (tt, _z, s) in self.buf if b_lo < tt <= t]
        if not span:
            return None
        key = collections.Counter(span).most_common(1)[0][0]
        win = [z for (tt, z, s) in self.buf if w_lo < tt <= t and s == key]
        base = [z for (tt, z, s) in self.buf if b_lo < tt <= w_lo and s == key]
        if len(win) < 15 or len(base) < 100:
            return None
        mw = np.mean(win, axis=0)
        mb = np.mean(base, axis=0)
        nw, nb = len(win), len(base)
        # Iki orneklem Hotelling olcegi: H0 altinda E[T2] = 1 (bagimsiz, beyaz z icin)
        return float((nw * nb / (nw + nb)) * np.sum((mw - mb) ** 2) / self.dim)

    def reset(self):
        self.buf.clear()
        self.settle_until = -1e18
        self.prev_dom = None
