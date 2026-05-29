#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import numpy as np


def read_session(path):
    meta = {}
    samples = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") == "session":
                meta = row
            elif row.get("type") == "sample":
                amps = row.get("amps")
                label = row.get("label") or meta.get("label")
                if amps and label:
                    sample = {
                        "amps": np.asarray(amps, dtype=np.float32),
                        "label": str(label),
                        "ts": row.get("receivedAt") or row.get("ts"),
                        "packetRate": row.get("packetRate"),
                        "motionScore": row.get("motionScore"),
                        "rssi": row.get("rssi"),
                    }
                    if row.get("phaseResiduals"):
                        sample["phaseResiduals"] = np.asarray(row["phaseResiduals"], dtype=np.float32)
                    samples.append(sample)
    return meta, samples


def moving_average(values, radius):
    if radius <= 0 or len(values) <= 1:
        return values.copy()
    cumsum = np.concatenate(
        [np.zeros((1,) + values.shape[1:], dtype=np.float32), np.cumsum(values, axis=0)],
        axis=0,
    )
    result = np.empty_like(values, dtype=np.float32)
    for idx in range(len(values)):
        start = max(0, idx - radius)
        end = min(len(values), idx + radius + 1)
        result[idx] = (cumsum[end] - cumsum[start]) / float(end - start)
    return result


def delta(values):
    result = np.zeros_like(values, dtype=np.float32)
    if len(values) > 1:
        result[1:] = values[1:] - values[:-1]
    return result


def stack_required(samples, key, path, feature_name):
    rows = []
    for idx, sample in enumerate(samples):
        value = sample.get(key)
        if value is None:
            raise SystemExit(
                f"Feature '{feature_name}' requires '{key}', but {path} sample {idx} does not contain it."
            )
        rows.append(value)
    return np.stack(rows, axis=0).astype(np.float32)


def build_feature_matrix(samples, feature_names, residual_radius, path):
    amps = stack_required(samples, "amps", path, "amp")
    cache = {"amp": amps}
    channels = []
    for name in feature_names:
        if name == "amp":
            value = cache["amp"]
        elif name == "amp_delta":
            value = cache.setdefault("amp_delta", delta(cache["amp"]))
        elif name == "amp_residual":
            baseline = moving_average(cache["amp"], residual_radius)
            value = cache.setdefault("amp_residual", cache["amp"] - baseline)
        elif name == "phase":
            value = cache.setdefault(
                "phase",
                stack_required(samples, "phaseResiduals", path, "phase"),
            )
        elif name == "phase_delta":
            phase = cache.setdefault(
                "phase",
                stack_required(samples, "phaseResiduals", path, "phase_delta"),
            )
            value = cache.setdefault("phase_delta", delta(phase))
        else:
            raise SystemExit(
                f"Unknown feature '{name}'. Use amp, amp_delta, amp_residual, phase, phase_delta."
            )
        channels.append(value)
    if len(channels) == 1:
        return channels[0]
    return np.stack(channels, axis=1).astype(np.float32)


def normalize_window(window):
    centered = window - window.mean(axis=0, keepdims=True)
    return centered / (window.std(axis=0, keepdims=True) + 1e-6)


def make_windows(features, start_idx, end_idx, window, stride):
    xs = []
    starts = []
    if end_idx - start_idx < window:
        return xs, starts
    last_start = end_idx - window
    for start in range(start_idx, last_start + 1, stride):
        clip = normalize_window(features[start:start + window])
        xs.append(clip.astype(np.float32))
        starts.append(start)
    return xs, starts


