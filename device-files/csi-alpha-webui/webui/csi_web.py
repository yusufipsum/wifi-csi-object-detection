#!/usr/bin/env python3
import argparse
import collections
import json
import math
import os
import queue
import signal
import struct
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


BASE_DIR = Path("/home/admin/csi")
WEB_DIR = BASE_DIR / "webui"
CAPTURE_DIR = BASE_DIR / "captures"
DATASET_DIR = BASE_DIR / "datasets"
MODEL_DIR = BASE_DIR / "models"
LIVE_MODEL_PATH = MODEL_DIR / "hand_motion_live_model.json"
STREAM_SCRIPT = BASE_DIR / "start_rx_stream.sh"
DEFAULT_CHANNEL = "48/80"
DEFAULT_SOURCE_MAC = "88:a2:9e:5d:4e:a6"
DEFAULT_DISTANCE_M = 2.0
PUBLISH_INTERVAL_S = 0.10
DATASET_FRAME_STRIDE = 12
VISUAL_TONES = 96
DATASET_TONES = 128
MAX_LOG_LINES = 80


def now_ms():
    return int(time.time() * 1000)


def read_exact(stream, size, stop_event):
    data = bytearray()
    while len(data) < size and not stop_event.is_set():
        chunk = stream.read(size - len(data))
        if not chunk:
            if data:
                raise EOFError("short read")
            raise EOFError("eof")
        data.extend(chunk)
    if len(data) != size:
        raise EOFError("stopped")
    return bytes(data)


def mac_to_text(raw):
    return ":".join(f"{b:02x}" for b in raw)


def clean_label(value):
    text = str(value or "").strip().lower()
    keep = []
    for ch in text:
        if ch.isalnum() or ch in ("_", "-"):
            keep.append(ch)
        elif ch.isspace():
            keep.append("_")
    return "".join(keep).strip("_-")


def round_series(values, digits=2):
    return [round(float(v), digits) for v in values]


def log_amp_series(values):
    return [math.log10(max(1.0, float(v))) for v in values]


def record_dirs():
    return {
        "captures": CAPTURE_DIR,
        "datasets": DATASET_DIR,
    }


def safe_record_path(kind, name):
    base = record_dirs().get(kind)
    filename = Path(str(name or "")).name
    if not base or not filename or filename != name:
        return None
    path = (base / filename).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


def content_type_for(path):
    suffix = path.suffix.lower()
    if suffix == ".pcap":
        return "application/vnd.tcpdump.pcap"
    if suffix == ".ndjson":
        return "application/x-ndjson; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    return "application/octet-stream"


def list_record_files():
    payload = {}
    for kind, folder in record_dirs().items():
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        for path in folder.iterdir():
            if not path.is_file():
                continue
            stat = path.stat()
            files.append({
                "kind": kind,
                "name": path.name,
                "size": stat.st_size,
                "modifiedAt": int(stat.st_mtime * 1000),
                "downloadUrl": f"/download?kind={kind}&name={quote(path.name)}",
            })
        files.sort(key=lambda item: item["modifiedAt"], reverse=True)
        payload[kind] = files
    return payload


