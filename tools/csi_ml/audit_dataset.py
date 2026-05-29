#!/usr/bin/env python3
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


def clean_float(value):
    try:
        if value is None:
            return None
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    return None


def summarize(values):
    nums = sorted(value for value in (clean_float(item) for item in values) if value is not None)
    if not nums:
        return {"count": 0, "min": None, "mean": None, "p90": None, "max": None}
    p90_index = min(len(nums) - 1, int(0.9 * (len(nums) - 1)))
    return {
        "count": len(nums),
        "min": nums[0],
        "mean": sum(nums) / len(nums),
        "p90": nums[p90_index],
        "max": nums[-1],
    }


def collect_paths(inputs):
    paths = []
    for value in inputs:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.ndjson")))
        elif path.exists():
            paths.append(path)
    return paths


def read_file(path, expected_tones):
    meta = {}
    samples = []
    malformed = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if row.get("type") == "session":
                meta = row
            elif row.get("type") == "sample":
                samples.append(row)

    label = str(meta.get("label") or "")
    if not label and samples:
        label = str(samples[0].get("label") or "")
    times = [
        clean_float(sample.get("receivedAt") or sample.get("ts"))
        for sample in samples
    ]
    times = [value for value in times if value is not None]
    duration_s = (max(times) - min(times)) / 1000.0 if len(times) >= 2 else None

    amp_lengths = [len(sample.get("amps") or []) for sample in samples]
    phase_lengths = [len(sample.get("phaseResiduals") or []) for sample in samples]
    missing_phase = sum(1 for length in phase_lengths if length == 0)
    bad_amp = sum(1 for length in amp_lengths if length != expected_tones)
    bad_phase = sum(1 for length in phase_lengths if length not in (0, expected_tones))

    warnings = []
    if malformed:
        warnings.append(f"malformed_json={malformed}")
    if not label:
        warnings.append("missing_label")
    if bad_amp:
        warnings.append(f"bad_amp_tones={bad_amp}")
    if bad_phase:
        warnings.append(f"bad_phase_tones={bad_phase}")
    if missing_phase:
        warnings.append(f"missing_phase={missing_phase}")

    return {
        "path": str(path),
        "file": path.name,
        "label": label,
        "samples": len(samples),
        "durationSeconds": duration_s,
        "schemaVersion": meta.get("schemaVersion"),
        "feature": meta.get("feature"),
        "features": meta.get("features"),
        "phaseCalibration": meta.get("phaseCalibration"),
        "ampToneLengths": summarize(amp_lengths),
        "phaseToneLengths": summarize(phase_lengths),
        "missingPhase": missing_phase,
        "badAmpTones": bad_amp,
        "badPhaseTones": bad_phase,
        "packetRate": summarize(sample.get("packetRate") for sample in samples),
        "motionScore": summarize(sample.get("motionScore") for sample in samples),
        "rssi": summarize(sample.get("rssi") for sample in samples),
        "warnings": warnings,
    }


def fmt(value, digits=2):
    if value is None:
        return "--"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def print_table(rows, by_label, args):
    print("CSI dataset audit")
    print(f"files={len(rows)} expected_tones={args.tones} require_phase={args.require_phase}")
    print()
    headers = [
        "file",
        "label",
        "samples",
        "duration_s",
        "phase",
        "pkt_mean",
        "motion_p90",
        "rssi_mean",
        "warnings",
    ]
    print(" | ".join(headers))
    print(" | ".join("---" for _ in headers))
    for row in rows:
        phase_ok = row["samples"] > 0 and row["missingPhase"] == 0 and row["badPhaseTones"] == 0
        values = [
            row["file"],
            row["label"] or "--",
            str(row["samples"]),
            fmt(row["durationSeconds"], 1),
            "ok" if phase_ok else f"missing {row['missingPhase']}",
            fmt(row["packetRate"]["mean"], 2),
            fmt(row["motionScore"]["p90"], 4),
            fmt(row["rssi"]["mean"], 1),
            ", ".join(row["warnings"]) or "-",
        ]
        print(" | ".join(values))

    print()
    print("By label")
    print("label | files | samples | duration_s | phase_ready")
    print("--- | ---: | ---: | ---: | ---")
    for label, group in sorted(by_label.items()):
        samples = sum(row["samples"] for row in group)
        duration = sum(row["durationSeconds"] or 0.0 for row in group)
        phase_ready = all(row["missingPhase"] == 0 and row["badPhaseTones"] == 0 for row in group)
        print(f"{label or '--'} | {len(group)} | {samples} | {duration:.1f} | {'yes' if phase_ready else 'no'}")


def validate(rows, by_label, args):
    issues = []
    for row in rows:
        if row["samples"] <= 0:
            issues.append(f"{row['file']}: no samples")
        if row["badAmpTones"]:
            issues.append(f"{row['file']}: {row['badAmpTones']} samples have wrong amp tone count")
        if args.require_phase and row["missingPhase"]:
            issues.append(f"{row['file']}: {row['missingPhase']} samples are missing phaseResiduals")
        if args.require_phase and row["badPhaseTones"]:
            issues.append(f"{row['file']}: {row['badPhaseTones']} samples have wrong phase tone count")
        if args.min_duration and (row["durationSeconds"] or 0.0) < args.min_duration:
            issues.append(f"{row['file']}: duration below {args.min_duration}s")
    for label, group in by_label.items():
        samples = sum(row["samples"] for row in group)
        if samples < args.min_samples_per_label:
            issues.append(f"label {label or '--'}: {samples} samples below target {args.min_samples_per_label}")
    return issues


def main():
    parser = argparse.ArgumentParser(description="Audit CSI NDJSON datasets before training.")
    parser.add_argument("inputs", nargs="+", help="NDJSON files or directories.")
    parser.add_argument("--tones", type=int, default=128)
    parser.add_argument("--require-phase", action="store_true")
    parser.add_argument("--min-samples-per-label", type=int, default=400)
    parser.add_argument("--min-duration", type=float, default=0.0)
    parser.add_argument("--json", dest="json_path", help="Optional path for JSON audit output.")
    args = parser.parse_args()

    paths = collect_paths(args.inputs)
    if not paths:
        raise SystemExit("No ndjson files found.")

    rows = [read_file(path, args.tones) for path in paths]
    by_label = defaultdict(list)
    for row in rows:
        by_label[row["label"]].append(row)
    issues = validate(rows, by_label, args)

    print_table(rows, by_label, args)
    print()
    if issues:
        print("Issues")
        for issue in issues:
            print(f"- {issue}")
    else:
        print("Issues: none")

    if args.json_path:
        payload = {
            "files": rows,
            "labels": {
                label: {
                    "files": len(group),
                    "samples": sum(row["samples"] for row in group),
                    "durationSeconds": sum(row["durationSeconds"] or 0.0 for row in group),
                    "phaseReady": all(row["missingPhase"] == 0 and row["badPhaseTones"] == 0 for row in group),
                }
                for label, group in sorted(by_label.items())
            },
            "issues": issues,
        }
        Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
