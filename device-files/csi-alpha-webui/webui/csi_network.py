#!/usr/bin/env python3
"""csi-alpha ag yonetimi (NetworkManager / nmcli). Yalnizca stdlib.

Panelden: mevcut baglanti, Wi-Fi tarama, yeni aga baglanma, kayitli aglar (oncelik/silme),
kurulum hotspot'u. Ag degisikligi HTTP baglantisini koparacagi icin islem
`systemd-run` ile ayri birimde `net_switch.sh` tarafindan yapilir (geri donus mantigi orada).
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

MGMT_IFACE = os.environ.get("CSI_MGMT_IFACE", "mgmt0")
HOTSPOT_CON = "csi-hotspot"

DEFAULT_NETWORK_CONFIG = {
    "hotspotSsid": "csi-alpha-kurulum",
    "hotspotPassword": "csialpha2026",
    "hotspotChannel": 6,
    "autoHotspot": True,
    "autoHotspotAfterS": 180,
    "panelPassword": "",
}


def _bin(name):
    found = shutil.which(name)
    if found:
        return found
    return f"/usr/sbin/{name}" if Path(f"/usr/sbin/{name}").exists() else name


def run(args, timeout=10):
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return res.returncode, res.stdout, res.stderr
    except Exception as exc:  # nmcli yok (gelistirme makinesi) vb.
        return 127, "", str(exc)


def _split_terse(line):
    """nmcli -t ciktisi: ':' ayirici, '\\:' kacisli."""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


class NetworkManager:
    def __init__(self, base_dir, log=None):
        self.base_dir = Path(base_dir)
        self.config_path = self.base_dir / "config" / "network.json"
        self.switch_script = self.base_dir / "net_switch.sh"
        self.log = log or (lambda line: None)
        self.lock = threading.RLock()
        self.last_action = None
        self.no_ip_since = None
        self.watch_thread = None
        self.stop_event = threading.Event()

    # ------------------------------------------------------------ config
    def config(self):
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        cfg = dict(DEFAULT_NETWORK_CONFIG)
        cfg.update({k: v for k, v in data.items() if k in DEFAULT_NETWORK_CONFIG})
        return cfg

    def public_config(self):
        cfg = self.config()
        cfg["panelPasswordSet"] = bool(cfg.pop("panelPassword"))
        return cfg

    def save_config(self, updates):
        cfg = self.config()
        for key, value in (updates or {}).items():
            if key not in DEFAULT_NETWORK_CONFIG:
                continue
            default = DEFAULT_NETWORK_CONFIG[key]
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = int(value)
            else:
                value = str(value)[:64]
            cfg[key] = value
        if len(cfg["hotspotPassword"]) < 8:
            raise ValueError("hotspot parolasi en az 8 karakter olmali")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.config_path)
        return self.public_config()

    def check_password(self, supplied):
        expected = self.config().get("panelPassword") or ""
        return not expected or supplied == expected

    # ------------------------------------------------------------ status
    def status(self):
        rc, out, _ = run(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"])
        active = []
        for line in out.splitlines():
            parts = _split_terse(line)
            if len(parts) >= 4 and parts[2] != "loopback":
                active.append({"name": parts[0], "type": parts[2], "device": parts[3]})
        addrs = {}
        rc2, out2, _ = run(["ip", "-4", "-o", "addr", "show"])
        for line in out2.splitlines():
            f = line.split()
            if len(f) >= 4 and f[1] != "lo":
                addrs.setdefault(f[1], []).append(f[3].split("/")[0])
        link = {}
        rc3, out3, _ = run([_bin("iw"), "dev", MGMT_IFACE, "link"])
        for line in out3.splitlines():
            line = line.strip()
            if line.startswith("SSID:"):
                link["ssid"] = line.split(":", 1)[1].strip()
            elif line.startswith("signal:"):
                link["signal"] = line.split(":", 1)[1].strip()
            elif line.startswith("freq:"):
                link["freq"] = line.split(":", 1)[1].strip()
            elif line.startswith("tx bitrate:"):
                link["txBitrate"] = line.split(":", 1)[1].strip()
        mgmt_con = next((a["name"] for a in active if a["device"] == MGMT_IFACE), None)
        hostname = socket.gethostname()
        ips = [ip for dev, lst in addrs.items() for ip in lst]
        return {
            "available": rc == 0,
            "hostname": hostname,
            "mdns": f"{hostname}.local",
            "panelUrls": [f"http://{hostname}.local:8080"] + [f"http://{ip}:8080" for ip in ips],
            "addresses": addrs,
            "active": active,
            "mgmt": {"iface": MGMT_IFACE, "connection": mgmt_con, "hotspot": mgmt_con == HOTSPOT_CON,
                     "ipv4": addrs.get(MGMT_IFACE, []), **link},
            "ethernet": {"ipv4": addrs.get("eth0", [])},
            "saved": self.saved(),
            "lastAction": self.last_action,
            "config": self.public_config(),
            "logTail": self._log_tail(),
        }

    def saved(self):
        rc, out, _ = run(["nmcli", "-t", "-f", "NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,DEVICE",
                          "connection", "show"])
        items = []
        for line in out.splitlines():
            p = _split_terse(line)
            if len(p) < 5 or p[1] not in ("802-11-wireless", "wifi"):
                continue
            if p[0] in (HOTSPOT_CON, "csi-fallback-broadcom"):
                continue
            ssid = None
            rc2, o2, _ = run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", p[0]], timeout=5)
            if rc2 == 0:
                ssid = o2.strip() or None
            items.append({"name": p[0], "ssid": ssid, "autoconnect": p[2] == "yes",
                          "priority": int(p[3] or 0) if p[3].lstrip("-").isdigit() else 0,
                          "active": p[4] == MGMT_IFACE})
        items.sort(key=lambda x: (-x["priority"], x["name"]))
        return items

    def scan(self):
        run(["nmcli", "device", "wifi", "rescan", "ifname", MGMT_IFACE], timeout=15)
        time.sleep(2)
        rc, out, err = run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN,FREQ",
                            "device", "wifi", "list", "ifname", MGMT_IFACE], timeout=15)
        nets = {}
        for line in out.splitlines():
            p = _split_terse(line)
            if len(p) < 6 or not p[1]:
                continue
            item = {"ssid": p[1], "signal": int(p[2] or 0), "security": p[3] or "açık",
                    "channel": p[4], "freq": p[5], "inUse": p[0] == "*"}
            best = nets.get(item["ssid"])
            if best is None or item["signal"] > best["signal"]:
                nets[item["ssid"]] = item
        return {"ok": rc == 0, "error": err.strip() if rc else None,
                "networks": sorted(nets.values(), key=lambda x: -x["signal"])}

    # ------------------------------------------------------------ actions
    def _spawn(self, *args):
        if not self.switch_script.exists():
            raise RuntimeError(f"{self.switch_script} yok (deploy.sh calistirin)")
        unit = f"csi-net-switch-{int(time.time())}"
        cmd = ["systemd-run", "--unit", unit, "--collect", "--quiet",
               "--setenv", f"CSI_HOME={self.base_dir}", str(self.switch_script), *args]
        rc, out, err = run(cmd, timeout=10)
        if rc != 0:
            raise RuntimeError(err.strip() or f"systemd-run rc={rc}")
        self.last_action = {"ts": int(time.time() * 1000), "command": args[0],
                            "target": args[1] if len(args) > 1 else None, "unit": unit}
        self.log(f"network: {args[0]} {args[1] if len(args) > 1 else ''} ({unit})")
        return self.last_action

    def _secret_file(self, secret):
        if not secret:
            return "-"
        fd, path = tempfile.mkstemp(prefix="csi-net-", dir="/run" if os.path.isdir("/run") else None)
        with os.fdopen(fd, "w") as handle:
            handle.write(secret)
        os.chmod(path, 0o600)
        return path

    def connect(self, ssid, password="", hidden=False, priority=50):
        ssid = str(ssid or "").strip()
        if not ssid:
            raise ValueError("SSID gerekli")
        if password and len(password) < 8:
            raise ValueError("WPA parolasi en az 8 karakter")
        return self._spawn("connect", ssid, self._secret_file(password), "yes" if hidden else "no",
                           str(int(priority)))

    def activate(self, name):
        return self._spawn("activate", str(name))

    def hotspot(self, on):
        if on:
            cfg = self.config()
            return self._spawn("hotspot-on", cfg["hotspotSsid"], self._secret_file(cfg["hotspotPassword"]))
        return self._spawn("hotspot-off")

    def forget(self, name):
        rc, _, err = run(["nmcli", "connection", "delete", str(name)])
        if rc != 0:
            raise RuntimeError(err.strip() or "silinemedi")
        return True

    def set_priority(self, name, priority, autoconnect=True):
        rc, _, err = run(["nmcli", "connection", "modify", str(name),
                          "connection.autoconnect", "yes" if autoconnect else "no",
                          "connection.autoconnect-priority", str(int(priority))])
        if rc != 0:
            raise RuntimeError(err.strip() or "degistirilemedi")
        return True

    def _log_tail(self, lines=12):
        path = self.base_dir / "logs" / "net-switch.log"
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        except OSError:
            return []

    # ------------------------------------------------------------ watchdog
    def start_watchdog(self):
        if self.watch_thread:
            return
        self.watch_thread = threading.Thread(target=self._watch, daemon=True)
        self.watch_thread.start()

    def _watch(self):
        """Hicbir yonetim yolu (mgmt0/eth0) IPv4 almadan autoHotspotAfterS gecerse hotspot ac."""
        while not self.stop_event.wait(20):
            try:
                cfg = self.config()
                if not cfg["autoHotspot"]:
                    self.no_ip_since = None
                    continue
                rc, out, _ = run(["ip", "-4", "-o", "addr", "show"])
                if rc != 0:
                    continue
                devs = {line.split()[1] for line in out.splitlines() if len(line.split()) > 1}
                if MGMT_IFACE in devs or "eth0" in devs:
                    self.no_ip_since = None
                    continue
                now = time.time()
                if self.no_ip_since is None:
                    self.no_ip_since = now
                elif now - self.no_ip_since >= cfg["autoHotspotAfterS"]:
                    self.log("network watchdog: baglanti yok, kurulum hotspot'u aciliyor")
                    self.hotspot(True)
                    self.no_ip_since = now + 120  # tekrar denemeden once bekle
            except Exception as exc:
                self.log(f"network watchdog hata: {exc}")
