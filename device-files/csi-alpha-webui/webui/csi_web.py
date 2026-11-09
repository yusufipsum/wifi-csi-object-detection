#!/usr/bin/env python3
import argparse
import collections
import json
import math
import os
import queue
import shutil
import signal
import struct
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import csi_sensing as sensing
import csi_network
import csi_nl80211
import csi_timeseries

try:
    import torch
    from torch import nn
except Exception as exc:
    torch = None
    nn = None
    TORCH_IMPORT_ERROR = str(exc)
else:
    TORCH_IMPORT_ERROR = None


BASE_DIR = Path(os.environ.get("CSI_BASE_DIR", "/home/admin/csi"))
WEB_DIR = Path(os.environ.get("CSI_WEB_DIR", str(BASE_DIR / "webui")))
# Yalniz gelistirme/test: ayarlanirsa gercek TX yerine sahte verici kullanilir ve bu MAC TX MAC sayilir.
DEV_TX_MAC = os.environ.get("CSI_DEV_TX_MAC", "").strip().lower() or None
CAPTURE_DIR = BASE_DIR / "captures"
DATASET_DIR = BASE_DIR / "datasets"
MODEL_DIR = BASE_DIR / "models"
LIVE_MODEL_PATH = MODEL_DIR / "hand_motion_live_model.json"
DEEP_MODEL_PATH = MODEL_DIR / "best_csi_cnn_lstm_temporal.pt"
REFLECTION_PROFILE_PATH = MODEL_DIR / "reflection_profiles.json"
ALARM_DIR = BASE_DIR / "alarms"
ALARM_LOG_PATH = ALARM_DIR / "alarm-events.ndjson"
STREAM_SCRIPT = BASE_DIR / "start_rx_stream.sh"
TIMED_REFLECTION_SCRIPT = BASE_DIR / "timed_reflection_measure.sh"
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
REFLECTION_PROFILE_LABELS = {"closed", "open", "partial_open", "weak_reflection"}
REFLECTION_CANONICAL_OPEN = {"open", "partial_open", "weak_reflection"}
REFLECTION_TONES = 128
REFLECTION_MAX_PACKETS = 20000
REFLECTION_FRAME_STRIDE = 2

# --- Tek kutu algilama (single_box) ---
# Alpha hem TX (AX210/mgmt0) hem RX (Broadcom/csi0 -> Nexmon CSI) olur; karar motoru csi_sensing.py.
CAPTURE_MODE_MOTION = "motion"
CAPTURE_MODE_SINGLE_BOX = "single_box"
LEGACY_MODE_ALIASES = {"reflection_live": CAPTURE_MODE_SINGLE_BOX}
LIVE_TX_IFACE = os.environ.get("CSI_TX_IFACE", "mgmt0")
LIVE_TX_PORT = 5501
LIVE_TX_RATE_PPS = 40.0
LIVE_TX_PAYLOAD_SIZE = 600
INJECT_MON_IFACE = os.environ.get("CSI_INJECT_IFACE", "csitx0")
CONFIG_DIR = BASE_DIR / "config"
SENSING_CONFIG_PATH = CONFIG_DIR / "sensing_config.json"
SENSING_CALIBRATION_PATH = MODEL_DIR / "sensing_calibration.json"
SENSING_PROFILES_PATH = MODEL_DIR / "sensing_profiles.json"
SENSING_TICK_S = 1.0
# Nexmon firmware zaman zaman CSI uretmeyi birakiyor (2026-11-09 16:55, 70 dk sonra; csi0'a hic paket
# gelmedi, AX210 ping'leri normal). Sinyal bir kez goruldukten sonra bu kadar sn kare gelmezse
# yakalama (surucu yeniden yuklemesiyle) kendiliginden yeniden baslatilir.
RX_STALL_RESTART_S = float(os.environ.get("CSI_RX_STALL_RESTART_S", "20"))
RX_RESTART_MIN_GAP_S = 120.0
HISTORY_ROWS = 1800          # 2 Hz karar x 15 dk
FRAME_HISTORY = 3000         # tam hizda son ~60 sn grup genligi (spektrogram/isi haritasi)
FRAME_PUBLISH_S = 0.25
# Sureli yansima olcumu (eski deney) pencere-seviyesi referanslari icin
LIVE_REFLECTION_WINDOW = 30
LIVE_REFLECTION_MARGIN = 0.08      # bu marjin altinda "uncertain"


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
        "iq": bytes(csi),  # ham I/Q (tek kutu motoru icin); JSON'a yazilmadan once cikarilir
    }


