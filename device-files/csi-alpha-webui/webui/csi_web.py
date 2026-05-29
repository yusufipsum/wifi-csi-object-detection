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

try:
    import torch
    from torch import nn
except Exception as exc:
    torch = None
    nn = None
    TORCH_IMPORT_ERROR = str(exc)
else:
    TORCH_IMPORT_ERROR = None


BASE_DIR = Path("/home/admin/csi")
WEB_DIR = BASE_DIR / "webui"
CAPTURE_DIR = BASE_DIR / "captures"
DATASET_DIR = BASE_DIR / "datasets"
MODEL_DIR = BASE_DIR / "models"
LIVE_MODEL_PATH = MODEL_DIR / "hand_motion_live_model.json"
DEEP_MODEL_PATH = MODEL_DIR / "best_csi_cnn_lstm_temporal.pt"
ALARM_DIR = BASE_DIR / "alarms"
ALARM_LOG_PATH = ALARM_DIR / "alarm-events.ndjson"
STREAM_SCRIPT = BASE_DIR / "start_rx_stream.sh"
DEFAULT_CHANNEL = "48/80"
DEFAULT_SOURCE_MAC = "88:a2:9e:5d:4e:a6"
DEFAULT_DISTANCE_M = 2.0
PUBLISH_INTERVAL_S = 0.10
DATASET_FRAME_STRIDE = 6
MODEL_INFER_STRIDE = DATASET_FRAME_STRIDE
VISUAL_TONES = 96
DATASET_TONES = 128
MAX_LOG_LINES = 80
ALARM_LABELS = {"passage", "hand_motion"}
ALARM_COOLDOWN_S = 5.0
ALARM_CONFIDENCE_THRESHOLD = 0.85
ALARM_MIN_MOTION_SCORE = 0.05
ALARM_MIN_PACKET_RATE = 5.0
ALARM_STREAK_REQUIRED = 2
CAPTURE_PROFILE = "multiscale_physical_v1"
RECOMMENDED_TRAINING_WINDOW = 16
RECOMMENDED_TRAINING_WINDOWS = [16, 48]


def alarm_policy():
    return {
        "confidence": ALARM_CONFIDENCE_THRESHOLD,
        "motionScore": ALARM_MIN_MOTION_SCORE,
        "packetRate": ALARM_MIN_PACKET_RATE,
        "streak": ALARM_STREAK_REQUIRED,
        "cooldownS": ALARM_COOLDOWN_S,
        "inferStride": MODEL_INFER_STRIDE,
        "profile": CAPTURE_PROFILE,
        "trainingWindows": RECOMMENDED_TRAINING_WINDOWS,
    }


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


def unwrap_phase_series(values):
    if not values:
        return []
    unwrapped = [float(values[0])]
    offset = 0.0
    previous = float(values[0])
    two_pi = 2.0 * math.pi
    for value in values[1:]:
        current = float(value) + offset
        delta = current - previous
        if delta > math.pi:
            offset -= two_pi
            current -= two_pi
        elif delta < -math.pi:
            offset += two_pi
            current += two_pi
        unwrapped.append(current)
        previous = current
    return unwrapped


def phase_residual_series(values):
    unwrapped = unwrap_phase_series(values)
    n = len(unwrapped)
    if n <= 1:
        return unwrapped
    sum_x = n * (n - 1) / 2.0
    sum_x2 = (n - 1) * n * (2 * n - 1) / 6.0
    sum_y = sum(unwrapped)
    sum_xy = sum(idx * value for idx, value in enumerate(unwrapped))
    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-12:
        slope = 0.0
    else:
        slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return [
        value - (slope * idx + intercept)
        for idx, value in enumerate(unwrapped)
    ]


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


class CsiCnnLstmLive(nn.Module if nn else object):
    def __init__(
        self,
        tones,
        classes,
        input_channels=1,
        conv_channels=32,
        hidden=64,
        dropout=0.35,
        bidirectional=True,
    ):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv1d(input_channels, conv_channels, kernel_size=7, padding=3),
            nn.BatchNorm1d(conv_channels),
            nn.GELU(),
            nn.MaxPool1d(2),
            nn.Dropout(dropout * 0.35),
            nn.Conv1d(conv_channels, conv_channels * 2, kernel_size=5, padding=2),
            nn.BatchNorm1d(conv_channels * 2),
            nn.GELU(),
            nn.AdaptiveAvgPool1d(16),
            nn.Flatten(),
        )
        self.lstm = nn.LSTM(
            conv_channels * 2 * 16,
            hidden,
            batch_first=True,
            bidirectional=bidirectional,
            dropout=0.0,
        )
        lstm_out = hidden * (2 if bidirectional else 1)
        self.classifier = nn.Sequential(
            nn.LayerNorm(lstm_out),
            nn.Dropout(dropout),
            nn.Linear(lstm_out, classes),
        )

    def forward(self, x):
        if x.dim() == 3:
            batch, steps, tones = x.shape
            x = x.reshape(batch * steps, 1, tones)
        else:
            batch, steps, channels, tones = x.shape
            x = x.reshape(batch * steps, channels, tones)
        x = self.encoder(x)
        x = x.reshape(batch, steps, -1)
        out, _ = self.lstm(x)
        return self.classifier(out[:, -1, :])