def load_live_model():
    try:
        model = json.loads(LIVE_MODEL_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    required = ("window", "threshold", "featureNames", "featureMean", "featureStd", "weights", "bias")
    if any(key not in model for key in required):
        return None
    return model


def sigmoid(value):
    value = max(-30.0, min(30.0, float(value)))
    return 1.0 / (1.0 + math.exp(-value))


def classify(motion_score, avg_amp, packet_rate):
    if packet_rate < 5:
        return {
            "label": "sinyal yok",
            "confidence": 0.0,
            "kind": "quiet",
            "model": "rules-v0",
        }
    if motion_score > 0.18:
        return {
            "label": "guclu hareket",
            "confidence": min(0.98, 0.55 + motion_score),
            "kind": "active",
            "model": "rules-v0",
        }
    if motion_score > 0.07:
        return {
            "label": "hafif hareket",
            "confidence": min(0.9, 0.48 + motion_score * 2.0),
            "kind": "motion",
            "model": "rules-v0",
        }
    if avg_amp > 1:
        return {
            "label": "stabil",
            "confidence": 0.72,
            "kind": "stable",
            "model": "rules-v0",
        }
    return {
        "label": "bekliyor",
        "confidence": 0.0,
        "kind": "quiet",
        "model": "rules-v0",
    }


def parse_csi_packet(frame, ts_sec, ts_usec):
    if len(frame) < 42:
        return None
    eth_type = struct.unpack("!H", frame[12:14])[0]
    if eth_type != 0x0800:
        return None
    ip_start = 14
    ihl = (frame[ip_start] & 0x0F) * 4
    if ihl < 20:
        return None
    proto = frame[ip_start + 9]
    if proto != 17:
        return None
    udp_start = ip_start + ihl
    if len(frame) < udp_start + 8:
        return None
    dst_port = struct.unpack("!H", frame[udp_start + 2:udp_start + 4])[0]
    if dst_port != 5500:
        return None
    payload = frame[udp_start + 8:]
    if len(payload) < 18 or payload[0:2] != b"\x11\x11":
        return None

    rssi = struct.unpack("b", payload[2:3])[0]
    fc = payload[3]
    src_mac = mac_to_text(payload[4:10])
    seq = struct.unpack("<H", payload[10:12])[0]
    csiconf = struct.unpack("<H", payload[12:14])[0]
    chanspec = struct.unpack("<H", payload[14:16])[0]
    chip = struct.unpack("<H", payload[16:18])[0]
    csi = payload[18:]
    raw_words = len(csi) // 4
    if raw_words >= 256:
        nfft = 256
    elif raw_words >= 128:
        nfft = 128
    elif raw_words >= 64:
        nfft = 64
    else:
        nfft = raw_words
    if nfft <= 0:
        return None
    csi = csi[:nfft * 4]

    amps = []
    phases = []
    total = 0.0
    peak = 0.0
    for offset in range(0, nfft * 4, 4):
        real, imag = struct.unpack_from("<hh", csi, offset)
        amp = math.sqrt(float(real * real + imag * imag))
        phase = math.atan2(imag, real)
        amps.append(amp)
        phases.append(phase)
        total += amp
        if amp > peak:
            peak = amp

    avg_amp = total / len(amps)
    return {
        "ts": ts_sec * 1000 + int(ts_usec / 1000),
        "receivedAt": now_ms(),
        "sourceMac": src_mac,
        "rssi": rssi,
        "frameControl": fc,
        "seq": seq,
        "csiconf": csiconf,
        "chanspec": f"0x{chanspec:04x}",
        "chip": f"0x{chip:04x}",
        "nfft": nfft,
        "avgAmp": avg_amp,
        "peakAmp": peak,
        "amps": amps,
        "phases": phases,
    }


class EventHub:
    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=20)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def publish(self, event, payload):
        item = (event, payload)
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                if q.full():
                    q.get_nowait()
                q.put_nowait(item)
            except queue.Empty:
                pass
            except queue.Full:
                pass


