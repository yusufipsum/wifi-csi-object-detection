#!/usr/bin/env python3
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from prepare_multiscale_splits import make_multiscale_windows, parse_windows
from prepare_temporal_splits import build_feature_matrix, read_session, summarize_values


def collect_paths(inputs):
    paths = []
    for value in inputs:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.ndjson")))
        elif path.exists():
            paths.append(path)
    return sorted(paths)


def session_label(samples):
    labels = [sample["label"] for sample in samples]
    return max(set(labels), key=labels.count)


def add_windows(target, split, label_id, session_idx, features, start_idx, end_idx, windows, stride):
    xs_by_window, starts = make_multiscale_windows(features, start_idx, end_idx, windows, stride)
    for window in windows:
        target[split]["x"][window].extend(xs_by_window[window])
    target[split]["y"].extend([label_id] * len(starts))
    target[split]["session"].extend([session_idx] * len(starts))
    target[split]["start"].extend(starts)
    return len(starts)


def latest_per_label(rows):
    selected = set()
    by_label = defaultdict(list)
    for row in rows:
        by_label[row["label"]].append(row)
    for group in by_label.values():
        selected.add(sorted(group, key=lambda row: row["path"].name)[-1]["path"])
    return selected


def parse_test_files(value):
    if not value:
        return set()
    return {Path(item.strip()).name for item in value.split(",") if item.strip()}


def main():
    parser = argparse.ArgumentParser(
        description="Build multi-scale CSI splits with session-disjoint test files."
    )
    parser.add_argument("inputs", nargs="+", help="Dataset ndjson files or directories.")
    parser.add_argument("-o", "--output", default="data/csi/csi_multiscale_session_holdout.npz")
    parser.add_argument("--windows", default="16,48")
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--train-ratio", type=float, default=0.78, help="Train ratio inside non-test sessions.")
    parser.add_argument("--purge", type=int, default=32, help="Gap between train and val inside non-test sessions.")
    parser.add_argument(
        "--features",
        default="amp,phase,amp_delta,phase_delta",
        help="Comma-separated feature channels: amp, amp_delta, amp_residual, phase, phase_delta.",
    )
    parser.add_argument("--residual-radius", type=int, default=15)
    parser.add_argument(
        "--test-policy",
        choices=["latest-per-label", "explicit"],
        default="latest-per-label",
    )
    parser.add_argument(
        "--test-files",
        default="",
        help="Comma-separated file names for --test-policy explicit.",
    )
    args = parser.parse_args()

    windows = parse_windows(args.windows)
    feature_names = [item.strip() for item in args.features.split(",") if item.strip()]
    if not feature_names:
        raise SystemExit("At least one feature must be selected.")

    paths = collect_paths(args.inputs)
    if not paths:
        raise SystemExit("No input ndjson files found.")

    rows = []
    labels = []
    label_to_id = {}
    for session_idx, path in enumerate(paths):
        meta, samples = read_session(path)
        if not samples:
            continue
        label = session_label(samples)
        if label not in label_to_id:
            label_to_id[label] = len(labels)
            labels.append(label)
        features = build_feature_matrix(samples, feature_names, args.residual_radius, path)
        rows.append({
            "sessionIdx": session_idx,
            "path": path,
            "meta": meta,
            "samples": samples,
            "label": label,
            "labelId": label_to_id[label],
            "features": features,
        })

    if not rows:
        raise SystemExit("No labelled samples found.")

    if args.test_policy == "latest-per-label":
        test_paths = latest_per_label(rows)
    else:
        wanted = parse_test_files(args.test_files)
        if not wanted:
            raise SystemExit("--test-files is required for --test-policy explicit.")
        test_paths = {row["path"] for row in rows if row["path"].name in wanted}
        missing = wanted - {path.name for path in test_paths}
        if missing:
            raise SystemExit(f"Explicit test files not found: {sorted(missing)}")

    splits = {
        name: {"x": {window: [] for window in windows}, "y": [], "session": [], "start": []}
        for name in ("train", "val", "test")
    }
    report = {
        "modelFamily": "multiscale",
        "splitMode": "session_holdout_test",
        "testPolicy": args.test_policy,
        "testFiles": sorted(path.name for path in test_paths),
        "windows": windows,
        "stride": args.stride,
        "trainRatioWithinTrainSessions": args.train_ratio,
        "purge": args.purge,
        "featureNames": feature_names,
        "residualRadius": args.residual_radius,
        "alignment": "same_end_time",
        "sessions": [],
    }

    for row in rows:
        path = row["path"]
        features = row["features"]
        samples = row["samples"]
        n = len(samples)
        if path in test_paths:
            split_counts = {
                "train": 0,
                "val": 0,
                "test": add_windows(
                    splits,
                    "test",
                    row["labelId"],
                    row["sessionIdx"],
                    features,
                    0,
                    n,
                    windows,
                    args.stride,
                ),
            }
            bounds = {"test": (0, n)}
        else:
            train_end = int(n * args.train_ratio)
            val_start = min(n, train_end + args.purge)
            split_counts = {
                "train": add_windows(
                    splits,
                    "train",
                    row["labelId"],
                    row["sessionIdx"],
                    features,
                    0,
                    train_end,
                    windows,
                    args.stride,
                ),
                "val": add_windows(
                    splits,
                    "val",
                    row["labelId"],
                    row["sessionIdx"],
                    features,
                    val_start,
                    n,
                    windows,
                    args.stride,
                ),
                "test": 0,
            }
            bounds = {"train": (0, train_end), "val": (val_start, n)}

        times = [sample["ts"] for sample in samples if sample.get("ts") is not None]
        duration_s = (max(times) - min(times)) / 1000.0 if len(times) >= 2 else None
        report["sessions"].append({
            "path": str(path),
            "role": "test" if path in test_paths else "train_val",
            "label": row["label"],
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
            "meta": row["meta"],
        })

    payload = {
        "labels": np.asarray(labels),
        "windows": np.asarray(windows, dtype=np.int64),
        "featureNames": np.asarray(feature_names),
        "channels": np.asarray([len(feature_names)], dtype=np.int64),
        "report": json.dumps(report, ensure_ascii=False, indent=2),
    }
    for split_name, values in splits.items():
        if not values["y"]:
            raise SystemExit(f"No {split_name} windows produced. Adjust split settings.")
        payload[f"y_{split_name}"] = np.asarray(values["y"], dtype=np.int64)
        payload[f"session_{split_name}"] = np.asarray(values["session"], dtype=np.int64)
        payload[f"start_{split_name}"] = np.asarray(values["start"], dtype=np.int64)
        for window in windows:
            rows_for_window = values["x"][window]
            if len(rows_for_window) != len(values["y"]):
                raise SystemExit(f"Internal error for split={split_name} window={window}.")
            payload[f"x_w{window}_{split_name}"] = np.stack(rows_for_window, axis=0).astype(np.float32)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, **payload)

    print(f"saved {output}")
    print(f"labels={labels}")
    print(f"windows={windows} features={feature_names}")
    print(f"test_files={sorted(path.name for path in test_paths)}")
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
