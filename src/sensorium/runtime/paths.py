#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

from sensorium._paths import REPO_ROOT

_DEFAULT_STATE_ROOT = Path(
    os.environ.get(
        "XDG_STATE_HOME",
        str(Path.home() / ".local" / "state"),
    )
) / "sensorium"
RUNTIME_RUN_ROOT = Path("/run/sensorium")
RUNTIME_SYSTEM_STATE_ROOT = Path("/var/lib/sensorium")
RUNTIME_SYSTEM_LOG_ROOT = Path("/var/log/sensorium")
RUNTIME_SYSTEM_SOCKET_PATH = RUNTIME_RUN_ROOT / "sensoriumd.sock"
RUNTIME_SYSTEM_PIDFILE_PATH = RUNTIME_RUN_ROOT / "sensoriumd.pid"
RUNTIME_BRIDGE_PATH = Path("/dev/sensorium-runtime-bridge")
RUNTIME_SYSTEM_SNAPSHOT_PATH = RUNTIME_SYSTEM_STATE_ROOT / "sensoriumd-runtime-snapshot.json"
RUNTIME_SYSTEM_TRACE_PATH = RUNTIME_SYSTEM_STATE_ROOT / "sensoriumd-trace.jsonl"
RUNTIME_SYSTEM_DAEMON_LOG_PATH = RUNTIME_SYSTEM_LOG_ROOT / "sensoriumd.log"


def runtime_state_root() -> Path:
    return Path(
        os.environ.get("SENSORIUM_STATE_DIR", str(_DEFAULT_STATE_ROOT))
    ).expanduser()


def runtime_socket_path() -> Path:
    return Path(
        os.environ.get("SENSORIUM_SOCKET_PATH", str(RUNTIME_SYSTEM_SOCKET_PATH))
    ).expanduser()


def runtime_pidfile_path() -> Path:
    return Path(
        os.environ.get("SENSORIUM_PIDFILE_PATH", str(RUNTIME_SYSTEM_PIDFILE_PATH))
    ).expanduser()


def runtime_snapshot_path() -> Path:
    return Path(
        os.environ.get(
            "SENSORIUM_SNAPSHOT_PATH",
            str(runtime_state_root() / "sensoriumd-runtime-snapshot.json"),
        )
    ).expanduser()


def runtime_trace_path() -> Path:
    return Path(
        os.environ.get(
            "SENSORIUM_TRACE_PATH",
            str(runtime_state_root() / "sensoriumd-trace.jsonl"),
        )
    ).expanduser()


def runtime_benchmark_dir() -> Path:
    return Path(
        os.environ.get(
            "SENSORIUM_BENCHMARK_DIR",
            str(runtime_state_root() / "benchmarks"),
        )
    ).expanduser()


def runtime_daemon_log_path() -> Path:
    return Path(
        os.environ.get(
            "SENSORIUM_DAEMON_LOG_PATH",
            str(runtime_state_root() / "sensoriumd.log"),
        )
    ).expanduser()


RUNTIME_STATE_ROOT = runtime_state_root()
RUNTIME_SOCKET_PATH = runtime_socket_path()
RUNTIME_PIDFILE_PATH = runtime_pidfile_path()
RUNTIME_SNAPSHOT_PATH = runtime_snapshot_path()
RUNTIME_TRACE_PATH = runtime_trace_path()
RUNTIME_BENCHMARK_DIR = runtime_benchmark_dir()
RUNTIME_DAEMON_LOG_PATH = runtime_daemon_log_path()
