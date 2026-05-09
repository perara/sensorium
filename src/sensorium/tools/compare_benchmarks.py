#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from pathlib import Path

from sensorium._paths import REPO_ROOT


KEY_METRICS = (
    "first_frame_latency_ms",
    "timestamp_fps",
    "record_fps",
    "capture_cpu_pct",
    "capture_rss_kb",
    "stream_cpu_pct",
    "record_bytes",
)

METADATA_KEYS = (
    "benchmark_env_ok",
    "benchmark_qemu_accel",
    "benchmark_host_nproc",
    "benchmark_host_load1",
    "remote_nproc",
    "qemu_cpus",
    "qemu_memory_mb",
)


def default_benchmark_dir() -> Path:
    return Path(
        os.environ.get(
            "SENSORIUM_BENCHMARK_DIR",
            str(REPO_ROOT / ".cache" / "benchmarks"),
        )
    ).expanduser()


def load_payload(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def benchmark_key(entry: dict, index: int):
    metrics = entry.get("metrics", {})
    if "sensor_target_fps" in metrics:
        return f"fps:{metrics['sensor_target_fps']}"
    label = entry.get("label")
    if label:
        return label
    return f"index:{index}"


def latest_artifacts_for_scenario(directory: Path, scenario: str):
    payloads = []
    for path in sorted(directory.glob("*.json")):
        payload = load_payload(path)
        if payload.get("scenario") == scenario:
            payloads.append((path, payload))
    return payloads


def latest_two_artifacts(directory: Path):
    paths = sorted(directory.glob("*.json"))
    if len(paths) < 2:
        raise SystemExit(f"need at least two benchmark artifacts in {directory}")
    return paths[-2], paths[-1]


def choose_default_comparison(directory: Path, baseline_window: int):
    all_paths = sorted(directory.glob("*.json"))
    if len(all_paths) < 2:
        raise SystemExit(f"need at least two benchmark artifacts in {directory}")

    latest_path = all_paths[-1]
    latest_payload = load_payload(latest_path)
    scenario = latest_payload.get("scenario")
    scenario_artifacts = latest_artifacts_for_scenario(directory, scenario)
    if len(scenario_artifacts) < 2:
        raise SystemExit(f"need at least two benchmark artifacts for scenario {scenario!r}")

    after_path, after_payload = scenario_artifacts[-1]
    before_group = scenario_artifacts[-(baseline_window + 1):-1]
    if not before_group:
        raise SystemExit(f"need at least one prior benchmark artifact for scenario {scenario!r}")
    before_paths = [path for path, _ in before_group]
    before_payloads = [payload for _, payload in before_group]
    return before_paths, before_payloads, after_path, after_payload


def aggregate_payloads(payloads: list[dict], *, scenario: str):
    grouped = {}
    for payload in payloads:
        for index, entry in enumerate(payload.get("benchmarks", [])):
            key = benchmark_key(entry, index)
            grouped.setdefault(key, []).append(entry.get("metrics", {}))

    benchmarks = []
    for key in sorted(grouped):
        metric_lists = grouped[key]
        metric_names = sorted({name for metrics in metric_lists for name in metrics})
        merged_metrics = {}
        for metric in metric_names:
            values = [
                metrics[metric]
                for metrics in metric_lists
                if isinstance(metrics.get(metric), (int, float))
            ]
            if values:
                merged_metrics[metric] = statistics.median(values)
        benchmarks.append({"label": key, "metrics": merged_metrics})

    return {
        "scenario": scenario,
        "benchmarks": benchmarks,
    }


def format_delta(before, after):
    delta = after - before
    if isinstance(before, int) and isinstance(after, int):
        return f"{delta:+d}"
    return f"{delta:+.2f}"


def maybe_ratio(before, after):
    if not before:
        return None
    return after / before


def main():
    parser = argparse.ArgumentParser(description="Compare two benchmark artifacts")
    parser.add_argument("before", nargs="?")
    parser.add_argument("after", nargs="?")
    parser.add_argument("--artifact-dir", type=Path, default=default_benchmark_dir())
    parser.add_argument(
        "--allow-missing-baseline",
        action="store_true",
        help="exit successfully when the default artifact directory has no comparable baseline",
    )
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--max-first-frame-delta-ms", type=float, default=None)
    parser.add_argument("--min-record-fps-ratio", type=float, default=None)
    parser.add_argument("--min-timestamp-fps-ratio", type=float, default=None)
    parser.add_argument(
        "--prefer-sustained-rate",
        action="store_true",
        help="treat timestamp_fps as the primary throughput gate and only show record_fps as context",
    )
    parser.add_argument("--baseline-window", type=int, default=3)
    args = parser.parse_args()

    if args.before and args.after:
        before_path = Path(args.before)
        after_path = Path(args.after)
        before = load_payload(before_path)
        after = load_payload(after_path)
        before_label = str(before_path)
        after_label = str(after_path)
    else:
        try:
            before_paths, before_payloads, after_path, after = choose_default_comparison(
                args.artifact_dir,
                max(1, args.baseline_window),
            )
        except SystemExit as exc:
            if args.allow_missing_baseline:
                print(f"benchmark baseline unavailable: {exc}; skipping comparison.")
                return 0
            raise
        before = aggregate_payloads(
            before_payloads,
            scenario=after.get("scenario", "unknown"),
        )
        before_label = f"median of {len(before_paths)} prior artifact(s) for scenario {after.get('scenario')}"
        after_label = str(after_path)

    print(f"before: {before_label}")
    print(f"after:  {after_label}")

    before_meta = before.get("metadata", {})
    after_meta = after.get("metadata", {})
    metadata_notes = []
    for key in METADATA_KEYS:
        if key in before_meta or key in after_meta:
            before_value = before_meta.get(key)
            after_value = after_meta.get(key)
            if before_value != after_value:
                metadata_notes.append(f"{key}: {before_value!r} -> {after_value!r}")
    if metadata_notes:
        print()
        print("environment differences:")
        for item in metadata_notes:
            print(f"  {item}")

    before_entries = {
        benchmark_key(entry, index): entry for index, entry in enumerate(before.get("benchmarks", []))
    }
    after_entries = {
        benchmark_key(entry, index): entry for index, entry in enumerate(after.get("benchmarks", []))
    }

    common_keys = [key for key in before_entries if key in after_entries]
    if not common_keys:
        raise SystemExit("no comparable benchmark blocks found")

    regressions = []
    for key in common_keys:
        print()
        print(f"[{key}]")
        before_metrics = before_entries[key].get("metrics", {})
        after_metrics = after_entries[key].get("metrics", {})
        for metric in KEY_METRICS:
            if metric not in before_metrics or metric not in after_metrics:
                continue
            before_value = before_metrics[metric]
            after_value = after_metrics[metric]
            if not isinstance(before_value, (int, float)) or not isinstance(after_value, (int, float)):
                continue
            ratio = maybe_ratio(before_value, after_value)
            ratio_text = ""
            if ratio is not None and math.isfinite(ratio):
                ratio_text = f" ({ratio:.3f}x)"
            print(
                f"  {metric}: {before_value} -> {after_value} "
                f"[{format_delta(before_value, after_value)}]{ratio_text}"
            )

        before_latency = before_metrics.get("first_frame_latency_ms")
        after_latency = after_metrics.get("first_frame_latency_ms")
        if (
            args.max_first_frame_delta_ms is not None
            and isinstance(before_latency, (int, float))
            and isinstance(after_latency, (int, float))
            and after_latency - before_latency > args.max_first_frame_delta_ms
        ):
            regressions.append(
                f"{key}: first_frame_latency_ms regressed by {after_latency - before_latency:.2f} ms"
            )

        before_timestamp_fps = before_metrics.get("timestamp_fps")
        after_timestamp_fps = after_metrics.get("timestamp_fps")
        if (
            args.min_timestamp_fps_ratio is not None
            and isinstance(before_timestamp_fps, (int, float))
            and isinstance(after_timestamp_fps, (int, float))
            and before_timestamp_fps > 0
            and (after_timestamp_fps / before_timestamp_fps) < args.min_timestamp_fps_ratio
        ):
            regressions.append(
                f"{key}: timestamp_fps ratio {after_timestamp_fps / before_timestamp_fps:.3f} below {args.min_timestamp_fps_ratio:.3f}"
            )

        before_fps = before_metrics.get("record_fps")
        after_fps = after_metrics.get("record_fps")
        if (
            args.min_record_fps_ratio is not None
            and isinstance(before_fps, (int, float))
            and isinstance(after_fps, (int, float))
            and before_fps > 0
            and (after_fps / before_fps) < args.min_record_fps_ratio
        ):
            if args.prefer_sustained_rate and isinstance(before_timestamp_fps, (int, float)) and isinstance(after_timestamp_fps, (int, float)):
                regressions.append(
                    f"{key}: record_fps ratio {after_fps / before_fps:.3f} below {args.min_record_fps_ratio:.3f} "
                    f"(startup/whole-run metric; sustained timestamp_fps ratio {after_timestamp_fps / before_timestamp_fps:.3f})"
                )
            else:
                regressions.append(
                    f"{key}: record_fps ratio {after_fps / before_fps:.3f} below {args.min_record_fps_ratio:.3f}"
                )

    if regressions:
        print()
        print("regressions:")
        for item in regressions:
            print(f"  - {item}")
        if args.fail_on_regression:
            raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