class FrameEncoderLive(nn.Module if nn else object):
    def __init__(self, input_channels=1, conv_channels=32, dropout=0.35):
        super().__init__()
        self.output_dim = conv_channels * 2 * 16
        self.net = nn.Sequential(
            nn.Conv1d(input_channels, conv_channels, kernel_size=7, padding=3),
            nn.BatchNorm1d(conv_channels),
            nn.GELU(),
            nn.MaxPool1d(2),
            nn.Dropout(dropout * 0.35),
            nn.Conv1d(conv_channels, conv_channels * 2, kernel_size=5, padding=2),
            nn.BatchNorm1d(conv_channels * 2),
            nn.GELU(),
            nn.AdaptiveAvgPool1d(16),
            nn.Flatten(),
        )

    def forward(self, x):
        return self.net(x)


class CsiMultiScaleCnnLstmLive(nn.Module if nn else object):
    def __init__(
        self,
        windows,
        classes,
        input_channels=1,
        conv_channels=32,
        hidden=64,
        dropout=0.35,
        bidirectional=True,
    ):
        super().__init__()
        self.windows = [int(window) for window in windows]
        self.encoder = FrameEncoderLive(input_channels, conv_channels, dropout)
        self.lstms = nn.ModuleDict({
            str(window): nn.LSTM(
                self.encoder.output_dim,
                hidden,
                batch_first=True,
                bidirectional=bidirectional,
                dropout=0.0,
            )
            for window in self.windows
        })
        branch_dim = hidden * (2 if bidirectional else 1)
        self.classifier = nn.Sequential(
            nn.LayerNorm(branch_dim * len(self.windows)),
            nn.Dropout(dropout),
            nn.Linear(branch_dim * len(self.windows), classes),
        )

    def _encode_sequence(self, x):
        if x.dim() == 3:
            batch, steps, tones = x.shape
            x = x.reshape(batch * steps, 1, tones)
        elif x.dim() == 4:
            batch, steps, channels, tones = x.shape
            x = x.reshape(batch * steps, channels, tones)
        else:
            raise ValueError(f"expected 3D or 4D input, got shape {tuple(x.shape)}")
        encoded = self.encoder(x)
        return encoded.reshape(batch, steps, -1)

    def forward(self, inputs):
        branches = []
        for window, x in zip(self.windows, inputs):
            encoded = self._encode_sequence(x)
            out, _ = self.lstms[str(window)](encoded)
            branches.append(out[:, -1, :])
        return self.classifier(torch.cat(branches, dim=1))