class CaptureManager:
    def __init__(self, hub):
        self.hub = hub
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.process = None
        self.thread = None
        self.stderr_thread = None
        self.logs = collections.deque(maxlen=MAX_LOG_LINES)
        self.packet_times = collections.deque()
        self.ml_window = collections.deque()
        self.ml_model = load_live_model()
        self.last_amps = None
        self.last_publish = 0.0
        self.dataset_frame_counter = 0
        self.capture_path = None
        self.dataset_path = None
        self.dataset_file = None
        self.params = {}
        self.state = {
            "running": False,
            "frames": 0,
            "datasetSamples": 0,
            "packetRate": 0.0,
            "motionScore": 0.0,
            "latest": None,
            "ml": None,
            "error": None,
            "capturePath": None,
            "datasetPath": None,
            "startedAt": None,
            "stoppedAt": None,
        }
        self.state["ml"] = self._model_status_locked()

    def start(
        self,
        channel=DEFAULT_CHANNEL,
        source_mac=DEFAULT_SOURCE_MAC,
        distance_m=DEFAULT_DISTANCE_M,
        label="",
        note="",
    ):
        with self.lock:
            if self.state["running"]:
                return self._snapshot_locked()
            self.stop_event.clear()
            self.logs.clear()
            self.packet_times.clear()
            self.ml_window.clear()
            self.ml_model = load_live_model()
            self.last_amps = None
            self.last_publish = 0.0
            self.dataset_frame_counter = 0
            started = time.strftime("%Y%m%d-%H%M%S")
            self.capture_path = CAPTURE_DIR / f"csi-alpha-web-{started}.pcap"
            label_clean = clean_label(label)
            self.dataset_path = None
            if label_clean:
                self.dataset_path = DATASET_DIR / f"{label_clean}-{started}.ndjson"
            self.params = {
                "channel": str(channel or DEFAULT_CHANNEL),
                "sourceMac": str(source_mac or DEFAULT_SOURCE_MAC).lower(),
                "distanceM": float(distance_m or DEFAULT_DISTANCE_M),
                "label": label_clean,
                "note": str(note or "").strip(),
            }
            self.state.update({
                "running": True,
                "frames": 0,
                "datasetSamples": 0,
                "packetRate": 0.0,
                "motionScore": 0.0,
                "latest": None,
                "ml": self._model_status_locked(),
                "error": None,
                "capturePath": str(self.capture_path),
                "datasetPath": str(self.dataset_path) if self.dataset_path else None,
                "startedAt": now_ms(),
                "stoppedAt": None,
            })
            self.thread = threading.Thread(target=self._run, daemon=True)
            payload = self._snapshot_locked()
        self.thread.start()
        return payload

    def stop(self):
        self.stop_event.set()
        proc = self.process
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception:
                proc.terminate()
        with self.lock:
            self.state["running"] = False
            self.state["stoppedAt"] = now_ms()
            self.state["error"] = None
            payload = self._snapshot_locked()
        threading.Thread(target=self._finish_stop, daemon=True).start()
        self.hub.publish("status", payload)
        return payload

    def status(self):
        with self.lock:
            return self._snapshot_locked()

    def _snapshot_locked(self):
        payload = dict(self.state)
        payload["params"] = dict(self.params)
        payload["logs"] = list(self.logs)[-20:]
        return payload

    def _model_status_locked(self):
        if not self.ml_model:
            return {
                "model": None,
                "label": "model_yok",
                "active": False,
                "confidence": 0.0,
                "handMotionProbability": 0.0,
            }
        return {
            "model": self.ml_model.get("model"),
            "label": "hazir",
            "active": False,
            "confidence": 0.0,
            "handMotionProbability": 0.0,
            "threshold": self.ml_model.get("threshold"),
            "window": self.ml_model.get("window"),
        }

    def _finish_stop(self):
        thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        self._cleanup_radio()
        self._close_dataset()
        self._set_stopped()

    def _log(self, line):
        text = line.rstrip()
        if not text:
            return
        with self.lock:
            self.logs.append(text)
        self.hub.publish("status", self.status())

    def _read_stderr(self, stream):
        try:
            for raw in iter(stream.readline, b""):
                self._log(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            self._log(f"stderr reader stopped: {exc}")

    def _set_stopped(self, error=None):
        with self.lock:
            self.state["running"] = False
            self.state["stoppedAt"] = now_ms()
            self.state["error"] = str(error) if error else None
        self.hub.publish("status", self.status())

    def _run(self):
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        DATASET_DIR.mkdir(parents=True, exist_ok=True)
        args = [
            str(STREAM_SCRIPT),
            self.params["channel"],
            self.params["sourceMac"],
            str(self.params["distanceM"]),
        ]
        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
            self.stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(self.process.stderr,),
                daemon=True,
            )
            self.stderr_thread.start()
            self._open_dataset()
            self._read_pcap_stream(self.process.stdout)
            rc = self.process.wait(timeout=2)
            self._cleanup_radio()
            self._close_dataset()
            if not self.stop_event.is_set() and rc != 0:
                self._set_stopped(f"capture exited with code {rc}")
            else:
                self._set_stopped()
        except Exception as exc:
            proc = self.process
            if proc and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
                    proc.terminate()
            self._cleanup_radio()
            self._close_dataset()
            if self.stop_event.is_set():
                self._set_stopped()
            else:
                self._set_stopped(exc)

    def _read_pcap_stream(self, stream):
        header = read_exact(stream, 24, self.stop_event)
        magic = header[:4]
        if magic == b"\xd4\xc3\xb2\xa1":
            endian = "<"
        elif magic == b"\xa1\xb2\xc3\xd4":
            endian = ">"
        elif magic == b"\x4d\x3c\xb2\xa1":
            endian = "<"
        elif magic == b"\xa1\xb2\x3c\x4d":
            endian = ">"
        else:
            raise ValueError(f"unexpected pcap magic {magic.hex()}")

        with open(self.capture_path, "wb") as out:
            out.write(header)
            out.flush()
            while not self.stop_event.is_set():
                pkt_header = read_exact(stream, 16, self.stop_event)
                ts_sec, ts_usec, incl_len, orig_len = struct.unpack(endian + "IIII", pkt_header)
                frame = read_exact(stream, incl_len, self.stop_event)
                out.write(pkt_header)
                out.write(frame)
                sample = parse_csi_packet(frame, ts_sec, ts_usec)
                if sample:
                    self._handle_sample(sample)

    def _cleanup_radio(self):
        try:
            subprocess.run(
                [str(BASE_DIR / "stop_rx.sh")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=12,
                check=False,
            )
        except Exception as exc:
            self._log(f"radio cleanup skipped: {exc}")

    def _open_dataset(self):
        if not self.dataset_path:
            return
        DATASET_DIR.mkdir(parents=True, exist_ok=True)
        self.dataset_file = open(self.dataset_path, "a", encoding="utf-8", buffering=1)
        header = {
            "type": "session",
            "createdAt": now_ms(),
            "channel": self.params.get("channel"),
            "sourceMac": self.params.get("sourceMac"),
            "distanceM": self.params.get("distanceM"),
            "label": self.params.get("label"),
            "note": self.params.get("note"),
            "pcap": str(self.capture_path) if self.capture_path else None,
            "tones": DATASET_TONES,
            "feature": "log10_amplitude",
        }
        self.dataset_file.write(json.dumps(header, separators=(",", ":")) + "\n")

    def _close_dataset(self):
        if self.dataset_file:
            try:
                self.dataset_file.flush()
                self.dataset_file.close()
            finally:
                self.dataset_file = None

    def _write_dataset_sample(self, sample, motion, packet_rate):
        if not self.dataset_file:
            return
        self.dataset_frame_counter += 1
        if self.dataset_frame_counter % DATASET_FRAME_STRIDE != 0:
            return
        record = {
            "type": "sample",
            "ts": sample["ts"],
            "receivedAt": sample["receivedAt"],
            "label": self.params.get("label"),
            "distanceM": self.params.get("distanceM"),
            "sourceMac": sample["sourceMac"],
            "rssi": sample["rssi"],
            "seq": sample["seq"],
            "packetRate": round(packet_rate, 2),
            "motionScore": round(motion, 5),
            "amps": round_series(log_amp_series(downsample(sample["amps"], DATASET_TONES)), 5),
        }
        self.dataset_file.write(json.dumps(record, separators=(",", ":")) + "\n")
        with self.lock:
            self.state["datasetSamples"] += 1

    def _infer_live_model(self, motion):
        model = self.ml_model
        if not model:
            return self._model_status_locked()
        window = int(model.get("window", 32))
        if self.ml_window.maxlen != window:
            self.ml_window = collections.deque(self.ml_window, maxlen=window)
        self.ml_window.append(float(motion))
        if len(self.ml_window) < window:
            return {
                "model": model.get("model"),
                "label": "ısınıyor",
                "active": False,
                "confidence": 0.0,
                "handMotionProbability": 0.0,
                "threshold": model.get("threshold"),
                "windowReady": len(self.ml_window),
                "window": window,
            }
        values = list(self.ml_window)
        sorted_values = sorted(values)
        p90_index = min(len(sorted_values) - 1, int(0.9 * (len(sorted_values) - 1)))
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        features_by_name = {
            "motion_mean": mean,
            "motion_max": max(values),
            "motion_p90": sorted_values[p90_index],
            "motion_std": math.sqrt(variance),
        }
        names = model.get("featureNames", [])
        raw_features = [features_by_name.get(name, 0.0) for name in names]
        feature_mean = model.get("featureMean", [])
        feature_std = model.get("featureStd", [])
        weights = model.get("weights", [])
        normalized = [
            (value - feature_mean[idx]) / max(1e-6, feature_std[idx])
            for idx, value in enumerate(raw_features)
        ]
        logit = sum(value * weights[idx] for idx, value in enumerate(normalized)) + float(model.get("bias", 0.0))
        probability = sigmoid(logit)
        threshold = float(model.get("threshold", 0.5))
        active = probability >= threshold
        return {
            "model": model.get("model"),
            "label": "hand_motion" if active else "stable",
            "active": active,
            "confidence": probability if active else 1.0 - probability,
            "handMotionProbability": probability,
            "threshold": threshold,
            "features": {
                name: round(raw_features[idx], 5)
                for idx, name in enumerate(names)
            },
            "windowReady": len(self.ml_window),
            "window": window,
        }

    def _handle_sample(self, sample):
        t = time.time()
        self.packet_times.append(t)
        while self.packet_times and self.packet_times[0] < t - 1.0:
            self.packet_times.popleft()
        packet_rate = float(len(self.packet_times))

        amps = sample["amps"]
        motion = 0.0
        if self.last_amps and len(self.last_amps) == len(amps):
            denom = max(1.0, sum(self.last_amps) / len(self.last_amps))
            diff = sum(abs(a - b) for a, b in zip(amps, self.last_amps)) / len(amps)
            motion = min(1.0, diff / denom)
        self.last_amps = amps
        sample["packetRate"] = packet_rate
        sample["motionScore"] = motion
        sample["classification"] = classify(motion, sample["avgAmp"], packet_rate)
        sample["ml"] = self._infer_live_model(motion)
        self._write_dataset_sample(sample, motion, packet_rate)

        with self.lock:
            self.state["frames"] += 1
            self.state["packetRate"] = packet_rate
            self.state["motionScore"] = motion
            slim = dict(sample)
            slim.pop("amps", None)
            slim.pop("phases", None)
            self.state["latest"] = slim
            self.state["ml"] = sample["ml"]

        if t - self.last_publish >= PUBLISH_INTERVAL_S:
            self.last_publish = t
            payload = dict(sample)
            payload["amps"] = round_series(downsample(sample["amps"], VISUAL_TONES), 2)
            payload.pop("phases", None)
            self.hub.publish("sample", payload)


def downsample(values, count):
    if len(values) <= count:
        return values
    bucket = len(values) / float(count)
    result = []
    for idx in range(count):
        start = int(idx * bucket)
        end = max(start + 1, int((idx + 1) * bucket))
        segment = values[start:end]
        result.append(sum(segment) / len(segment))
    return result


HUB = EventHub()
CAPTURE = CaptureManager(HUB)


class Handler(BaseHTTPRequestHandler):
    server_version = "CSIWeb/0.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
        elif parsed.path == "/app.js":
            self._send_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
        elif parsed.path == "/style.css":
            self._send_file(WEB_DIR / "style.css", "text/css; charset=utf-8")
        elif parsed.path == "/api/status":
            self._send_json(CAPTURE.status())
        elif parsed.path == "/api/files":
            self._send_json(list_record_files())
        elif parsed.path == "/download":
            params = parse_qs(parsed.query)
            kind = (params.get("kind") or [""])[0]
            name = unquote((params.get("name") or [""])[0])
            self._send_download(kind, name)
        elif parsed.path == "/events":
            self._events()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        data = {}
        if body:
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST, "invalid json")
                return
        if parsed.path == "/api/start":
            status = CAPTURE.start(
                data.get("channel", DEFAULT_CHANNEL),
                data.get("sourceMac", DEFAULT_SOURCE_MAC),
                data.get("distanceM", DEFAULT_DISTANCE_M),
                data.get("label", ""),
                data.get("note", ""),
            )
            self._send_json(status)
        elif parsed.path == "/api/stop":
            self._send_json(CAPTURE.stop())
        elif parsed.path == "/api/delete":
            kind = str(data.get("kind", ""))
            name = str(data.get("name", ""))
            path = safe_record_path(kind, name)
            if not path:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                path.unlink()
            except OSError as exc:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json({"ok": True, "kind": kind, "name": name, "files": list_record_files()})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, fmt, *args):
        return

    def _send_json(self, payload, status=HTTPStatus.OK):
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path, content_type):
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_download(self, kind, name):
        path = safe_record_path(kind, name)
        if not path:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            stat = path.stat()
            handle = path.open("rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        with handle:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type_for(path))
            self.send_header("Content-Length", str(stat.st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _write_sse(self, event, payload):
        raw = f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")
        self.wfile.write(raw)
        self.wfile.flush()

    def _events(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = HUB.subscribe()
        try:
            self._write_sse("status", CAPTURE.status())
            while True:
                try:
                    event, payload = q.get(timeout=1.0)
                    self._write_sse(event, payload)
                except queue.Empty:
                    self._write_sse("status", CAPTURE.status())
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            HUB.unsubscribe(q)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"CSI web UI listening on http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        CAPTURE.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