def summarize_values(values):
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"mean": None, "min": None, "max": None}
    return {
        "mean": float(np.mean(vals)),
        "min": float(np.min(vals)),
        "max": float(np.max(vals)),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Build temporally separated train/val/test CSI windows from labelled ndjson sessions."
    )
    parser.add_argument("inputs", nargs="+", help="Dataset ndjson files or directories containing ndjson files.")
    parser.add_argument("-o", "--output", default="data/csi/csi_temporal_dataset.npz")
    parser.add_argument("--window", type=int, default=32, help="Samples per window.")
    parser.add_argument("--stride", type=int, default=4, help="Window stride in samples.")
    parser.add_argument("--train-ratio", type=float, default=0.60)
    parser.add_argument("--val-ratio", type=float, default=0.20)
    parser.add_argument("--purge", type=int, default=16, help="Sample gap between temporal splits.")
    parser.add_argument(
        "--features",
        default="amp",
        help="Comma-separated feature channels: amp, amp_delta, amp_residual, phase, phase_delta.",
    )
    parser.add_argument(
        "--residual-radius",
        type=int,
        default=15,
        help="Radius for amp_residual moving average in dataset samples.",
    )
    args = parser.parse_args()
    feature_names = [item.strip() for item in args.features.split(",") if item.strip()]
    if not feature_names:
        raise SystemExit("At least one feature must be selected.")

    paths = []
    for value in args.inputs:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.ndjson")))
        else:
            paths.append(path)
    paths = [path for path in paths if path.exists()]
    if not paths:
        raise SystemExit("No input ndjson files found.")

    labels = []
    label_to_id = {}
    splits = {name: {"x": [], "y": [], "session": [], "start": []} for name in ("train", "val", "test")}
    report = {
        "window": args.window,
        "stride": args.stride,
        "trainRatio": args.train_ratio,
        "valRatio": args.val_ratio,
        "testRatio": 1.0 - args.train_ratio - args.val_ratio,
        "purge": args.purge,
        "featureNames": feature_names,
        "residualRadius": args.residual_radius,
        "sessions": [],
    }

    for session_idx, path in enumerate(paths):
        meta, samples = read_session(path)
        if not samples:
            continue
        session_label = max(set(sample["label"] for sample in samples), key=[sample["label"] for sample in samples].count)
        if session_label not in label_to_id:
            label_to_id[session_label] = len(labels)
            labels.append(session_label)
        label_id = label_to_id[session_label]

        features = build_feature_matrix(samples, feature_names, args.residual_radius, path)
        n = len(samples)
        train_end = int(n * args.train_ratio)
        val_start = min(n, train_end + args.purge)
        val_end = int(n * (args.train_ratio + args.val_ratio))
        test_start = min(n, val_end + args.purge)
        bounds = {
            "train": (0, train_end),
            "val": (val_start, val_end),
            "test": (test_start, n),
        }

        split_counts = {}
        for split_name, (start_idx, end_idx) in bounds.items():
            xs, starts = make_windows(features, start_idx, end_idx, args.window, args.stride)
            split_counts[split_name] = len(xs)
            splits[split_name]["x"].extend(xs)
            splits[split_name]["y"].extend([label_id] * len(xs))
            splits[split_name]["session"].extend([session_idx] * len(xs))
            splits[split_name]["start"].extend(starts)

        times = [sample["ts"] for sample in samples if sample.get("ts") is not None]
        duration_s = (max(times) - min(times)) / 1000.0 if len(times) >= 2 else None
        report["sessions"].append({
            "path": str(path),
            "label": session_label,
            "samples": n,
            "durationSeconds": duration_s,
            "tones": int(features.shape[-1]),
            "channels": len(feature_names),
            "featureNames": feature_names,
            "featureShape": list(features.shape[1:]),
            "splitBounds": bounds,
            "windows": split_counts,
            "packetRate": summarize_values(sample.get("packetRate") for sample in samples),
            "motionScore": summarize_values(sample.get("motionScore") for sample in samples),
            "rssi": summarize_values(sample.get("rssi") for sample in samples),
            "meta": meta,
        })

    if not labels:
        raise SystemExit("No labelled samples found.")

    payload = {
        "labels": np.asarray(labels),
        "featureNames": np.asarray(feature_names),
        "channels": np.asarray([len(feature_names)], dtype=np.int64),
        "report": json.dumps(report, ensure_ascii=False, indent=2),
    }
    for split_name, values in splits.items():
        if not values["x"]:
            raise SystemExit(f"No {split_name} windows produced. Try a smaller window or purge.")
        payload[f"x_{split_name}"] = np.stack(values["x"], axis=0).astype(np.float32)
        payload[f"y_{split_name}"] = np.asarray(values["y"], dtype=np.int64)
        payload[f"session_{split_name}"] = np.asarray(values["session"], dtype=np.int64)
        payload[f"start_{split_name}"] = np.asarray(values["start"], dtype=np.int64)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, **payload)

    print(f"saved {output}")
    print(f"labels={labels}")
    for split_name in ("train", "val", "test"):
        x = payload[f"x_{split_name}"]
        y = payload[f"y_{split_name}"]
        counts = {labels[idx]: int((y == idx).sum()) for idx in range(len(labels))}
        print(f"{split_name}: x={x.shape} counts={counts}")


if __name__ == "__main__":
    main()
