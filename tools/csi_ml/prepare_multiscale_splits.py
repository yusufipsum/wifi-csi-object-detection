#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import numpy as np

from prepare_temporal_splits import (
    build_feature_matrix,
    normalize_window,
    read_session,
    summarize_values,
)


def parse_windows(value):
    windows = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    if not windows or any(window <= 1 for window in windows):
        raise SystemExit("Windows must be comma-separated integers greater than 1.")
    return windows


def collect_paths(inputs):
    paths = []
    for value in inputs:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.ndjson")))
        else:
            paths.append(path)
    return [path for path in paths if path.exists()]


def make_multiscale_windows(features, start_idx, end_idx, windows, stride):
    max_window = max(windows)
    xs = {window: [] for window in windows}
    starts = []
    if end_idx - start_idx < max_window:
        return xs, starts
    last_start = end_idx - max_window
    for start in range(start_idx, last_start + 1, stride):
        end = start + max_window
        for window in windows:
            clip = features[end - window:end]
            xs[window].append(normalize_window(clip).astype(np.float32))
        starts.append(start)
    return xs, starts


def main():
    parser = argparse.ArgumentParser(
        description="Build temporally separated multi-scale CSI windows from labelled ndjson sessions."
    )
    parser.add_argument("inputs", nargs="+", help="Dataset ndjson files or directories containing ndjson files.")
    parser.add_argument("-o", "--output", default="data/csi/csi_multiscale_dataset.npz")
    parser.add_argument("--windows", default="16,48", help="Comma-separated sample windows, aligned by end time.")
    parser.add_argument("--stride", type=int, default=4, help="Window stride in dataset samples.")
    parser.add_argument("--train-ratio", type=float, default=0.60)
    parser.add_argument("--val-ratio", type=float, default=0.20)
    parser.add_argument("--purge", type=int, default=32, help="Sample gap between temporal splits.")
    parser.add_argument(
        "--features",
        default="amp,phase,amp_delta,phase_delta",
        help="Comma-separated feature channels: amp, amp_delta, amp_residual, phase, phase_delta.",
    )
    parser.add_argument("--residual-radius", type=int, default=15)
    args = parser.parse_args()

    windows = parse_windows(args.windows)
    feature_names = [item.strip() for item in args.features.split(",") if item.strip()]
    if not feature_names:
        raise SystemExit("At least one feature must be selected.")

    paths = collect_paths(args.inputs)
    if not paths:
        raise SystemExit("No input ndjson files found.")

    labels = []
    label_to_id = {}
    splits = {
        name: {"x": {window: [] for window in windows}, "y": [], "session": [], "start": []}
        for name in ("train", "val", "test")
    }
    report = {
        "modelFamily": "multiscale",
        "windows": windows,
        "stride": args.stride,
        "trainRatio": args.train_ratio,
        "valRatio": args.val_ratio,
        "testRatio": 1.0 - args.train_ratio - args.val_ratio,
        "purge": args.purge,
        "featureNames": feature_names,
        "residualRadius": args.residual_radius,
        "alignment": "same_end_time",
        "sessions": [],
    }

    for session_idx, path in enumerate(paths):
        meta, samples = read_session(path)
        if not samples:
            continue
        labels_in_file = [sample["label"] for sample in samples]
        session_label = max(set(labels_in_file), key=labels_in_file.count)
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
            xs_by_window, starts = make_multiscale_windows(
                features,
                start_idx,
                end_idx,
                windows,
                args.stride,
            )
            split_counts[split_name] = len(starts)
            for window in windows:
                splits[split_name]["x"][window].extend(xs_by_window[window])
            splits[split_name]["y"].extend([label_id] * len(starts))
            splits[split_name]["session"].extend([session_idx] * len(starts))
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
        "windows": np.asarray(windows, dtype=np.int64),
        "featureNames": np.asarray(feature_names),
        "channels": np.asarray([len(feature_names)], dtype=np.int64),
        "report": json.dumps(report, ensure_ascii=False, indent=2),
    }
    for split_name, values in splits.items():
        if not values["y"]:
            raise SystemExit(f"No {split_name} windows produced. Try smaller windows or purge.")
        payload[f"y_{split_name}"] = np.asarray(values["y"], dtype=np.int64)
        payload[f"session_{split_name}"] = np.asarray(values["session"], dtype=np.int64)
        payload[f"start_{split_name}"] = np.asarray(values["start"], dtype=np.int64)
        for window in windows:
            rows = values["x"][window]
            if len(rows) != len(values["y"]):
                raise SystemExit(f"Internal error for split={split_name} window={window}.")
            payload[f"x_w{window}_{split_name}"] = np.stack(rows, axis=0).astype(np.float32)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, **payload)

    print(f"saved {output}")
    print(f"labels={labels}")
    print(f"windows={windows} features={feature_names}")
    for split_name in ("train", "val", "test"):
        y = payload[f"y_{split_name}"]
        counts = {labels[idx]: int((y == idx).sum()) for idx in range(len(labels))}
        shapes = {
            f"w{window}": payload[f"x_w{window}_{split_name}"].shape
            for window in windows
        }
        print(f"{split_name}: shapes={shapes} counts={counts}")


if __name__ == "__main__":
    main()