def iter_pcap_samples(path, max_packets=REFLECTION_MAX_PACKETS, frame_stride=REFLECTION_FRAME_STRIDE):
    path = Path(path)
    with path.open("rb") as handle:
        header = handle.read(24)
        if len(header) != 24:
            raise ValueError("pcap header is incomplete")
        magic = header[:4]
        if magic == b"\xd4\xc3\xb2\xa1":
            endian = "<"
            ts_scale = 1000
        elif magic == b"\xa1\xb2\xc3\xd4":
            endian = ">"
            ts_scale = 1000
        elif magic == b"\x4d\x3c\xb2\xa1":
            endian = "<"
            ts_scale = 1000000
        elif magic == b"\xa1\xb2\x3c\x4d":
            endian = ">"
            ts_scale = 1000000
        else:
            raise ValueError(f"unexpected pcap magic {magic.hex()}")

        seen = 0
        yielded = 0
        while True:
            pkt_header = handle.read(16)
            if not pkt_header:
                break
            if len(pkt_header) != 16:
                break
            ts_sec, ts_frac, incl_len, _orig_len = struct.unpack(endian + "IIII", pkt_header)
            frame = handle.read(incl_len)
            if len(frame) != incl_len:
                break
            seen += 1
            if frame_stride > 1 and seen % frame_stride != 0:
                continue
            sample = parse_csi_packet(frame, ts_sec, int(ts_frac / max(1, ts_scale // 1000)))
            if sample:
                yielded += 1
                yield sample
                if yielded >= max_packets:
                    break


def vector_mean(rows):
    if not rows:
        return []
    n = len(rows)
    width = len(rows[0])
    total = [0.0] * width
    for row in rows:
        for idx, value in enumerate(row):
            total[idx] += float(value)
    return [value / n for value in total]


def vector_std(rows, mean=None):
    if not rows:
        return []
    if mean is None:
        mean = vector_mean(rows)
    n = len(rows)
    width = len(rows[0])
    total = [0.0] * width
    for row in rows:
        for idx, value in enumerate(row):
            delta = float(value) - mean[idx]
            total[idx] += delta * delta
    return [math.sqrt(value / max(1, n)) for value in total]


def mean_value(values):
    return sum(values) / len(values) if values else 0.0


def std_value(values):
    if not values:
        return 0.0
    mean = mean_value(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def percentile_value(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def normalize_shape(values):
    mean = mean_value(values)
    std = max(1e-6, std_value(values))
    return [(float(value) - mean) / std for value in values]


def reflection_frame_vector(sample, previous_amp=None):
    amps = log_amp_series(downsample(sample.get("amps") or [], REFLECTION_TONES))
    if len(amps) != REFLECTION_TONES:
        return None, None
    phases = downsample(phase_residual_series(sample.get("phases") or []), REFLECTION_TONES)
    if len(phases) != REFLECTION_TONES:
        phases = [0.0] * REFLECTION_TONES
    amp_shape = normalize_shape(amps)
    phase_shape = normalize_shape(phases)
    amp_mean = mean_value(amps)
    amp_std = std_value(amps)
    phase_std = std_value(phases)
    amp_delta = 0.0
    if previous_amp and len(previous_amp) == len(amps):
        amp_delta = mean_value([abs(a - b) for a, b in zip(amps, previous_amp)])
    scalars = [
        amp_mean / 5.0,
        amp_std,
        phase_std / 4.0,
        amp_delta * 8.0,
        (float(sample.get("rssi") or -100.0) + 80.0) / 40.0,
    ]
    return [*amp_shape, *[0.35 * value for value in phase_shape], *scalars], amps


def reflection_fingerprint_from_pcap(path):
    return reflection_fingerprint_from_samples(iter_pcap_samples(path), path=path)


def iter_window_fingerprints_from_pcap(path, window=LIVE_REFLECTION_WINDOW):
    """Bir oturumu ardisik, ortusmeyen pencerelere bolup her pencere icin fingerprint uretir.

    Canli moddaki kisa pencere istatistikleri 60+ sn'lik oturum istatistiklerinden farkli
    dagildigi icin, canli karar bu pencere-seviyesi referanslarla yapilir.
    """
    buffer = []
    for sample in iter_pcap_samples(path):
        buffer.append(sample)
        if len(buffer) >= window:
            try:
                yield reflection_fingerprint_from_samples(buffer, path=path)["feature"]
            except ValueError:
                pass
            buffer = []


def reflection_fingerprint_from_samples(samples, path=None):
    frames = []
    amp_means = []
    amp_stds = []
    phase_stds = []
    motion = []
    rssi_values = []
    source_macs = collections.Counter()
    first_ts = None
    last_ts = None
    previous_amp = None
    parsed = 0
    for sample in samples:
        parsed += 1
        vector, amps = reflection_frame_vector(sample, previous_amp)
        if vector is None:
            continue
        previous_amp = amps
        frames.append(vector)
        amp_means.append(mean_value(amps))
        amp_stds.append(std_value(amps))
        phase_values = downsample(phase_residual_series(sample.get("phases") or []), REFLECTION_TONES)
        phase_stds.append(std_value(phase_values))
        rssi_values.append(float(sample.get("rssi") or 0.0))
        source = sample.get("sourceMac")
        if source:
            source_macs[source] += 1
        if first_ts is None:
            first_ts = sample.get("ts")
        last_ts = sample.get("ts")
        if len(frames) > 1:
            prev = frames[-2]
            motion.append(mean_value([abs(a - b) for a, b in zip(vector, prev)]))
    if len(frames) < 3:
        raise ValueError(f"not enough CSI frames in {path}")

    mean_vec = vector_mean(frames)
    std_vec = vector_std(frames, mean_vec)
    duration_s = max(0.0, ((last_ts or first_ts or 0) - (first_ts or last_ts or 0)) / 1000.0)
    feature = [
        *mean_vec,
        *[0.45 * value for value in std_vec],
        mean_value(amp_means) / 5.0,
        std_value(amp_means),
        mean_value(amp_stds),
        mean_value(phase_stds) / 4.0,
        mean_value(motion) * 4.0,
        percentile_value(motion, 0.9) * 4.0,
        (mean_value(rssi_values) + 80.0) / 40.0,
    ]
    return {
        "path": str(path),
        "feature": feature,
        "frames": len(frames),
        "parsedPackets": parsed,
        "durationS": round(duration_s, 2),
        "sourceMac": source_macs.most_common(1)[0][0] if source_macs else None,
        "metrics": {
            "ampMean": round(mean_value(amp_means), 5),
            "ampStdMean": round(mean_value(amp_stds), 5),
            "phaseStdMean": round(mean_value(phase_stds), 5),
            "motionMean": round(mean_value(motion), 5),
            "motionP90": round(percentile_value(motion, 0.9), 5),
            "rssiMean": round(mean_value(rssi_values), 2),
        },
    }


def cosine_similarity(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(y) * float(y) for y in b))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return dot / (na * nb)


def diagonal_distance(x, mean, std):
    if not x or not mean or len(x) != len(mean):
        return float("inf")
    total = 0.0
    for idx, value in enumerate(x):
        scale = max(0.08, float(std[idx]) if idx < len(std) else 0.0)
        z = (float(value) - float(mean[idx])) / scale
        total += z * z
    return math.sqrt(total / len(x))


def reflection_label_to_state(label):
    if label == "closed":
        return "closed"
    if label in REFLECTION_CANONICAL_OPEN:
        return "open"
    return label or "unknown"


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
        self.finisher = None
        self.last_sample_wall = 0.0
        self.last_auto_restart = 0.0
        self.auto_restarts = 0
        self.tx_loop = None
        self.engine = None
        self.ticker = None
        self.sensing_log = None
        self.events_path = None
        self.history = collections.deque(maxlen=HISTORY_ROWS)
        self.frame_history = collections.deque(maxlen=FRAME_HISTORY)
        self.frame_pending = []
        self.last_frame_obj = None
        self.last_frame_publish = 0.0
        self.state = {
            "running": False,
            "mode": CAPTURE_MODE_SINGLE_BOX,
            "sensing": None,
            "sensingMessage": None,
            "tx": None,
            "radio": None,
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
        mode=CAPTURE_MODE_SINGLE_BOX,
        tx_rate_pps=None,
    ):
        with self.lock:
            if self.state["running"]:
                return self._snapshot_locked()
            mode = LEGACY_MODE_ALIASES.get(mode, mode)
            mode = mode if mode in (CAPTURE_MODE_MOTION, CAPTURE_MODE_SINGLE_BOX) else CAPTURE_MODE_SINGLE_BOX
            self.tx_loop = None
            self.engine = None
            radio = None
            if mode == CAPTURE_MODE_SINGLE_BOX:
                # Tek kutu: kaynak MAC = Alpha'nin kendi AX210'u (bravo gerekmez).
                cfg = SENSING.config()
                if tx_rate_pps not in (None, ""):
                    try:
                        cfg = SENSING.save_config({**cfg, "txRatePps": float(tx_rate_pps)})
                    except (TypeError, ValueError):
                        pass
                radio = radio_diagnostics(LIVE_TX_IFACE)
                if DEV_TX_MAC:
                    self.tx_loop = DryTxLoop(rate_pps=cfg["txRatePps"], log=self._log)
                    channel = cfg["channel"] if cfg["channel"].lower() != "auto" else DEFAULT_CHANNEL
                elif cfg["txBackend"] in ("ax210_ba", "ax210_action"):
                    if cfg["txBackend"] == "ax210_action":
                        self.tx_loop = ActionProbeLoop(iface=LIVE_TX_IFACE, rate_pps=cfg["txRatePps"], log=self._log)
                    else:
                        self.tx_loop = PingProbeLoop(
                            iface=LIVE_TX_IFACE,
                            target=None if cfg["probeTarget"].lower() == "auto" else cfg["probeTarget"],
                            rate_pps=cfg["txRatePps"],
                            log=self._log,
                        )
                    requested = str(channel or "").strip().lower()
                    if cfg["channel"] and cfg["channel"].lower() != "auto":
                        channel = cfg["channel"]
                    elif requested and requested not in ("auto", DEFAULT_CHANNEL):
                        channel = requested
                    else:
                        channel = (radio or {}).get("chanspec20") or "48/20"
                elif cfg["txBackend"] == "inject":
                    self.tx_loop = InjectTxLoop(
                        phy_iface=LIVE_TX_IFACE,
                        channel=cfg["injectChannel"],
                        rate_mbps=cfg["injectRateMbps"],
                        rate_pps=cfg["txRatePps"],
                        payload_size=cfg["txPayloadBytes"],
                        log=self._log,
                    )
                    channel = f"{int(cfg['injectChannel'])}/20"
                else:
                    self.tx_loop = UdpTxLoop(
                        iface=LIVE_TX_IFACE,
                        rate_pps=cfg["txRatePps"],
                        payload_size=cfg["txPayloadBytes"],
                        log=self._log,
                    )
                    requested = str(channel or "").strip().lower()
                    if cfg["channel"] and cfg["channel"].lower() != "auto":
                        channel = cfg["channel"]
                    elif requested and requested != "auto" and requested != DEFAULT_CHANNEL:
                        channel = requested
                    else:
                        channel = (radio or {}).get("chanspec") or DEFAULT_CHANNEL
                source_mac = iface_mac(LIVE_TX_IFACE) or ""
                if not source_mac:
                    self.tx_loop = None
                    self.state["error"] = f"TX arayuzu {LIVE_TX_IFACE} bulunamadi (AX210 takili mi?)"
                    return self._snapshot_locked()
                self.engine = sensing.SensingEngine(
                    cfg,
                    calibration=SENSING.calibration(),
                    profiles=SENSING.profiles(),
                    meta={"channel": channel, "txMac": source_mac, "backend": cfg["txBackend"]},
                )
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
                "mode": mode,
            }
            self.sensing_log = None
            self.events_path = None
            self.history.clear()
            self.frame_history.clear()
            self.frame_pending = []
            self.last_frame_obj = None
            if self.engine:
                self.events_path = CAPTURE_DIR / f"csi-alpha-web-{started}.events.ndjson"
                try:
                    self.sensing_log = open(CAPTURE_DIR / f"csi-alpha-web-{started}.sensing.ndjson",
                                            "a", encoding="utf-8", buffering=1)
                except OSError:
                    self.sensing_log = None
            self.state.update({
                "running": True,
                "mode": mode,
                "sensing": self.engine.status() if self.engine else None,
                "sensingMessage": None,
                "radio": radio,
                "tx": {
                    "iface": LIVE_TX_IFACE,
                    "mac": iface_mac(LIVE_TX_IFACE),
                    "ratePps": self.tx_loop.rate_pps,
                    "backend": getattr(self.tx_loop, "backend", "ap_udp"),
                } if self.tx_loop else None,
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
        self._stop_tx()
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
        self.finisher = threading.Thread(target=self._finish_stop, daemon=True)
        self.finisher.start()
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
        self._stop_tx()
        log = self.sensing_log
        self.sensing_log = None
        if log:
            try:
                log.close()
            except OSError:
                pass
        with self.lock:
            self.state["running"] = False
            self.state["stoppedAt"] = now_ms()
            self.state["error"] = str(error) if error else None
        self.hub.publish("status", self.status())

    def _stop_tx(self):
        tx = self.tx_loop
        if tx:
            tx.stop()

    def _run(self):
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        DATASET_DIR.mkdir(parents=True, exist_ok=True)
        if self.tx_loop:
            try:
                self.tx_loop.start()
            except Exception as exc:
                self._set_stopped(f"TX baslatilamadi: {exc}")
                return
        if self.engine:
            self.ticker = threading.Thread(target=self._tick_loop, daemon=True)
            self.ticker.start()
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
        self.last_sample_wall = t
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
        engine = self.engine
        if engine is None:
            # eski Alpha-Bravo hareket modu: CNN/LSTM + alarm kapisi
            sample["ml"] = self._infer_model(sample, motion)
            self._maybe_record_alarm(sample, sample["ml"])
        else:
            sample["ml"] = None
        self._write_dataset_sample(sample, motion, packet_rate)
        sensing_info = None
        if engine is not None:
            sensing_info, events = engine.push(sample)
            self._handle_sensing(sensing_info, events, sample)
            frame = engine.last_frame()
            if frame is not None and frame is not self.last_frame_obj:
                self.last_frame_obj = frame
                item = (round(frame.t, 3), frame.mode, [round(v, 4) for v in frame.amp])
                self.frame_history.append(item)
                self.frame_pending.append(item)
            sample["sensing"] = compact_sensing(sensing_info or engine.status())
        iq = sample.pop("iq", None)

        with self.lock:
            self.state["frames"] += 1
            self.state["packetRate"] = packet_rate
            self.state["motionScore"] = motion
            if sensing_info is not None:
                self.state["sensing"] = sensing_info
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
        if self.frame_pending and t - self.last_frame_publish >= FRAME_PUBLISH_S:
            self.last_frame_publish = t
            batch, self.frame_pending = self.frame_pending, []
            self.hub.publish("frames", {"t": [b[0] for b in batch], "m": [b[1] for b in batch],
                                        "a": [b[2] for b in batch]})
        del iq

    # ------------------------------------------------------------ tek kutu algilama
    def _tick_loop(self):
        """Kare gelmese de motoru ilerletir (sinyal kaybi) ve paneli gunceller."""
        while not self.stop_event.wait(SENSING_TICK_S):
            engine = self.engine
            if engine is None:
                break
            try:
                info, events = engine.tick(time.time())
            except Exception as exc:
                self._log(f"sensing tick error: {exc}")
                continue
            self._handle_sensing(info, events, None)
            if info is not None:
                with self.lock:
                    self.state["sensing"] = info
                    if info.get("overall") == "no_signal":
                        self.state["packetRate"] = 0.0
            now = time.time()
            if (RX_STALL_RESTART_S > 0 and getattr(engine, "signal_seen_ok", False)
                    and self.last_sample_wall and now - self.last_sample_wall >= RX_STALL_RESTART_S
                    and now - self.last_auto_restart >= RX_RESTART_MIN_GAP_S):
                self.last_auto_restart = now
                threading.Thread(target=self._auto_restart, args=(now - self.last_sample_wall,),
                                 daemon=True).start()
                break

    def _auto_restart(self, silent_s):
        """CSI akisi durdu: yakalamayi ayni parametrelerle yeniden baslat (surucu yeniden yuklenir)."""
        params = dict(self.params)
        self.auto_restarts += 1
        self._log(f"RX takildi ({silent_s:.0f} sn kare yok) -> yakalama yeniden baslatiliyor (#{self.auto_restarts})")
        self._append_event({"kind": "info", "label": "rx_restart", "t": time.time(), "silentS": round(silent_s, 1),
                            "count": self.auto_restarts})
        self.stop()
        for th in (self.thread, self.finisher):
            if th and th.is_alive() and th is not threading.current_thread():
                th.join(timeout=20)
        time.sleep(1.0)
        try:
            self.start(channel=params.get("channel"), source_mac=params.get("sourceMac"),
                       distance_m=params.get("distanceM"), label=params.get("label", ""),
                       note=(params.get("note") or "").replace(" [otomatik yeniden baslatma]", "") + " [otomatik yeniden baslatma]",
                       mode=params.get("mode", CAPTURE_MODE_SINGLE_BOX))
            self.last_sample_wall = time.time()
            with self.lock:
                self.state["autoRestarts"] = self.auto_restarts
        except Exception as exc:
            self._log(f"otomatik yeniden baslatma basarisiz: {exc}")

    def _handle_sensing(self, info, events, sample):
        if info is not None:
            row = csi_timeseries.history_row(info)
            last = self.history[-1] if self.history else None
            if last is None or last["t"] != row["t"]:
                self.history.append(row)
                self.hub.publish("row", row)
        log = self.sensing_log
        if info is not None and log is not None:
            try:
                log.write(json.dumps(sensing_log_row(info), separators=(",", ":")) + "\n")
            except (OSError, ValueError):
                pass
        for event in events or []:
            self._handle_sensing_event(event, sample)

    def _handle_sensing_event(self, event, sample):
        kind = event.get("kind")
        record = {k: v for k, v in event.items() if k not in ("calibration", "profile")}
        record["ts"] = int(float(event.get("t") or time.time()) * 1000)
        self._append_event(record)
        if kind == "alarm":
            if event.get("suppressed"):
                return
            alarm = {
                "id": f"{record['ts']}-{event['label']}",
                "ts": record["ts"],
                "label": event["label"],
                "text": event.get("text"),
                "confidence": round(min(1.0, max(0.0, (float(event.get("score") or 1.0) - 1.0) / 2.0 + 0.5)), 4),
                "score": event.get("score"),
                "model": event.get("engine"),
                "scenario": event.get("scenario"),
                "detail": event.get("detail"),
                "sourceMac": (sample or {}).get("sourceMac") or self.params.get("sourceMac"),
                "rssi": (sample or {}).get("rssi"),
                "packetRate": round(float((sample or {}).get("packetRate") or 0.0), 2),
            }
            ALARMS.append(alarm)
            with self.lock:
                self.state["alarms"] = ALARMS.list()
            self.hub.publish("alarm", alarm)
            self._log(f"ALARM {event['label']} skor={event.get('score')}")
        elif kind == "calibration_done":
            SENSING.save_calibration(event["calibration"])
            cal = event["calibration"]
            self._set_message(f"Kalibrasyon tamam: {cal['frames']} kare, {len(cal['references'])} mod, "
                              f"{cal['rate']} kare/sn" + (" · " + " ".join(cal.get("warnings") or []) if cal.get("warnings") else ""))
        elif kind == "calibration_failed":
            self._set_message(f"Kalibrasyon başarısız: {event.get('reason')}")
        elif kind == "profile_done":
            SENSING.save_profile(event["label"], event["profile"])
            self._set_message(f"Profil kaydedildi: {event['label']} ({event['profile']['frames']} kare)")
        elif kind == "profile_failed":
            self._set_message(f"Profil kaydedilemedi: {event.get('label')} · {event.get('reason')}")
        elif kind in ("info", "state", "clear"):
            self._log(f"sensing {kind} {event.get('label')}")

    def _set_message(self, text):
        with self.lock:
            self.state["sensingMessage"] = {"text": text, "ts": now_ms()}
        self._log(text)

    def _append_event(self, record):
        path = self.events_path
        if not path:
            return
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except (OSError, ValueError):
            pass

    def sensing_command(self, command, data):
        engine = self.engine
        if command == "mark":
            label = clean_label(data.get("label")) or "olay"
            kind = str(data.get("kind") or "instant")
            if kind not in ("start", "end", "instant"):
                kind = "instant"
            record = {"type": "gt", "kind": kind, "label": label, "ts": now_ms(),
                      "note": str(data.get("note") or "")[:200]}
            if not self.events_path:
                return {"ok": False, "error": "kayit calismiyor"}
            self._append_event(record)
            return {"ok": True, "mark": record}
        if engine is None:
            if command == "arm":
                cfg = SENSING.save_config({**SENSING.config(), "armed": bool(data.get("armed", True))})
                return {"ok": True, "armed": cfg["armed"]}
            return {"ok": False, "error": "tek kutu algilama calismiyor (once Baslat)"}
        if command == "calibrate":
            ok = engine.start_calibration(data.get("durationS"), t=time.time())
            self._set_message("Kalibrasyon başladı: ortamı normal (boş/kapalı) ve hareketsiz bırakın.")
        elif command == "profile":
            ok = engine.start_profile(data.get("label"), data.get("durationS") or 30, t=time.time())
            if ok:
                self._set_message(f"Profil kaydı başladı: {clean_label(data.get('label'))}")
        elif command == "cancel":
            engine.cancel_session()
            ok = True
            self._set_message("Kalibrasyon/profil kaydı iptal edildi.")
        elif command == "accept_baseline":
            ok = engine.accept_baseline()
            self._set_message("Mevcut ortam yeni statik referans kabul edildi." if ok else "Referans güncellenemedi (kalibrasyon yok?).")
        elif command == "arm":
            armed = bool(data.get("armed", True))
            engine.set_armed(armed)
            SENSING.save_config({**SENSING.config(), "armed": armed})
            if armed and data.get("rebaseline", True):
                engine.accept_baseline()
            ok = True
            self._set_message("Alarm kuruldu (statik referans tazelendi)." if armed else "Alarm devre dışı.")
        elif command == "delete_profile":
            ok = engine.delete_profile(str(data.get("label") or ""))
        else:
            return {"ok": False, "error": f"bilinmeyen komut {command}"}
        return {"ok": bool(ok)}

    def history_payload(self, seconds=900, frame_seconds=60):
        rows = list(self.history)
        frames = list(self.frame_history)
        if rows:
            cutoff = rows[-1]["t"] - float(seconds)
            rows = [r for r in rows if r["t"] >= cutoff]
        if frames:
            fcut = frames[-1][0] - float(frame_seconds)
            frames = [f for f in frames if f[0] >= fcut]
        return {
            "rows": rows,
            "frames": {"t": [f[0] for f in frames], "m": [f[1] for f in frames], "a": [f[2] for f in frames]},
            "alarms": [a for a in ALARMS.list() if rows and a.get("ts", 0) / 1000.0 >= rows[0]["t"]],
            "running": self.state.get("running"),
        }

    def update_sensing_config(self, config):
        engine = self.engine
        if engine is not None:
            engine.update_config(config)


class ReflectionAnalyzer:
    def __init__(self, path=REFLECTION_PROFILE_PATH):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.profile = None
        self.error = None
        self._load()

    def _load(self):
        with self.lock:
            try:
                self.profile = json.loads(self.path.read_text(encoding="utf-8"))
                self.error = None
            except FileNotFoundError:
                self.profile = None
                self.error = "profile_missing"
            except Exception as exc:
                self.profile = None
                self.error = f"profile_load_failed: {exc}"

    def status(self):
        with self.lock:
            profile = self.profile or {}
            labels = profile.get("labels") or {}
            return {
                "ready": bool(labels),
                "path": str(self.path),
                "error": self.error,
                "createdAt": profile.get("createdAt"),
                "featureDim": profile.get("featureDim"),
                "labels": {
                    label: {
                        "sessionCount": len(item.get("sessions") or []),
                        "frameCount": item.get("frameCount", 0),
                        "liveWindowCount": item.get("liveWindowCount", 0),
                    }
                    for label, item in labels.items()
                },
                "liveReady": bool(labels) and all(item.get("liveMean") for item in labels.values()),
                "liveWindow": profile.get("liveWindow"),
            }

    def _discover_sessions(self):
        sessions = []
        for meta_path in sorted(CAPTURE_DIR.glob("reflection-*.session.json")):
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            label = clean_label(payload.get("label"))
            if label not in REFLECTION_PROFILE_LABELS:
                continue
            artifacts = payload.get("artifacts") or {}
            pcap = artifacts.get("latest_pcap") or artifacts.get("latestPcap")
            if not pcap:
                continue
            pcap_path = Path(pcap)
            if not pcap_path.is_file():
                continue
            sessions.append({
                "label": label,
                "meta": str(meta_path),
                "pcap": str(pcap_path),
                "createdAt": payload.get("created_or_updated_at"),
            })
        return sessions

    def build_profiles(self):
        sessions = self._discover_sessions()
        grouped = collections.defaultdict(list)
        live_windows = collections.defaultdict(list)
        skipped = []
        for session in sessions:
            try:
                fingerprint = reflection_fingerprint_from_pcap(session["pcap"])
            except Exception as exc:
                skipped.append({
                    "label": session["label"],
                    "pcap": session["pcap"],
                    "error": str(exc),
                })
                continue
            fingerprint["meta"] = session["meta"]
            fingerprint["createdAt"] = session.get("createdAt")
            grouped[session["label"]].append(fingerprint)
            try:
                live_windows[session["label"]].extend(
                    iter_window_fingerprints_from_pcap(session["pcap"], LIVE_REFLECTION_WINDOW)
                )
            except Exception:
                pass

        labels = {}
        for label, fingerprints in sorted(grouped.items()):
            vectors = [item["feature"] for item in fingerprints]
            mean_vec = vector_mean(vectors)
            std_vec = vector_std(vectors, mean_vec)
            windows = live_windows.get(label) or []
            live_mean = vector_mean(windows) if windows else []
            live_std = vector_std(windows, live_mean) if windows else []
            metric_names = sorted({
                key
                for item in fingerprints
                for key in (item.get("metrics") or {}).keys()
            })
            metrics = {
                name: round(mean_value([
                    float((item.get("metrics") or {}).get(name, 0.0))
                    for item in fingerprints
                ]), 5)
                for name in metric_names
            }
            labels[label] = {
                "mean": mean_vec,
                "std": std_vec,
                "liveMean": live_mean,
                "liveStd": live_std,
                "liveWindowCount": len(windows),
                "frameCount": sum(int(item.get("frames") or 0) for item in fingerprints),
                "metrics": metrics,
                "sessions": [
                    {
                        "pcap": item.get("path"),
                        "meta": item.get("meta"),
                        "frames": item.get("frames"),
                        "durationS": item.get("durationS"),
                        "sourceMac": item.get("sourceMac"),
                        "metrics": item.get("metrics"),
                    }
                    for item in fingerprints
                ],
            }

        if not labels:
            with self.lock:
                self.error = "no_valid_reflection_sessions"
                self.profile = None
            return {
                "ok": False,
                "error": self.error,
                "sessionsFound": len(sessions),
                "skipped": skipped,
                "profile": self.status(),
            }

        feature_dim = len(next(iter(labels.values()))["mean"])
        payload = {
            "version": 1,
            "model": "reflection_fingerprint_v1",
            "createdAt": now_ms(),
            "featureDim": feature_dim,
            "tones": REFLECTION_TONES,
            "frameStride": REFLECTION_FRAME_STRIDE,
            "maxPackets": REFLECTION_MAX_PACKETS,
            "liveWindow": LIVE_REFLECTION_WINDOW,
            "labels": labels,
            "math": {
                "channel": "H_k(t)=sum_l alpha_l(t) exp(-j2*pi*f_k*tau_l(t))+n_k(t)",
                "feature": "session mean/std of normalized log-amplitude shape and detrended phase residual shape",
                "distance": "diagonal z-distance to reference mean with cosine similarity as a secondary score",
                "decision": "closed/open selected by smaller calibrated distance; low margin yields uncertain",
            },
        }
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        with self.lock:
            self.profile = payload
            self.error = None
        return {
            "ok": True,
            "sessionsFound": len(sessions),
            "skipped": skipped,
            "profile": self.status(),
        }

    def analyze_pcap(self, pcap_path):
        self._load()
        with self.lock:
            profile = self.profile
        if not profile or not profile.get("labels"):
            return {
                "ready": False,
                "decision": "profile_missing",
                "error": self.error or "profile_missing",
                "profile": self.status(),
            }
        try:
            fp = reflection_fingerprint_from_pcap(pcap_path)
        except Exception as exc:
            return {
                "ready": False,
                "decision": "analysis_failed",
                "error": str(exc),
                "profile": self.status(),
            }
        x = fp["feature"]
        scored = self.score_feature(x, profile=profile, live=False)
        if not scored.get("ready"):
            scored["profile"] = self.status()
            return scored
        scored.update({
            "pcap": str(pcap_path),
            "fingerprint": {
                "frames": fp.get("frames"),
                "durationS": fp.get("durationS"),
                "sourceMac": fp.get("sourceMac"),
                "metrics": fp.get("metrics"),
            },
            "profile": self.status(),
        })
        return scored

    def score_feature(self, x, profile=None, live=False):
        """Fingerprint vektorunu referans profillere gore puanlar.

        live=True ise pencere-seviyesi referanslar (liveMean/liveStd) kullanilir;
        bunlar yoksa oturum-seviyesi referanslara (mean/std) geri dusulur.
        """
        if profile is None:
            with self.lock:
                profile = self.profile
        labels = (profile or {}).get("labels") or {}
        results = {}
        reference_kind = "session"
        for label, item in labels.items():
            mean_vec = item.get("mean") or []
            std_vec = item.get("std") or []
            if live and item.get("liveMean") and item.get("liveStd"):
                mean_vec = item["liveMean"]
                std_vec = item["liveStd"]
                reference_kind = "window"
            distance = diagonal_distance(x, mean_vec, std_vec)
            cosine = cosine_similarity(x, mean_vec)
            similarity = max(0.0, min(100.0, 100.0 / (1.0 + distance)))
            results[label] = {
                "state": reflection_label_to_state(label),
                "distance": round(distance, 5),
                "cosine": round(cosine, 5),
                "similarity": round(similarity, 2),
            }
        if not results:
            return {
                "ready": False,
                "decision": "profile_missing",
                "error": "profile_has_no_labels",
            }

        ordered = sorted(results.items(), key=lambda item: item[1]["distance"])
        best_label, best = ordered[0]
        second = ordered[1][1] if len(ordered) > 1 else None
        margin = 0.0
        if second:
            margin = (second["distance"] - best["distance"]) / max(1e-9, second["distance"] + best["distance"])
        decision = reflection_label_to_state(best_label)
        if second and margin < LIVE_REFLECTION_MARGIN:
            decision = "uncertain"

        closed_distance = results.get("closed", {}).get("distance")
        open_candidates = [
            item["distance"]
            for label, item in results.items()
            if label in REFLECTION_CANONICAL_OPEN
        ]
        open_distance = min(open_candidates) if open_candidates else None
        binary_decision = None
        binary_confidence = None
        reflection_score = None
        if closed_distance is not None and open_distance is not None:
            binary_margin = abs(open_distance - closed_distance) / max(1e-9, open_distance + closed_distance)
            binary_confidence = max(0.0, min(1.0, binary_margin * 2.5))
            binary_decision = "closed" if closed_distance < open_distance else "open"
            if binary_margin < LIVE_REFLECTION_MARGIN:
                binary_decision = "uncertain"
            closed_sim = results.get("closed", {}).get("similarity", 0.0)
            open_sim = max(
                [item["similarity"] for label, item in results.items() if label in REFLECTION_CANONICAL_OPEN]
                or [0.0]
            )
            reflection_score = round(closed_sim - open_sim, 2)

        return {
            "ready": True,
            "decision": binary_decision or decision,
            "closestLabel": best_label,
            "confidence": round(float(binary_confidence if binary_confidence is not None else max(0.0, min(1.0, margin * 2.5))), 4),
            "margin": round(float(margin), 5),
            "reflectionScore": reflection_score,
            "scores": results,
            "referenceKind": reference_kind,
        }


def default_gateway():
    try:
        out = subprocess.check_output(["ip", "route"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if parts[:1] == ["default"] and "via" in parts:
            return parts[parts.index("via") + 1]
    return None


def iface_mac(iface):
    if DEV_TX_MAC and iface == LIVE_TX_IFACE:
        return DEV_TX_MAC
    try:
        return Path(f"/sys/class/net/{iface}/address").read_text().strip().lower()
    except OSError:
        return None


def iface_ipv4(iface):
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "dev", iface], text=True, stderr=subprocess.DEVNULL
        )
    except Exception:
        return None
    fields = out.split()
    if len(fields) >= 4:
        return fields[3].split("/", 1)[0]
    return None


def _run_text(args, timeout=4):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except Exception:
        return ""


def radio_diagnostics(iface=LIVE_TX_IFACE):
    """AX210 baglantisini okur, Nexmon icin chanspec onerir ve bilinen tuzaklari raporlar."""
    info = {"iface": iface, "mac": iface_mac(iface), "ipv4": iface_ipv4(iface), "gateway": default_gateway(),
            "connected": False, "warnings": []}
    iw = shutil.which("iw") or "/usr/sbin/iw"
    link = _run_text([iw, "dev", iface, "link"])
    devinfo = _run_text([iw, "dev", iface, "info"])
    for line in link.splitlines():
        line = line.strip()
        if line.startswith("Connected to"):
            info["connected"] = True
            info["bssid"] = line.split()[2] if len(line.split()) > 2 else None
        elif line.startswith("SSID:"):
            info["ssid"] = line.split(":", 1)[1].strip()
        elif line.startswith("freq:"):
            try:
                info["freq"] = int(float(line.split(":", 1)[1].strip().split()[0]))
            except ValueError:
                pass
        elif line.startswith("tx bitrate:"):
            info["txBitrate"] = line.split(":", 1)[1].strip()
        elif line.startswith("rx bitrate:"):
            info["rxBitrate"] = line.split(":", 1)[1].strip()
    for line in devinfo.splitlines():
        line = line.strip()
        if line.startswith("channel "):
            # channel 48 (5240 MHz), width: 80 MHz, center1: 5210 MHz
            parts = line.replace(",", " ").split()
            try:
                info["channel"] = int(parts[1])
            except (IndexError, ValueError):
                pass
            if "width:" in parts:
                try:
                    info["width"] = int(parts[parts.index("width:") + 1])
                except (IndexError, ValueError):
                    pass
    ch = info.get("channel")
    width = info.get("width") or 20
    if ch:
        info["chanspec20"] = f"{ch}/20"
    if ch:
        if ch <= 14:
            info["chanspec"] = f"{ch}/20"
            if width > 20:
                info["warnings"].append("2.4 GHz'de 40 MHz: Nexmon 20 MHz dinler, CSI 64 ton olur.")
        else:
            bw = 80 if width >= 80 else (40 if width >= 40 else 20)
            info["chanspec"] = f"{ch}/{bw}"
            if width >= 160:
                info["warnings"].append("Modem 160 MHz: bcm43455c0 160 MHz'i çözemez; modemi 80 MHz'e alın.")
    if not info["connected"]:
        info["warnings"].append(f"{iface} bir modeme bağlı değil: ap_udp TX çalışmaz (inject yöntemini deneyin).")
    rate = str(info.get("txBitrate") or "")
    info["he"] = "HE-MCS" in rate or " HE" in rate or "EHT" in rate
    if info["he"]:
        info["notes"] = ["AX210 bağlantısı 802.11ax (HE): veri çerçeveleri Broadcom'da okunamaz. "
                         "ax210_ba yöntemi (önerilen) bundan etkilenmez; ap_udp kullanılacaksa "
                         "iwlwifi disable_11ax=1 ve 80 MHz gerekir."]
    if width >= 160:
        info["warnings"] = [w for w in info["warnings"] if "160 MHz" not in w]
        info.setdefault("notes", []).append("Modem 160 MHz: ax210_ba ana 20 MHz kanaldan okur, sorun değil; "
                                            "ap_udp için modem 80 MHz olmalı.")
    return info


def compact_sensing(info):
    """Her ornekle panele gonderilen kucuk ozet (grafik icin)."""
    if not info:
        return None
    det = info.get("detectors") or {}
    return {
        "t": info.get("t"),
        "overall": info.get("overall"),
        "motion": (det.get("motion") or {}).get("score"),
        "static": (det.get("static") or {}).get("score"),
        "micro": (det.get("micro") or {}).get("score"),
        "active": info.get("activeAlarms") or [],
    }


def sensing_log_row(info):
    det = info.get("detectors") or {}
    m = det.get("motion") or {}
    st = det.get("static") or {}
    mi = det.get("micro") or {}
    sig = info.get("signal") or {}
    return {
        "t": info.get("t"),
        "overall": info.get("overall"),
        "motion": m.get("value"), "novelty": m.get("novelty"), "gain": m.get("gain"), "residual": m.get("residual"), "motionScore": m.get("score"),
        "motionActive": m.get("active"),
        "static": st.get("value"), "staticScore": st.get("score"), "staticActive": st.get("active"),
        "staticEvaluated": st.get("evaluated"),
        "micro": mi.get("value"), "microScore": mi.get("score"), "microActive": mi.get("active"),
        "state": (det.get("state") or {}).get("label"), "stateStable": (det.get("state") or {}).get("stable"),
        "rate": sig.get("rate"), "rssi": sig.get("rssi"), "modes": sig.get("modesInWindow"),
        "active": info.get("activeAlarms"),
    }


class SensingStore:
    """Tek kutu algilama ayarlari, kalibrasyon ve durum profilleri (diskte kalici)."""

    def __init__(self):
        self.lock = threading.RLock()
        self._config = None

    def config(self):
        with self.lock:
            if self._config is None:
                raw = sensing.load_json(SENSING_CONFIG_PATH, {}) or {}
                base = sensing.default_config(raw.get("scenario") or "room")
                self._config = sensing.normalize_config(raw, base=base)[0]
            return dict(self._config)

    def save_config(self, config, scenario=None):
        with self.lock:
            current = self.config()
            if scenario:
                merged = sensing.apply_scenario({**current, **(config or {})}, scenario)
            else:
                merged = sensing.normalize_config(config or {}, base=current)[0]
            self._config = merged
            self._write(SENSING_CONFIG_PATH, merged)
            return dict(merged)

    def reset_config(self, scenario=None):
        with self.lock:
            scenario = scenario or self.config().get("scenario") or "room"
            self._config = sensing.default_config(scenario)
            self._write(SENSING_CONFIG_PATH, self._config)
            return dict(self._config)

    def calibration(self):
        return sensing.load_json(SENSING_CALIBRATION_PATH, None)

    def save_calibration(self, calibration):
        self._write(SENSING_CALIBRATION_PATH, calibration)

    def delete_calibration(self):
        try:
            SENSING_CALIBRATION_PATH.unlink()
            return True
        except OSError:
            return False

    def profiles(self):
        return sensing.load_json(SENSING_PROFILES_PATH, {}) or {}

    def save_profile(self, label, profile):
        with self.lock:
            profiles = self.profiles()
            profiles[label] = profile
            self._write(SENSING_PROFILES_PATH, profiles)

    def delete_profile(self, label):
        with self.lock:
            profiles = self.profiles()
            removed = profiles.pop(label, None) is not None
            self._write(SENSING_PROFILES_PATH, profiles)
            return removed

    def summary(self):
        cal = self.calibration()
        profiles = self.profiles()
        return {
            "calibration": {
                "createdAt": cal.get("createdAt"),
                "durationS": cal.get("durationS"),
                "frames": cal.get("frames"),
                "rate": cal.get("rate"),
                "acceptRatio": cal.get("acceptRatio"),
                "modes": len(cal.get("references") or []),
                "thresholds": cal.get("thresholds"),
                "stats": cal.get("stats"),
                "warnings": cal.get("warnings") or [],
                "meta": cal.get("meta"),
                "config": cal.get("config"),
                "engine": cal.get("version"),
                "residual": {k: (cal.get("residual") or {}).get(k) for k in ("state", "residualDb", "frames")}
                            if cal.get("residual") else None,
            } if cal else None,
            "profiles": {
                label: {
                    "createdAt": p.get("createdAt"),
                    "durationS": p.get("durationS"),
                    "frames": p.get("frames"),
                    "modes": len(p.get("references") or []),
                    "distP99": p.get("distP99"),
                    "warnings": p.get("warnings") or [],
                }
                for label, p in profiles.items()
            },
        }

    @staticmethod
    def _write(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, path)


class ActionProbeLoop:
    """ax210_action: AX210 (mgmt0) bagliyken AP'ye adresli Public Action yonetim cercevesi gonderir.

    Yonetim cerceveleri sabit en dusuk temel hizda (5 GHz: 6 Mbps OFDM, 20 MHz, legacy) ve hiz
    uyarlamasi olmadan gider. Cerceveyi AX210 bizim istegimizle yayar; AP yalnizca ACK doner (ACK'te
    AX210 MAC'i yok, Nexmon filtresi onu almaz). Olculen yol: AX210 -> ortam -> Broadcom.
    ax210_ba'dan farki: modemin cevap paketlerine ve AX210'un BlockAck guc/durum secimine bagli degil.
    Kanal degismez; SSH/web etkilenmez.
    """

    backend = "ax210_action"
    BSSID_REFRESH_S = 5.0

    def __init__(self, iface=LIVE_TX_IFACE, rate_pps=50.0, payload_size=96, log=None):
        self.iface = iface
        self.rate_pps = max(1.0, float(rate_pps))
        self.payload_size = max(8, min(1200, int(payload_size)))
        self.log = log or (lambda line: None)
        self.stop_event = threading.Event()
        self.thread = None
        self.sent = 0
        self.errors = 0
        self.bssid = None
        self.mac = iface_mac(iface)
        self.tx = None

    def _current_bssid(self):
        iw = shutil.which("iw") or "/usr/sbin/iw"
        try:
            out = subprocess.run([iw, "dev", self.iface, "link"], capture_output=True, text=True,
                                 timeout=3, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        if "Connected to " not in out:
            return None
        return out.split("Connected to ", 1)[1].split()[0].lower()

    def start(self):
        if not self.mac:
            raise RuntimeError(f"{self.iface} bulunamadi")
        self.bssid = self._current_bssid()
        if not self.bssid:
            raise RuntimeError(f"{self.iface} bir aga bagli degil (ax210_action bagli AP gerektirir)")
        self.tx = csi_nl80211.Nl80211FrameTx(self.iface)
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        if self.tx:
            try:
                self.tx.close()
            except OSError:
                pass
            self.tx = None

    def _run(self):
        self.log(f"action_probe iface={self.iface} mac={self.mac} ap={self.bssid} pps={self.rate_pps}")
        interval = 1.0 / self.rate_pps
        next_at = time.monotonic()
        next_refresh = next_at + self.BSSID_REFRESH_S
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now >= next_refresh:
                    next_refresh = now + self.BSSID_REFRESH_S
                    bssid = self._current_bssid()
                    if bssid != self.bssid:
                        self.log(f"action_probe: AP degisti {self.bssid} -> {bssid}")
                        self.bssid = bssid
                if not self.bssid:
                    self.stop_event.wait(1.0)
                    next_at = time.monotonic()
                    continue
                self.sent += 1
                frame = csi_nl80211.public_action_frame(self.mac, self.sent, dst_mac=self.bssid,
                                                        payload_len=self.payload_size)
                try:
                    err = self.tx.send(frame)
                except OSError as exc:
                    err = exc.errno or 1
                if err:
                    self.errors += 1
                    if self.errors <= 5 or self.errors % 500 == 0:
                        self.log(f"action_probe gonderim hatasi ({self.errors}): {os.strerror(err)}")
                next_at += interval
                delay = next_at - time.monotonic()
                if delay > 0:
                    self.stop_event.wait(delay)
                else:
                    next_at = time.monotonic()
        except Exception as exc:
            self.log(f"action_probe hata: {exc}")
        finally:
            self.log(f"action_probe stopped sent={self.sent} errors={self.errors}")


class PingProbeLoop:
    """ax210_ba: AX210 (mgmt0) uzerinden ag gecidine sabit hizda ICMP echo gonderir.

    Modemin her cevabina AX210 legacy (non-HT) bir BlockAck yayar; Broadcom/Nexmon bu cercevenin
    CSI'ini AX210 MAC filtresiyle toplar. Olculen kanal AX210 -> ortam -> Broadcom (tek kutu).
    AX210 managed modda kalir; SSH/web etkilenmez.
    """

    backend = "ax210_ba"

    def __init__(self, iface=LIVE_TX_IFACE, target=None, rate_pps=50.0, payload_size=32, log=None):
        self.iface = iface
        self.target = target
        self.rate_pps = max(1.0, float(rate_pps))
        self.payload_size = max(8, min(1400, int(payload_size)))
        self.log = log or (lambda line: None)
        self.stop_event = threading.Event()
        self.thread = None
        self.sent = 0
        self.replies = 0
        self.ident = os.getpid() & 0xFFFF

    def start(self):
        self.target = self.target or default_gateway()
        if not self.target:
            raise RuntimeError("sondalama hedefi yok (varsayilan ag gecidi bulunamadi; mgmt0 bagli mi?)")
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

    @staticmethod
    def _checksum(data):
        if len(data) % 2:
            data += b"\0"
        total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
        total = (total >> 16) + (total & 0xFFFF)
        total += total >> 16
        return (~total) & 0xFFFF

    def _packet(self, seq):
        body = bytes(self.payload_size)
        header = struct.pack("!BBHHH", 8, 0, 0, self.ident, seq & 0xFFFF)
        csum = self._checksum(header + body)
        return struct.pack("!BBHHH", 8, 0, csum, self.ident, seq & 0xFFFF) + body

    def _drain(self, sock):
        while True:
            try:
                data = sock.recv(2048)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            ihl = (data[0] & 0x0F) * 4 if data else 0
            if len(data) >= ihl + 8 and data[ihl] == 0 and struct.unpack("!H", data[ihl + 4:ihl + 6])[0] == self.ident:
                self.replies += 1

    def _run(self):
        import socket
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
            try:
                sock.setsockopt(socket.SOL_SOCKET, 25, self.iface.encode() + b"\0")  # SO_BINDTODEVICE
            except OSError:
                pass
            sock.setblocking(False)
            self.log(f"probe_loop iface={self.iface} target={self.target} rate_pps={self.rate_pps}")
            interval = 1.0 / self.rate_pps
            next_at = time.monotonic()
            next_report = time.monotonic() + 10.0
            last_sent = last_replies = 0
            while not self.stop_event.is_set():
                self.sent += 1
                try:
                    sock.sendto(self._packet(self.sent), (self.target, 0))
                except OSError as exc:
                    self.log(f"probe_loop send error: {exc}")
                    self.stop_event.wait(1.0)
                self._drain(sock)
                now = time.monotonic()
                if now >= next_report:
                    sent = self.sent - last_sent
                    rep = self.replies - last_replies
                    if sent and rep < 0.5 * sent:
                        self.log(f"probe_loop uyari: {rep}/{sent} cevap (ag gecidi ICMP sinirliyor olabilir)")
                    last_sent, last_replies = self.sent, self.replies
                    next_report = now + 10.0
                next_at += interval
                delay = next_at - time.monotonic()
                if delay > 0:
                    self.stop_event.wait(delay)
                else:
                    next_at = time.monotonic()
        except Exception as exc:
            self.log(f"probe_loop hata: {exc}")
        finally:
            if sock:
                sock.close()
            self.log(f"probe_loop stopped sent={self.sent} replies={self.replies}")


class DryTxLoop:
    """Gelistirme/test icin: hicbir sey gondermez (CSI_DEV_TX_MAC)."""

    backend = "dev"

    def __init__(self, rate_pps=LIVE_TX_RATE_PPS, log=None):
        self.rate_pps = float(rate_pps)
        self.log = log or (lambda line: None)

    def start(self):
        self.log("dry_tx: gelistirme modu, paket gonderilmiyor")

    def stop(self):
        pass


class InjectTxLoop:
    """(Deneysel) AX210'u monitor modda kullanip radiotap ile 802.11 veri cercevesi enjekte eder.

    Modem gerektirmez (arac kasasi gibi ortamlar). addr2 = AX210 MAC oldugu icin Nexmon'un
    kaynak-MAC filtresi ayni kalir. Sabit legacy hiz -> tek, kararli PHY modu.
    Not: yonetim baglantisi (SSH/web) bu durumda Ethernet uzerinden olmalidir; mgmt0 bir modeme
    bagliysa enjeksiyon onun kanalini izler.
    """

    def __init__(self, phy_iface=LIVE_TX_IFACE, mon_iface=INJECT_MON_IFACE, channel=36, rate_mbps=6.0,
                 rate_pps=LIVE_TX_RATE_PPS, payload_size=LIVE_TX_PAYLOAD_SIZE, log=None):
        self.phy_iface = phy_iface
        self.mon_iface = mon_iface
        self.channel = int(channel)
        self.rate_500k = max(2, min(108, int(round(float(rate_mbps) * 2))))
        self.rate_pps = max(1.0, float(rate_pps))
        self.payload_size = max(32, min(1400, int(payload_size)))
        self.log = log or (lambda line: None)
        self.stop_event = threading.Event()
        self.thread = None
        self.sent = 0
        self.mac = iface_mac(phy_iface)
        self.created = False
        self.backend = "inject"

    def _cmd(self, *args):
        result = subprocess.run(list(args), capture_output=True, text=True, timeout=8, check=False)
        if result.returncode != 0:
            self.log(f"inject: {' '.join(args)} -> {result.stderr.strip() or result.returncode}")
        return result.returncode == 0

    def start(self):
        if not self.mac:
            raise RuntimeError(f"{self.phy_iface} bulunamadi")
        try:
            phy = Path(f"/sys/class/net/{self.phy_iface}/phy80211/name").read_text().strip()
        except OSError as exc:
            raise RuntimeError(f"{self.phy_iface} phy bulunamadi: {exc}")
        if not Path(f"/sys/class/net/{self.mon_iface}").exists():
            if not self._cmd("iw", "phy", phy, "interface", "add", self.mon_iface, "type", "monitor"):
                raise RuntimeError("monitor arayuzu olusturulamadi (iwlwifi monitor/injection destegi?)")
            self.created = True
        self._cmd("ip", "link", "set", self.mon_iface, "up")
        if not self._cmd("iw", "dev", self.mon_iface, "set", "channel", str(self.channel)):
            self.log("inject: kanal ayarlanamadi; mgmt0 bagliysa onun kanali kullanilir. "
                     "Nexmon kanali ile ayni oldugundan emin olun.")
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        if self.created:
            self._cmd("iw", "dev", self.mon_iface, "del")
            self.created = False

    def _frame(self, seq, body):
        # radiotap: version, pad, len=12, present = RATE(bit2) | TX_FLAGS(bit15); rate u8, pad, tx_flags u16 (NOACK)
        radiotap = struct.pack("<BBHI", 0, 0, 12, (1 << 2) | (1 << 15)) + struct.pack("<BxH", self.rate_500k, 0x0008)
        mac = bytes(int(x, 16) for x in self.mac.split(":"))
        header = (b"\x08\x00" + b"\x00\x00" + b"\xff" * 6 + mac + mac
                  + struct.pack("<H", (seq & 0x0FFF) << 4))
        llc = b"\xaa\xaa\x03\x00\x00\x00\x88\xb5"  # yerel deneysel ethertype
        return radiotap + header + llc + body

    def _run(self):
        import socket
        sock = None
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
            sock.bind((self.mon_iface, 0))
            self.log(f"inject_loop iface={self.mon_iface} mac={self.mac} ch={self.channel} "
                     f"rate={self.rate_500k / 2} Mbps pps={self.rate_pps}")
            body = bytearray(os.urandom(self.payload_size))
            interval = 1.0 / self.rate_pps
            next_at = time.monotonic()
            while not self.stop_event.is_set():
                self.sent += 1
                body[0:8] = self.sent.to_bytes(8, "big")
                try:
                    sock.send(self._frame(self.sent, bytes(body)))
                except OSError as exc:
                    self.log(f"inject send error: {exc}")
                    self.stop_event.wait(1.0)
                next_at += interval
                delay = next_at - time.monotonic()
                if delay > 0:
                    self.stop_event.wait(delay)
                else:
                    next_at = time.monotonic()
        except Exception as exc:
            self.log(f"inject_loop hata: {exc}")
        finally:
            if sock:
                sock.close()
            self.log(f"inject_loop stopped sent={self.sent}")


class UdpTxLoop:
    """AX210 (mgmt0) uzerinden sabit hizda UDP gonderir; Nexmon bu paketlerin CSI'ini toplar."""

    backend = "ap_udp"

    def __init__(self, iface=LIVE_TX_IFACE, target=None, port=LIVE_TX_PORT,
                 rate_pps=LIVE_TX_RATE_PPS, payload_size=LIVE_TX_PAYLOAD_SIZE, log=None):
        self.iface = iface
        self.target = target
        self.port = int(port)
        self.rate_pps = max(1.0, float(rate_pps))
        self.payload_size = max(32, int(payload_size))
        self.log = log or (lambda line: None)
        self.stop_event = threading.Event()
        self.thread = None
        self.sent = 0

    def start(self):
        self.target = self.target or default_gateway()
        if not self.target:
            raise RuntimeError("TX hedefi bulunamadi (default gateway yok); mgmt0 bagli mi?")
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _run(self):
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            try:
                sock.setsockopt(socket.SOL_SOCKET, 25, self.iface.encode() + b"\0")  # SO_BINDTODEVICE
            except OSError:
                pass
            source_ip = iface_ipv4(self.iface)
            if source_ip:
                sock.bind((source_ip, 0))
            self.log(f"tx_loop iface={self.iface} source_ip={source_ip or 'default'} "
                     f"target={self.target}:{self.port} rate_pps={self.rate_pps}")
            payload = bytearray(os.urandom(self.payload_size))
            interval = 1.0 / self.rate_pps
            next_at = time.monotonic()
            while not self.stop_event.is_set():
                self.sent += 1
                payload[0:8] = self.sent.to_bytes(8, "big")
                try:
                    sock.sendto(payload, (self.target, self.port))
                except OSError as exc:
                    self.log(f"tx_loop send error: {exc}")
                    self.stop_event.wait(1.0)
                next_at += interval
                delay = next_at - time.monotonic()
                if delay > 0:
                    self.stop_event.wait(delay)
                else:
                    next_at = time.monotonic()
        finally:
            sock.close()
            self.log(f"tx_loop stopped sent={self.sent}")


class ReflectionManager:
    def __init__(self, hub):
        self.hub = hub
        self.analyzer = ReflectionAnalyzer()
        self.lock = threading.RLock()
        self.process = None
        self.thread = None
        self.logs = collections.deque(maxlen=MAX_LOG_LINES)
        self.state = {
            "running": False,
            "label": "",
            "durationSecs": 0,
            "channel": DEFAULT_CHANNEL,
            "sourceMac": "",
            "distanceM": 0.0,
            "txRatePps": 20.0,
            "warmupSecs": 14,
            "startedAt": None,
            "expectedStopAt": None,
            "stoppedAt": None,
            "exitCode": None,
            "error": None,
            "sessionMeta": None,
            "latestPcap": None,
            "analysis": None,
            "profile": self.analyzer.status(),
            "command": [],
        }

    def status(self):
        with self.lock:
            self.state["profile"] = self.analyzer.status()
            payload = dict(self.state)
            payload["logs"] = list(self.logs)[-20:]
            return payload

    def start(
        self,
        duration_secs=60,
        label="closed",
        channel=DEFAULT_CHANNEL,
        source_mac="",
        distance_m=0.0,
        tx_rate_pps=20.0,
        warmup_secs=14,
    ):
        try:
            duration_secs = int(duration_secs)
            tx_rate_pps = float(tx_rate_pps)
            warmup_secs = int(warmup_secs)
        except (TypeError, ValueError):
            return self._error_status("invalid numeric parameter")
        duration_secs = max(5, min(duration_secs, 7200))
        tx_rate_pps = max(1.0, min(tx_rate_pps, 200.0))
        warmup_secs = max(5, min(warmup_secs, 60))
        label_clean = clean_label(label) or "reflection_test"

        with self.lock:
            if self.state["running"]:
                return self.status()
            if not TIMED_REFLECTION_SCRIPT.exists():
                return self._error_status(f"missing script: {TIMED_REFLECTION_SCRIPT}")
            self.logs.clear()
            started_at = now_ms()
            expected_stop_at = started_at + (duration_secs + warmup_secs + 10) * 1000
            command = [
                str(TIMED_REFLECTION_SCRIPT),
                str(duration_secs),
                label_clean,
                str(channel or DEFAULT_CHANNEL),
                str(source_mac or ""),
                str(distance_m or 0),
            ]
            self.state.update({
                "running": True,
                "label": label_clean,
                "durationSecs": duration_secs,
                "channel": str(channel or DEFAULT_CHANNEL),
                "sourceMac": str(source_mac or ""),
                "distanceM": float(distance_m or 0),
                "txRatePps": tx_rate_pps,
                "warmupSecs": warmup_secs,
                "startedAt": started_at,
                "expectedStopAt": expected_stop_at,
                "stoppedAt": None,
                "exitCode": None,
                "error": None,
                "sessionMeta": None,
                "latestPcap": None,
                "analysis": None,
                "profile": self.analyzer.status(),
                "command": command,
            })
            self.thread = threading.Thread(
                target=self._run,
                args=(command, tx_rate_pps, warmup_secs),
                daemon=True,
            )
            payload = self.status()
        self.thread.start()
        self.hub.publish("status", status_payload())
        return payload

    def stop(self):
        proc = self.process
        if proc and proc.poll() is None:
            self._log("panel stop requested")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception:
                proc.terminate()
        return self.status()

    def build_profiles(self):
        with self.lock:
            if self.state["running"]:
                return {
                    "ok": False,
                    "error": "measurement_running",
                    "reflection": self.status(),
                }
        result = self.analyzer.build_profiles()
        with self.lock:
            self.state["profile"] = self.analyzer.status()
        self.hub.publish("status", status_payload())
        result["reflection"] = self.status()
        return result

    def _error_status(self, message):
        with self.lock:
            self.state["error"] = str(message)
            return self.status()

    def _run(self, command, tx_rate_pps, warmup_secs):
        env = os.environ.copy()
        env["TX_RATE_PPS"] = str(tx_rate_pps)
        env["RX_WARMUP_SECS"] = str(warmup_secs)
        env.setdefault("PYTHONUNBUFFERED", "1")
        rc = None
        error = None
        try:
            CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid,
            )
            for line in self.process.stdout:
                self._log(line)
            rc = self.process.wait()
        except Exception as exc:
            error = str(exc)
            proc = self.process
            if proc and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
                    proc.terminate()
        finally:
            self._finish(rc, error)

    def _finish(self, rc, error):
        artifacts = self._find_latest_artifacts()
        analysis = None
        if artifacts.get("latestPcap"):
            analysis = self.analyzer.analyze_pcap(artifacts["latestPcap"])
        with self.lock:
            self.state.update({
                "running": False,
                "stoppedAt": now_ms(),
                "exitCode": rc,
                "error": error,
                "sessionMeta": artifacts.get("sessionMeta"),
                "latestPcap": artifacts.get("latestPcap"),
                "analysis": analysis,
                "profile": self.analyzer.status(),
            })
            self.process = None
        self.hub.publish("status", status_payload())

    def _find_latest_artifacts(self):
        session_meta = None
        latest_pcap = None
        run_label = self.state.get("label") or ""
        try:
            metas = sorted(
                CAPTURE_DIR.glob(f"reflection-{run_label}-*.session.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if metas:
                session_meta = str(metas[0])
                try:
                    payload = json.loads(metas[0].read_text(encoding="utf-8"))
                    latest_pcap = payload.get("artifacts", {}).get("latest_pcap")
                except Exception:
                    latest_pcap = None
        except Exception:
            session_meta = None
        if not latest_pcap:
            try:
                pcaps = sorted(
                    CAPTURE_DIR.glob("*.pcap"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                if pcaps:
                    latest_pcap = str(pcaps[0])
            except Exception:
                latest_pcap = None
        return {
            "sessionMeta": session_meta,
            "latestPcap": latest_pcap,
        }

    def _log(self, line):
        text = str(line or "").rstrip()
        if not text:
            return
        with self.lock:
            self.logs.append(text)
        self.hub.publish("status", status_payload())


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
SENSING = SensingStore()
CAPTURE = CaptureManager(HUB)
NETWORK = csi_network.NetworkManager(BASE_DIR, log=lambda line: CAPTURE._log(line))
REFLECTION = ReflectionManager(HUB)


def status_payload():
    payload = CAPTURE.status()
    payload["reflection"] = REFLECTION.status()
    return payload


def list_recordings():
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(CAPTURE_DIR.glob("*.pcap"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        stem = path.with_suffix("")
        events = Path(str(stem) + ".events.ndjson")
        sens = Path(str(stem) + ".sensing.ndjson")
        marks = 0
        if events.exists():
            try:
                marks = sum(1 for line in events.open("r", encoding="utf-8") if '"type":"gt"' in line)
            except OSError:
                marks = 0
        items.append({
            "name": path.name,
            "size": stat.st_size,
            "modifiedAt": int(stat.st_mtime * 1000),
            "hasSensingLog": sens.exists(),
            "hasEvents": events.exists(),
            "marks": marks,
            "active": CAPTURE.state.get("running") and str(CAPTURE.capture_path) == str(path),
        })
    return items


def analyze_recording(data):
    name = Path(str(data.get("name") or "")).name
    path = CAPTURE_DIR / name
    if not name.endswith(".pcap") or not path.is_file():
        raise ValueError("kayit bulunamadi")
    config = SENSING.config()
    config.update({k: v for k, v in (data.get("config") or {}).items() if k in config})
    cal_segment = None
    calibration = None
    if data.get("calibrateFrom") is not None and data.get("calibrateTo") is not None:
        cal_segment = (float(data["calibrateFrom"]), float(data["calibrateTo"]))
    else:
        calibration = SENSING.calibration()
    events_path = Path(str(path.with_suffix("")) + ".events.ndjson")
    started = time.time()
    result = csi_timeseries.analyze_capture(
        path, config, calibration=calibration, profiles=SENSING.profiles(), cal_segment=cal_segment,
        events_path=events_path, max_seconds=float(data.get("maxSeconds") or 3600))
    result["elapsedS"] = round(time.time() - started, 2)
    return result


def sensing_payload(include_radio=False):
    engine = CAPTURE.engine
    payload = {
        "config": SENSING.config(),
        "schema": sensing.CONFIG_SCHEMA,
        "scenarios": {key: {"label": v["label"], "description": v["description"], "overrides": v["overrides"]}
                      for key, v in sensing.SCENARIOS.items()},
        "engine": sensing.ENGINE_VERSION,
        "running": engine is not None,
        "status": engine.status() if engine else CAPTURE.state.get("sensing"),
        **SENSING.summary(),
    }
    if engine is not None:
        payload["profiles"] = {**payload["profiles"], **engine.profiles_summary()}
    if include_radio:
        payload["radio"] = radio_diagnostics(LIVE_TX_IFACE)
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "CSIWeb/0.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
        elif parsed.path in ("/app.js", "/charts.js"):
            self._send_file(WEB_DIR / parsed.path.lstrip("/"), "application/javascript; charset=utf-8")
        elif parsed.path == "/style.css":
            self._send_file(WEB_DIR / "style.css", "text/css; charset=utf-8")
        elif parsed.path == "/api/status":
            self._send_json(status_payload())
        elif parsed.path == "/api/reflection/status":
            self._send_json(REFLECTION.status())
        elif parsed.path == "/api/reflection/profile":
            self._send_json(REFLECTION.analyzer.status())
        elif parsed.path == "/api/files":
            self._send_json(list_record_files())
        elif parsed.path == "/api/alarms":
            self._send_json({"alarms": ALARMS.list()})
        elif parsed.path == "/api/sensing":
            self._send_json(sensing_payload(include_radio="radio=1" in (parsed.query or "")))
        elif parsed.path == "/api/sensing/radio":
            self._send_json(radio_diagnostics(LIVE_TX_IFACE))
        elif parsed.path == "/api/history":
            params = parse_qs(parsed.query)
            seconds = float((params.get("seconds") or ["900"])[0])
            self._send_json(CAPTURE.history_payload(seconds))
        elif parsed.path == "/api/recordings":
            self._send_json({"recordings": list_recordings()})
        elif parsed.path == "/api/network":
            self._send_json(NETWORK.status())
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
        if not NETWORK.check_password(self.headers.get("X-CSI-Key", "")):
            self._send_json({"ok": False, "error": "panel parolasi gerekli", "auth": True}, HTTPStatus.UNAUTHORIZED)
            return
        if parsed.path == "/api/start":
            CAPTURE.start(
                data.get("channel", DEFAULT_CHANNEL),
                data.get("sourceMac", DEFAULT_SOURCE_MAC),
                data.get("distanceM", DEFAULT_DISTANCE_M),
                data.get("label", ""),
                data.get("note", ""),
                data.get("mode", CAPTURE_MODE_SINGLE_BOX),
                data.get("txRatePps"),
            )
            self._send_json(status_payload())
        elif parsed.path == "/api/sensing/config":
            if data.get("reset"):
                cfg = SENSING.reset_config(data.get("scenario"))
            else:
                cfg = SENSING.save_config(data.get("config") or {}, scenario=data.get("applyScenario"))
            CAPTURE.update_sensing_config(cfg)
            self._send_json(sensing_payload())
        elif parsed.path == "/api/sensing/command":
            result = CAPTURE.sensing_command(str(data.get("command") or ""), data)
            if data.get("command") == "delete_profile" and data.get("label"):
                result["ok"] = SENSING.delete_profile(str(data["label"])) or result.get("ok")
            result["sensing"] = sensing_payload()
            self._send_json(result)
        elif parsed.path == "/api/analyze":
            try:
                self._send_json({"ok": True, "analysis": analyze_recording(data)})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)})
        elif parsed.path.startswith("/api/network/"):
            action = parsed.path.rsplit("/", 1)[-1]
            try:
                if action == "scan":
                    result = NETWORK.scan()
                elif action == "connect":
                    result = {"action": NETWORK.connect(data.get("ssid"), data.get("password") or "",
                                                        bool(data.get("hidden")), int(data.get("priority") or 50))}
                elif action == "activate":
                    result = {"action": NETWORK.activate(data.get("name"))}
                elif action == "forget":
                    result = {"ok": NETWORK.forget(data.get("name"))}
                elif action == "priority":
                    result = {"ok": NETWORK.set_priority(data.get("name"), int(data.get("priority") or 0),
                                                         bool(data.get("autoconnect", True)))}
                elif action == "hotspot":
                    result = {"action": NETWORK.hotspot(bool(data.get("on")))}
                elif action == "config":
                    result = {"config": NETWORK.save_config(data.get("config") or {})}
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                result.setdefault("ok", True)
                self._send_json(result)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)})
        elif parsed.path == "/api/sensing/calibration/delete":
            ok = SENSING.delete_calibration()
            self._send_json({"ok": ok, "sensing": sensing_payload()})
        elif parsed.path == "/api/stop":
            CAPTURE.stop()
            self._send_json(status_payload())
        elif parsed.path == "/api/reflection/start":
            status = REFLECTION.start(
                data.get("durationSecs", 60),
                data.get("label", "closed"),
                data.get("channel", DEFAULT_CHANNEL),
                data.get("sourceMac", ""),
                data.get("distanceM", 0),
                data.get("txRatePps", 20),
                data.get("warmupSecs", 14),
            )
            self._send_json({"reflection": status})
        elif parsed.path == "/api/reflection/stop":
            status = REFLECTION.stop()
            self._send_json({"reflection": status})
        elif parsed.path == "/api/reflection/build-profile":
            self._send_json(REFLECTION.build_profiles())
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
                CAPTURE.hub.publish("status", status_payload())
                self._send_json({"ok": True, "alarms": []})
                return
            alarm_id = str(data.get("id", ""))
            if not alarm_id:
                self.send_error(HTTPStatus.BAD_REQUEST, "missing alarm id")
                return
            removed = ALARMS.delete(alarm_id)
            CAPTURE.hub.publish("status", status_payload())
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
            self._write_sse("status", status_payload())
            while True:
                try:
                    event, payload = q.get(timeout=1.0)
                    self._write_sse(event, payload)
                except queue.Empty:
                    self._write_sse("status", status_payload())
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
    if not DEV_TX_MAC:
        NETWORK.start_watchdog()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"CSI web UI listening on http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        REFLECTION.stop()
        CAPTURE.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
