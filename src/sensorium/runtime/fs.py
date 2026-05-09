#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, payload):
    ensure_parent(path)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, sort_keys=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def append_jsonl(path: Path, record: dict):
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=False) + "\n")


def rewrite_jsonl(path: Path, records):
    ensure_parent(path)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=False) + "\n")
    os.replace(tmp_path, path)


def read_jsonl_tail(path: Path, limit: int):
    if limit <= 0 or not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records = []
    tail = lines[-limit:]
    for line in tail:
        line = line.replace("\x00", "").strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
