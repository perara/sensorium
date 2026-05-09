#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sensorium._paths import REPO_ROOT


def parse_value(raw: str):
    raw = raw.strip()
    if re.fullmatch(r"-?[0-9]+", raw):
        return int(raw)
    if re.fullmatch(r"-?[0-9]+\.[0-9]+", raw):
        return float(raw)
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    return raw


def default_benchmark_dir() -> Path:
    return Path(
        os.environ.get(
            "SENSORIUM_BENCHMARK_DIR",
            str(REPO_ROOT / ".cache" / "benchmarks"),
        )
    ).expanduser()


def parse_benchmark_output(lines: list[str]):
    blocks = []
    current = {"label": "default", "metrics": {}}

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("==> "):
            if current["metrics"] or current["label"] != "default":
                blocks.append(current)
            current = {"label": line[4:].strip(), "metrics": {}}
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z0-9_./:-]+", key):
            continue
        current["metrics"][key] = parse_value(value)

    if current["metrics"] or not blocks:
        blocks.append(current)

    return blocks


def aggregate_sample_blocks(blocks: list[dict]):
    sample_pattern = re.compile(r"sample (\d+)/(.*)")
    grouped: dict[str, list[dict]] = {}
    max_sample_index = 0

    for block in blocks:
        label = block.get("label", "")
        match = sample_pattern.fullmatch(label)
        if not match:
            return blocks, None
        base_label = match.group(2) or "default"
        grouped.setdefault(base_label, []).append(block.get("metrics", {}))
        max_sample_index = max(max_sample_index, int(match.group(1)))

    if max_sample_index <= 1:
        return blocks, 1 if blocks else None

    aggregated = []
    for label, metric_sets in grouped.items():
        metric_names = sorted({name for metrics in metric_sets for name in metrics})
        merged_metrics = {}
        for name in metric_names:
            values = [metrics[name] for metrics in metric_sets if name in metrics]
            if not values:
                continue
            if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
                merged_metrics[name] = statistics.median(values)
            elif all(value == values[0] for value in values):
                merged_metrics[name] = values[0]
        aggregated.append({"label": label, "metrics": merged_metrics})

    return aggregated, max_sample_index


def current_git_metadata(repo_root: Path):
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
        ).strip()
    except Exception:
        commit = None

    try:
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--short"],
                cwd=repo_root,
                text=True,
            ).strip()
        )
    except Exception:
        dirty = None

    return commit, dirty


def main():
    parser = argparse.ArgumentParser(description="Record a benchmark artifact from benchmark stdout")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--source-url", default="")
    parser.add_argument("--remote-kernel", default="")
    parser.add_argument("--remote-target", default="")
    parser.add_argument("--artifact-dir", type=Path, default=default_benchmark_dir())
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--meta", action="append", default=[], help="Additional metadata entries as key=value")
    args = parser.parse_args()

    lines = sys.stdin.read().splitlines()
    blocks = parse_benchmark_output(lines)
    blocks, sample_count = aggregate_sample_blocks(blocks)
    commit, dirty = current_git_metadata(args.repo_root)

    metadata = {}
    for item in args.meta:
        if "=" not in item:
            raise SystemExit(f"invalid --meta entry: {item!r}")
        key, value = item.split("=", 1)
        metadata[key] = parse_value(value)

    created_at = datetime.now(timezone.utc)
    payload = {
        "schema_version": 2,
        "scenario": args.scenario,
        "created_at": created_at.isoformat(),
        "source_url": args.source_url,
        "remote_kernel": args.remote_kernel or None,
        "remote_target": args.remote_target or None,
        "git_commit": commit,
        "git_dirty": dirty,
        "sample_count": sample_count,
        "benchmarks": blocks,
        "metadata": metadata,
        "raw_stdout": "\n".join(lines),
    }

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    artifact_path = args.artifact_dir / f"{args.scenario}-{timestamp}.json"
    artifact_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(artifact_path)


if __name__ == "__main__":
    raise SystemExit(main())
