#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import numpy as np


def read_session(path):
    samples = []
    label = None
    meta = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") == "session":
                meta = row
                label = row.get("label") or label
            elif row.get("type") == "sample":
                amps = row.get("amps")
                row_label = row.get("label") or label
                if amps and row_label:
                    samples.append((np.asarray(amps, dtype=np.float32), row_label))
    return samples, meta


def build_windows(paths, window, stride):
    label_names = []
    label_to_id = {}
    xs = []
    ys = []
    session_meta = []

    for path in paths:
        samples, meta = read_session(path)
        session_meta.append({"path": str(path), **meta, "samples": len(samples)})
        if len(samples) < window:
            continue
        features = np.stack([item[0] for item in samples], axis=0)
        labels = [item[1] for item in samples]
        session_label = max(set(labels), key=labels.count)
        if session_label not in label_to_id:
            label_to_id[session_label] = len(label_names)
            label_names.append(session_label)

        for start in range(0, len(features) - window + 1, stride):
            clip = features[start:start + window]
            clip = (clip - clip.mean(axis=0, keepdims=True)) / (clip.std(axis=0, keepdims=True) + 1e-6)
            xs.append(clip.astype(np.float32))
            ys.append(label_to_id[session_label])

    if not xs:
        raise SystemExit("No windows produced. Collect longer labelled sessions first.")

    return np.stack(xs, axis=0), np.asarray(ys, dtype=np.int64), label_names, session_meta


def main():
    parser = argparse.ArgumentParser(description="Build CNN/LSTM windows from CSI WebUI ndjson sessions.")
    parser.add_argument("inputs", nargs="+", help="Dataset ndjson files or directories containing ndjson files.")
    parser.add_argument("-o", "--output", default="csi_dataset.npz")
    parser.add_argument("--window", type=int, default=64, help="Frames per training window.")
    parser.add_argument("--stride", type=int, default=16, help="Window stride in frames.")
    args = parser.parse_args()

    paths = []
    for value in args.inputs:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.ndjson")))
        else:
            paths.append(path)
    paths = [path for path in paths if path.exists()]
    if not paths:
        raise SystemExit("No input files found.")

    x, y, labels, meta = build_windows(paths, args.window, args.stride)
    np.savez_compressed(args.output, x=x, y=y, labels=np.asarray(labels), meta=json.dumps(meta))
    print(f"saved {args.output}: x={x.shape} y={y.shape} labels={labels}")


if __name__ == "__main__":
    main()