class DeepCsiModel:
    def __init__(self, path=DEEP_MODEL_PATH):
        self.path = Path(path)
        self.error = None
        self.model_name = "csi_cnn_lstm_temporal_v2"
        self.model = None
        self.labels = []
        self.window = 24
        self.windows = [self.window]
        self.is_multiscale = False
        self.tones = DATASET_TONES
        self.input_channels = 1
        self.feature_names = ["amp"]
        self.buffer = collections.deque(maxlen=self.window)
        self.previous_amp = None
        self.previous_phase = None
        self._load()

    def _load(self):
        if torch is None or nn is None:
            self.error = f"torch_missing: {TORCH_IMPORT_ERROR}"
            return
        if not self.path.exists():
            self.error = f"model_missing: {self.path}"
            return
        try:
            checkpoint = torch.load(str(self.path), map_location="cpu", weights_only=False)
            self.model_name = str(checkpoint.get("model") or self.model_name)
            self.labels = [str(label) for label in checkpoint.get("labels", [])]
            raw_windows = checkpoint.get("windows")
            if raw_windows is not None:
                self.windows = sorted({int(window) for window in raw_windows})
            else:
                self.windows = [int(checkpoint.get("window", 24))]
            self.window = max(self.windows)
            self.tones = int(checkpoint.get("tones", DATASET_TONES))
            self.input_channels = int(checkpoint.get("inputChannels") or checkpoint.get("channels") or 1)
            self.feature_names = [
                str(value)
                for value in checkpoint.get("featureNames", ["amp"])
            ]
            bidirectional = bool(checkpoint.get("bidirectional", True))
            self.is_multiscale = (
                self.model_name.startswith("csi_cnn_lstm_multiscale")
                or len(self.windows) > 1
            )
            if self.is_multiscale:
                self.model = CsiMultiScaleCnnLstmLive(
                    windows=self.windows,
                    classes=len(self.labels),
                    input_channels=self.input_channels,
                    bidirectional=bidirectional,
                )
            else:
                self.model = CsiCnnLstmLive(
                    tones=self.tones,
                    classes=len(self.labels),
                    input_channels=self.input_channels,
                    bidirectional=bidirectional,
                )
            self.model.load_state_dict(checkpoint["model_state"])
            self.model.eval()
            self.buffer = collections.deque(maxlen=self.window)
        except Exception as exc:
            self.model = None
            self.error = f"model_load_failed: {exc}"

    def clear(self):
        self.buffer.clear()
        self.previous_amp = None
        self.previous_phase = None

    def status(self):
        if self.model is None:
            return {
                "model": None,
                "label": "model_yok",
                "active": False,
                "confidence": 0.0,
                "error": self.error,
                "windowReady": len(self.buffer),
                "window": self.window,
                "windows": self.windows,
                "tones": self.tones,
                "inputChannels": self.input_channels,
                "featureNames": self.feature_names,
            }
        return {
            "model": self.model_name,
            "label": "hazir",
            "active": False,
            "confidence": 0.0,
            "windowReady": len(self.buffer),
            "window": self.window,
            "windows": self.windows,
            "tones": self.tones,
            "inputChannels": self.input_channels,
            "featureNames": self.feature_names,
            "labels": self.labels,
            "alarmLabels": sorted(ALARM_LABELS),
            "alarmPolicy": alarm_policy(),
        }

    def _feature_frame(self, sample_or_amps):
        if isinstance(sample_or_amps, dict):
            sample = sample_or_amps
            amps = sample.get("amps", [])
            phases = sample.get("phases", [])
        else:
            amps = sample_or_amps
            phases = []
        amp = log_amp_series(downsample(amps, self.tones))
        if len(amp) != self.tones:
            return None, f"expected {self.tones} tones, got {len(amp)}"
        phase = []
        if phases:
            phase = downsample(phase_residual_series(phases), self.tones)
        channels = []
        for name in self.feature_names or ["amp"]:
            if name == "amp":
                channels.append(amp)
            elif name == "phase":
                if len(phase) != self.tones:
                    return None, "phase feature requested but phases are missing"
                channels.append(phase)
            elif name == "amp_delta":
                if self.previous_amp is None:
                    channels.append([0.0] * self.tones)
                else:
                    channels.append([a - b for a, b in zip(amp, self.previous_amp)])
            elif name == "phase_delta":
                if len(phase) != self.tones:
                    return None, "phase_delta feature requested but phases are missing"
                if self.previous_phase is None:
                    channels.append([0.0] * self.tones)
                else:
                    channels.append([a - b for a, b in zip(phase, self.previous_phase)])
            else:
                return None, f"unsupported live feature: {name}"
        if len(channels) != self.input_channels:
            return None, f"checkpoint expects {self.input_channels} channels, built {len(channels)}"
        self.previous_amp = amp
        if len(phase) == self.tones:
            self.previous_phase = phase
        if len(channels) == 1:
            return channels[0], None
        return channels, None

    def infer(self, sample_or_amps):
        if self.model is None:
            return self.status()
        frame, error = self._feature_frame(sample_or_amps)
        if error:
            return {
                **self.status(),
                "label": "boyut_hatasi",
                "error": error,
            }
        self.buffer.append(frame)
        if len(self.buffer) < self.window:
            return {
                "model": self.model_name,
                "label": "ısınıyor",
                "active": False,
                "confidence": 0.0,
                "windowReady": len(self.buffer),
                "window": self.window,
                "windows": self.windows,
                "tones": self.tones,
                "inputChannels": self.input_channels,
                "featureNames": self.feature_names,
                "labels": self.labels,
                "alarmLabels": sorted(ALARM_LABELS),
                "alarmPolicy": alarm_policy(),
            }
        frames = list(self.buffer)
        with torch.no_grad():
            if self.is_multiscale:
                inputs = []
                for window in self.windows:
                    matrix = torch.tensor(frames[-window:], dtype=torch.float32)
                    matrix = (matrix - matrix.mean(dim=0, keepdim=True)) / (
                        matrix.std(dim=0, keepdim=True, unbiased=False) + 1e-6
                    )
                    inputs.append(matrix.unsqueeze(0))
                logits = self.model(inputs)
            else:
                matrix = torch.tensor(frames, dtype=torch.float32)
                matrix = (matrix - matrix.mean(dim=0, keepdim=True)) / (
                    matrix.std(dim=0, keepdim=True, unbiased=False) + 1e-6
                )
                logits = self.model(matrix.unsqueeze(0))
            probs = torch.softmax(logits, dim=1).squeeze(0).cpu().tolist()
        best_idx = max(range(len(probs)), key=lambda idx: probs[idx])
        label = self.labels[best_idx]
        confidence = float(probs[best_idx])
        return {
            "model": self.model_name,
            "label": label,
            "active": label in ALARM_LABELS,
            "confidence": confidence,
            "probabilities": {
                self.labels[idx]: round(float(prob), 4)
                for idx, prob in enumerate(probs)
            },
            "windowReady": len(self.buffer),
            "window": self.window,
            "windows": self.windows,
            "tones": self.tones,
            "inputChannels": self.input_channels,
            "featureNames": self.feature_names,
            "labels": self.labels,
            "alarmLabels": sorted(ALARM_LABELS),
            "alarmPolicy": alarm_policy(),
        }


class AlarmStore:
    def __init__(self, path=ALARM_LOG_PATH, max_cache=250):
        self.path = Path(path)
        self.max_cache = max_cache
        self.lock = threading.RLock()
        self.events = collections.deque(maxlen=max_cache)
        self._load()

    def _load(self):
        self.events.clear()
        if not self.path.exists():
            return
        try:
            rows = []
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        rows.append(json.loads(line))
            for row in rows[-self.max_cache:]:
                self.events.append(row)
        except Exception:
            self.events.clear()

    def list(self):
        with self.lock:
            return list(reversed(self.events))

    def append(self, event):
        with self.lock:
            ALARM_DIR.mkdir(parents=True, exist_ok=True)
            self.events.append(event)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            return event

    def delete(self, alarm_id):
        with self.lock:
            kept = [event for event in self.events if event.get("id") != alarm_id]
            removed = len(kept) != len(self.events)
            self.events = collections.deque(kept, maxlen=self.max_cache)
            self._rewrite()
            return removed

    def clear(self):
        with self.lock:
            self.events.clear()
            self._rewrite()

    def _rewrite(self):
        ALARM_DIR.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for event in self.events:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


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
        self.deep_model = DeepCsiModel()
        self.last_alarm_at = {}
        self.last_model_result = None
        self.inference_frame_counter = 0
        self.alarm_streak_label = None
        self.alarm_streak_count = 0
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
            "alarms": [],
            "error": None,
            "capturePath": None,
            "datasetPath": None,
            "startedAt": None,
            "stoppedAt": None,
        }
        self.state["ml"] = self._model_status_locked()
        self.state["alarms"] = ALARMS.list()

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
            self.deep_model = DeepCsiModel()
            self.deep_model.clear()
            self.last_alarm_at.clear()
            self.last_model_result = None
            self.inference_frame_counter = 0
            self.alarm_streak_label = None
            self.alarm_streak_count = 0
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
                "alarms": ALARMS.list(),
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
        payload["alarms"] = ALARMS.list()
        return payload

    def _model_status_locked(self):
        if self.deep_model:
            status = self.deep_model.status()
            if status.get("model") or status.get("error"):
                return status
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
            "schemaVersion": 2,
            "profile": CAPTURE_PROFILE,
            "datasetFrameStride": DATASET_FRAME_STRIDE,
            "recommendedTrainingWindow": RECOMMENDED_TRAINING_WINDOW,
            "recommendedTrainingWindows": RECOMMENDED_TRAINING_WINDOWS,
            "feature": "log10_amplitude+linear_detrended_phase",
            "features": ["amps", "phaseResiduals"],
            "phaseCalibration": "unwrap_per_frame_then_remove_linear_trend_over_tones",
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
        amps = round_series(log_amp_series(downsample(sample["amps"], DATASET_TONES)), 5)
        phase_residuals = round_series(
            downsample(phase_residual_series(sample.get("phases") or []), DATASET_TONES),
            5,
        )
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
            "amps": amps,
            "phaseResiduals": phase_residuals,
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

    def _infer_model(self, sample, motion):
        if self.deep_model:
            self.inference_frame_counter += 1
            should_infer = (
                self.inference_frame_counter == 1
                or self.inference_frame_counter % MODEL_INFER_STRIDE == 0
                or not self.last_model_result
            )
            if should_infer:
                result = self.deep_model.infer(sample)
                result["skipped"] = False
                result["inferenceStride"] = MODEL_INFER_STRIDE
                result["alarmPolicy"] = alarm_policy()
                self.last_model_result = dict(result)
            else:
                result = dict(self.last_model_result)
                result["skipped"] = True
                result["windowReady"] = len(self.deep_model.buffer)
                result["inferenceStride"] = MODEL_INFER_STRIDE
                result["alarmPolicy"] = alarm_policy()
            if result.get("model") or result.get("error"):
                return result
        return self._infer_live_model(motion)

    def _maybe_record_alarm(self, sample, info):
        if not info:
            return None
        raw_active = bool(info.get("active"))
        info["rawActive"] = raw_active
        info["alarmPolicy"] = alarm_policy()
        if info.get("skipped"):
            info["active"] = False
            info["alarmSuppressed"] = "stride"
            return None
        label = str(info.get("label") or "")
        if not raw_active or label not in ALARM_LABELS:
            info["active"] = False
            self.alarm_streak_label = None
            self.alarm_streak_count = 0
            return None
        confidence = float(info.get("confidence") or 0.0)
        motion_score = float(sample.get("motionScore") or 0.0)
        packet_rate = float(sample.get("packetRate") or 0.0)
        suppress_reason = None
        if confidence < ALARM_CONFIDENCE_THRESHOLD:
            suppress_reason = "low_confidence"
        elif motion_score < ALARM_MIN_MOTION_SCORE:
            suppress_reason = "low_motion"
        elif packet_rate < ALARM_MIN_PACKET_RATE:
            suppress_reason = "low_packet_rate"
        if suppress_reason:
            info["active"] = False
            info["alarmSuppressed"] = suppress_reason
            self.alarm_streak_label = None
            self.alarm_streak_count = 0
            return None
        if label == self.alarm_streak_label:
            self.alarm_streak_count += 1
        else:
            self.alarm_streak_label = label
            self.alarm_streak_count = 1
        info["alarmStreak"] = self.alarm_streak_count
        if self.alarm_streak_count < ALARM_STREAK_REQUIRED:
            info["active"] = False
            info["alarmSuppressed"] = "streak"
            return None
        info["active"] = True
        now = time.time()
        previous = self.last_alarm_at.get(label, 0.0)
        if now - previous < ALARM_COOLDOWN_S:
            info["alarmSuppressed"] = "cooldown"
            return None
        self.last_alarm_at[label] = now
        event = {
            "id": f"{now_ms()}-{sample.get('seq', 'na')}-{label}",
            "ts": now_ms(),
            "label": label,
            "confidence": round(confidence, 4),
            "model": info.get("model"),
            "window": info.get("window"),
            "alarmStreak": self.alarm_streak_count,
            "alarmPolicy": alarm_policy(),
            "sourceMac": sample.get("sourceMac"),
            "seq": sample.get("seq"),
            "rssi": sample.get("rssi"),
            "motionScore": round(motion_score, 5),
            "packetRate": round(packet_rate, 2),
            "probabilities": info.get("probabilities") or {},
        }
        ALARMS.append(event)
        with self.lock:
            self.state["alarms"] = ALARMS.list()
        self.hub.publish("alarm", event)
        return event

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
        sample["ml"] = self._infer_model(sample, motion)
        self._maybe_record_alarm(sample, sample["ml"])
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
ALARMS = AlarmStore()
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
        elif parsed.path == "/api/alarms":
            self._send_json({"alarms": ALARMS.list()})
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
        elif parsed.path == "/api/delete-alarm":
            if data.get("all"):
                ALARMS.clear()
                CAPTURE.hub.publish("status", CAPTURE.status())
                self._send_json({"ok": True, "alarms": []})
                return
            alarm_id = str(data.get("id", ""))
            if not alarm_id:
                self.send_error(HTTPStatus.BAD_REQUEST, "missing alarm id")
                return
            removed = ALARMS.delete(alarm_id)
            CAPTURE.hub.publish("status", CAPTURE.status())
            self._send_json({"ok": removed, "alarms": ALARMS.list()})
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
    ALARM_DIR.mkdir(parents=True, exist_ok=True)
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
