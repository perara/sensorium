#!/usr/bin/env python3
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml
from sensorium.model_control import (
    REPO_ROOT,
    ModelError,
    build_apply_env,
    iter_model_paths,
    kernel_build_is_current,
    model_matches_module_state,
    normalize_model,
    read_module_state,
)
from sensorium.runtime.common import (
    RUNTIME_SYSTEM_SOCKET_PATH,
    rpc_call,
    runtime_pidfile_path,
    runtime_socket_path,
)
RELOAD_SCRIPT = REPO_ROOT / "scripts" / "runtime" / "reload-sensorium.sh"
SENSORIUMD_SCRIPT = REPO_ROOT / "scripts" / "runtime" / "sensoriumd"
SENSORIUMD_SYSTEMD_UNIT = "sensoriumd.service"


def _env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _privileged_cmd(cmd: list[str], *, action: str) -> list[str]:
    if os.geteuid() == 0:
        return list(cmd)
    sudo = shutil.which("sudo")
    if sudo is None:
        raise ModelError(f"{action} requires root privileges; run as root or install sudo")
    return [sudo, *cmd]


def _systemd_available() -> bool:
    return shutil.which("systemctl") is not None and Path("/run/systemd/system").exists()


def _systemd_service_load_state() -> str | None:
    if not _systemd_available():
        return None
    result = subprocess.run(
        [
            "systemctl",
            "show",
            "--property=LoadState",
            "--value",
            SENSORIUMD_SYSTEMD_UNIT,
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        return None
    load_state = result.stdout.strip()
    return load_state or None


def _systemd_service_execstart() -> str | None:
    if not _systemd_available():
        return None
    result = subprocess.run(
        [
            "systemctl",
            "show",
            "--property=ExecStart",
            "--value",
            SENSORIUMD_SYSTEMD_UNIT,
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        return None
    execstart = result.stdout.strip()
    return execstart or None


def _normalize_exec_path(path_value: str) -> str:
    try:
        return str(Path(path_value).resolve(strict=False))
    except OSError:
        return str(Path(path_value))


def _systemd_service_execstart_paths() -> set[str]:
    execstart = _systemd_service_execstart()
    if execstart is None:
        return set()

    paths: set[str] = set()

    for match in re.finditer(r"\bpath=([^ ;]+)", execstart):
        paths.add(_normalize_exec_path(match.group(1)))

    argv_match = re.search(r"\bargv\[\]=(.+?)(?:\s*;\s*\w+=|$)", execstart)
    if argv_match:
        argv_text = argv_match.group(1).strip()
        try:
            argv = shlex.split(argv_text)
        except ValueError:
            argv = argv_text.split()
        if argv:
            paths.add(_normalize_exec_path(argv[0]))

    try:
        argv = shlex.split(execstart)
    except ValueError:
        argv = execstart.split()
    if argv:
        paths.add(_normalize_exec_path(argv[0]))

    return paths


def _systemd_service_matches_current_script() -> bool:
    expected = _normalize_exec_path(str(SENSORIUMD_SCRIPT))
    return expected in _systemd_service_execstart_paths()


def _systemd_service_active() -> bool:
    if not _systemd_available():
        return False
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", SENSORIUMD_SYSTEMD_UNIT],
        check=False,
        cwd=REPO_ROOT,
    )
    return result.returncode == 0


def _systemd_management_enabled() -> bool:
    override = _env_flag("SENSORIUMD_MANAGED_BY_SYSTEMD")
    if override is not None:
        return override
    return (
        _systemd_service_load_state() == "loaded"
        and _systemd_service_matches_current_script()
    )


def _foreign_systemd_service_conflict() -> str | None:
    if _systemd_management_enabled():
        return None
    if _systemd_service_load_state() != "loaded":
        return None
    if _systemd_service_matches_current_script():
        return None
    if not _systemd_service_active():
        return None
    if runtime_socket_path() != RUNTIME_SYSTEM_SOCKET_PATH:
        return None
    execstart_paths = sorted(_systemd_service_execstart_paths())
    execstart = execstart_paths[0] if execstart_paths else (_systemd_service_execstart() or "<unknown>")
    return (
        "a systemd-managed sensoriumd service is active on the default runtime "
        f"socket, but it does not point at this checkout "
        f"({SENSORIUMD_SCRIPT} vs {execstart}). "
        "Set SENSORIUMD_MANAGED_BY_SYSTEMD=1 to manage that service explicitly, "
        "stop it, or set SENSORIUM_SOCKET_PATH/SENSORIUM_PIDFILE_PATH for an "
        "isolated checkout daemon."
    )


def _assert_no_foreign_systemd_service_conflict():
    message = _foreign_systemd_service_conflict()
    if message is not None:
        raise ModelError(message)


def _run_systemctl(action: str, *, check: bool = True):
    cmd = _privileged_cmd(
        ["systemctl", action, SENSORIUMD_SYSTEMD_UNIT],
        action=f"systemctl {action}",
    )
    return subprocess.run(cmd, check=check, cwd=REPO_ROOT)


def _forwarded_daemon_env() -> list[str]:
    forwarded = []
    for name in (
        "SENSORIUM_STATE_DIR",
        "SENSORIUM_SOCKET_PATH",
        "SENSORIUM_PIDFILE_PATH",
        "SENSORIUM_TRACE_PATH",
        "SENSORIUM_SNAPSHOT_PATH",
        "SENSORIUM_DAEMON_LOG_PATH",
        "SENSORIUM_BENCHMARK_DIR",
    ):
        value = os.environ.get(name)
        if value:
            forwarded.append(f"{name}={value}")
    return forwarded

def apply_model(path: Path) -> None:
    model = normalize_model(path)
    if model["adapter"] == "runtime":
        raise ModelError("runtime models must use 'sensoriumctl runtime apply <model>'")
    _assert_no_foreign_systemd_service_conflict()
    if sensoriumd_running():
        daemon_stop()
    if os.environ.get("SENSORIUM_FORCE_RELOAD", "0") != "1":
        state = read_module_state()
        if model_matches_module_state(model, state) and kernel_build_is_current():
            print(
                f"sensoriumctl: '{model['name']}' is already active; skipping reload",
                file=sys.stderr,
            )
            return
    env = build_apply_env(model)
    subprocess.run([str(RELOAD_SCRIPT)], check=True, cwd=REPO_ROOT, env=env)


def sensoriumd_running() -> bool:
    try:
        rpc_call("status", timeout=1.0)
        return True
    except Exception:
        return False


def _read_pidfile_pid() -> int | None:
    pidfile_path = runtime_pidfile_path()
    try:
        pid_text = pidfile_path.read_text().strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not pid_text:
        return None
    try:
        return int(pid_text)
    except ValueError:
        return None


def _sensoriumd_pids() -> set[int]:
    pids: set[int] = set()
    proc_root = Path("/proc")
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        try:
            raw_cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw_cmdline:
            continue
        argv = [item.decode("utf-8", "ignore") for item in raw_cmdline.split(b"\0") if item]
        if str(SENSORIUMD_SCRIPT) in argv:
            pids.add(pid)
    return pids


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_for_daemon_exit(target_pids: set[int], timeout: float) -> bool:
    pidfile_path = runtime_pidfile_path()
    socket_path = runtime_socket_path()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive_pids = {pid for pid in target_pids if _pid_alive(pid)}
        if not alive_pids and not pidfile_path.exists() and not socket_path.exists():
            return True
        time.sleep(0.1)
    return False


def daemon_start() -> None:
    _assert_no_foreign_systemd_service_conflict()
    socket_path = runtime_socket_path()
    pidfile_path = runtime_pidfile_path()
    if sensoriumd_running():
        return

    if _sensoriumd_pids() or pidfile_path.exists() or socket_path.exists():
        daemon_stop()

    state = read_module_state()
    if not state or state.get("adapter") != "runtime":
        env = os.environ.copy()
        env["SENSORIUM_ADAPTER"] = "runtime"
        env["SENSORIUM_TRANSPORT"] = "virtual"
        env["SENSORIUM_INSTANCE"] = "runtime"
        subprocess.run([str(RELOAD_SCRIPT)], check=True, cwd=REPO_ROOT, env=env)

    if _systemd_management_enabled():
        _run_systemctl("start")
    else:
        cmd = _privileged_cmd(
            [
                "env",
                *_forwarded_daemon_env(),
                f"SENSORIUMD_SOCKET_UID={os.getuid()}",
                f"SENSORIUMD_SOCKET_GID={os.getgid()}",
                sys.executable,
                str(SENSORIUMD_SCRIPT),
                "--daemonize",
                "--socket-path",
                str(socket_path),
                "--pidfile",
                str(pidfile_path),
            ],
            action="starting sensoriumd",
        )
        subprocess.run(cmd, check=True, cwd=REPO_ROOT)

    deadline = time.monotonic() + 10.0
    last_error = "sensoriumd did not start"
    while time.monotonic() < deadline:
        try:
            rpc_call("status", timeout=1.0)
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.25)

    raise ModelError(f"sensoriumd failed to start: {last_error}")


def daemon_stop() -> None:
    _assert_no_foreign_systemd_service_conflict()
    target_pids = _sensoriumd_pids()
    pidfile_pid = _read_pidfile_pid()
    pidfile_path = runtime_pidfile_path()
    socket_path = runtime_socket_path()
    if pidfile_pid is not None:
        target_pids.add(pidfile_pid)

    stopped_via_rpc = False
    stopped_via_systemd = False
    if _systemd_management_enabled():
        _run_systemctl("stop", check=False)
        stopped_via_systemd = True
    elif sensoriumd_running():
        rpc_call("daemon.stop", timeout=2.0)
        stopped_via_rpc = True

    target_pids.update(_sensoriumd_pids())

    if (stopped_via_rpc or stopped_via_systemd) and _wait_for_daemon_exit(
        target_pids,
        timeout=2.0,
    ):
        return

    if target_pids:
        subprocess.run(
            _privileged_cmd(
                ["kill", *[str(pid) for pid in sorted(target_pids)]],
                action="stopping sensoriumd",
            ),
            check=False,
            cwd=REPO_ROOT,
        )

    if _wait_for_daemon_exit(target_pids, timeout=5.0):
        return

    alive_pids = [str(pid) for pid in sorted(target_pids) if _pid_alive(pid)]
    if alive_pids:
        subprocess.run(
            _privileged_cmd(
                ["kill", "-KILL", *alive_pids],
                action="force-stopping sensoriumd",
            ),
            check=False,
            cwd=REPO_ROOT,
        )

    if _wait_for_daemon_exit(target_pids, timeout=2.0):
        return

    if not sensoriumd_running():
        subprocess.run(
            _privileged_cmd(
                ["rm", "-f", str(pidfile_path), str(socket_path)],
                action="cleaning sensoriumd runtime files",
            ),
            check=False,
            cwd=REPO_ROOT,
        )
        return

    if stopped_via_rpc or stopped_via_systemd:
        raise ModelError("sensoriumd did not stop cleanly")
    raise ModelError("stale sensoriumd process still holds runtime bridge")


def daemon_status() -> dict:
    _assert_no_foreign_systemd_service_conflict()
    try:
        return rpc_call("status", timeout=2.0)
    except Exception as exc:
        raise ModelError(f"sensoriumd is not running: {exc}") from exc


def ensure_runtime_ready() -> None:
    _assert_no_foreign_systemd_service_conflict()
    state = read_module_state()
    runtime_module_active = bool(state) and state.get("adapter") == "runtime"

    if sensoriumd_running():
        if runtime_module_active:
            return
        daemon_stop()

    daemon_start()


def runtime_apply(path: Path) -> None:
    model = normalize_model(path)
    if model["adapter"] != "runtime":
        raise ModelError("runtime apply requires a runtime model")
    ensure_runtime_ready()
    rpc_call("runtime.apply", {"model": model}, timeout=20.0)


def runtime_reset() -> None:
    ensure_runtime_ready()
    rpc_call("runtime.reset", timeout=5.0)


def runtime_resync() -> None:
    ensure_runtime_ready()
    rpc_call("runtime.resync", timeout=20.0)


def runtime_status() -> None:
    status = daemon_status()
    print(f"model: {status.get('model') or 'none'}")
    print(f"state: {status.get('state', 'unknown')}")
    print(f"generation: {status.get('generation', 0)}")
    if status.get("desync_reason"):
        print(f"desync_reason: {status['desync_reason']}")
    health = status.get("health", {})
    if health:
        print(f"health: {health.get('status', 'unknown')}")
    print(f"bridge: {status.get('bridge')}")
    bridge_runtime = status.get("bridge_runtime", {})
    if bridge_runtime:
        print(f"bridge_abi: {bridge_runtime.get('bridge_abi')}")
        print(f"session_id: {bridge_runtime.get('session_id')}")
        print(f"inflight_requests: {bridge_runtime.get('inflight')}")
        print(f"late_reply_drops: {bridge_runtime.get('late_replies')}")
        print(
            f"bridge_busy_rejections_generation: {bridge_runtime.get('busy_rejections_generation')}"
        )
        print(f"bridge_busy_rejections_total: {bridge_runtime.get('busy_rejections_total')}")
        print(f"bridge_ebusy_generation: {bridge_runtime.get('ebusy_generation')}")
        print(f"bridge_ebusy_total: {bridge_runtime.get('ebusy_total')}")
    rpc = status.get("rpc", {})
    if rpc:
        print(
            f"rpc_busy_rejections_generation: {rpc.get('busy_rejections_generation')}"
        )
        print(f"rpc_busy_rejections_total: {rpc.get('busy_rejections_total')}")
    queue_depths = status.get("queue_depths", {})
    if queue_depths:
        print(f"queue_depths: {queue_depths}")
    print(f"buses: {status.get('bus_count', 0)}")
    print(f"devices: {status.get('device_count', 0)}")
    print(f"schema_version: {status.get('schema_version')}")
    print(f"snapshot_schema_version: {status.get('snapshot_schema_version')}")
    persistence = status.get("persistence", {})
    if persistence.get("snapshot_path"):
        print(f"snapshot: {persistence['snapshot_path']}")
        print(f"snapshot_restore_enabled: {persistence.get('snapshot_restore_enabled', False)}")
        print(f"snapshot_loaded: {persistence.get('snapshot_loaded', False)}")
        if persistence.get("last_snapshot_saved_ts") is not None:
            print(f"last_snapshot_saved_ts: {persistence['last_snapshot_saved_ts']}")
        if persistence.get("last_snapshot_restored_ts") is not None:
            print(f"last_snapshot_restored_ts: {persistence['last_snapshot_restored_ts']}")
    if persistence.get("trace_path"):
        print(f"trace: {persistence['trace_path']}")
        print(f"trace_limit: {persistence.get('trace_limit', 0)}")
        print(f"trace_loaded: {persistence.get('trace_loaded', 0)}")
        print(f"trace_drop_count: {persistence.get('trace_drop_count', 0)}")


def runtime_health() -> None:
    result = rpc_call("health.get", timeout=5.0)
    print(yaml.safe_dump(result, sort_keys=False).strip())


def runtime_list_buses() -> None:
    result = rpc_call("bus.list", timeout=5.0)
    for bus in result.get("buses", []):
        print(f"{bus['id']}: {bus['transport']} -> {bus['name']}")


def runtime_list_devices() -> None:
    result = rpc_call("device.list", timeout=5.0)
    for device in result.get("devices", []):
        suffix = ""
        if device["transport"] == "i2c":
            suffix = f" addr=0x{device['address']:02x}"
        elif device["transport"] == "spi":
            suffix = f" node={device['device_name']}"
        elif device["transport"] == "uart":
            suffix = f" node={device['port_name']}"
        print(
            f"{device['id']}: {device['transport']} on {device['bus']} "
            f"[{device['backend']['kind']}]"
            f"{suffix}"
        )


def runtime_inspect_device(device_id: str) -> None:
    result = rpc_call("device.get", {"device_id": device_id}, timeout=5.0)
    print(yaml.safe_dump(result.get("device", {}), sort_keys=False).strip())


def runtime_stats() -> None:
    result = rpc_call("stats.get", timeout=5.0)
    print(yaml.safe_dump(result, sort_keys=False).strip())


def runtime_trace(limit: int) -> None:
    result = rpc_call("trace.list", {"limit": limit}, timeout=5.0)
    print(yaml.safe_dump(result, sort_keys=False).strip())


def remove_instance(expected_name: str | None) -> None:
    state = read_module_state()
    if expected_name and state and state.get("instance") and state["instance"] != expected_name:
        raise ModelError(
            f"active instance is '{state['instance']}', not '{expected_name}'"
        )

    result = subprocess.run(
        ["bash", "-lc", "if lsmod | awk '{print $1}' | grep -Fxq sensorium; then "
         "if [[ $EUID -eq 0 ]]; then rmmod sensorium; else sudo rmmod sensorium; fi; fi"],
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise ModelError("failed to remove sensorium module")

def list_state() -> None:
    state = read_module_state()
    print("Available models:")
    for path in iter_model_paths():
        rel = path.relative_to(REPO_ROOT)
        try:
            model = normalize_model(path)
            if model["adapter"] == "runtime":
                print(
                    f"  {rel}  [runtime with "
                    f"{len(model['runtime']['buses'])} buses / "
                    f"{len(model['runtime']['devices'])} devices]"
                )
            else:
                print(f"  {rel}  [{model['adapter']} via {model['transport']}]")
        except ModelError as exc:
            print(f"  {rel}  [invalid: {exc}]")

    print()
    if not state:
        print("Active instance: none")
        return

    print("Active instance:")
    for key in (
        "instance",
        "adapter",
        "transport",
        "transport_device_name",
        "fault_mode",
        "family",
        "sensor",
    ):
        if key in state and state[key]:
            print(f"  {key}: {state[key]}")


def validate(paths: list[str]) -> None:
    if paths:
        targets = [Path(path).resolve() for path in paths]
    else:
        targets = iter_model_paths()

    if not targets:
        raise ModelError("no model files found")

    for path in targets:
        model = normalize_model(path)
        rel = path.relative_to(REPO_ROOT)
        if model["adapter"] == "runtime":
            print(
                f"{rel}: ok [runtime with "
                f"{len(model['runtime']['buses'])} buses / "
                f"{len(model['runtime']['devices'])} devices]"
            )
        else:
            print(f"{rel}: ok [{model['adapter']} via {model['transport']}]")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sensorium model control tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser("apply", help="Apply a model")
    apply_parser.add_argument("model", help="Path to a model YAML file")

    remove_parser = subparsers.add_parser("remove", help="Remove the active instance")
    remove_parser.add_argument("instance", nargs="?", help="Expected active instance name")

    subparsers.add_parser("list", help="List available models and the active instance")

    validate_parser = subparsers.add_parser("validate", help="Validate one or more model files")
    validate_parser.add_argument("models", nargs="*", help="Model file paths")

    daemon_parser = subparsers.add_parser("daemon", help="Manage the sensorium runtime daemon")
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command", required=True)
    daemon_subparsers.add_parser("start", help="Start sensoriumd and load the runtime adapter")
    daemon_subparsers.add_parser("stop", help="Stop sensoriumd")
    daemon_subparsers.add_parser("status", help="Show sensoriumd status")

    runtime_parser = subparsers.add_parser("runtime", help="Manage the live runtime model")
    runtime_subparsers = runtime_parser.add_subparsers(dest="runtime_command", required=True)
    runtime_apply_parser = runtime_subparsers.add_parser("apply", help="Apply a runtime model")
    runtime_apply_parser.add_argument("model", help="Path to a runtime YAML model")
    runtime_subparsers.add_parser("reset", help="Remove all runtime buses and devices")
    runtime_subparsers.add_parser("resync", help="Force a runtime repair/reapply of the current generation")
    runtime_subparsers.add_parser("status", help="Show runtime status")
    runtime_subparsers.add_parser("health", help="Show runtime health")
    runtime_subparsers.add_parser("buses", help="List active runtime buses")
    runtime_subparsers.add_parser("devices", help="List active runtime devices")
    runtime_inspect_parser = runtime_subparsers.add_parser("inspect", help="Show one runtime device")
    runtime_inspect_parser.add_argument("device_id", help="Runtime device id")
    runtime_subparsers.add_parser("stats", help="Show runtime counters")
    runtime_trace_parser = runtime_subparsers.add_parser("trace", help="Show recent runtime events")
    runtime_trace_parser.add_argument("--limit", type=int, default=16, help="Number of recent events")

    args = parser.parse_args()

    try:
        if args.command == "apply":
            apply_model(Path(args.model).resolve())
        elif args.command == "remove":
            remove_instance(args.instance)
        elif args.command == "list":
            list_state()
        elif args.command == "validate":
            validate(args.models)
        elif args.command == "daemon":
            if args.daemon_command == "start":
                daemon_start()
            elif args.daemon_command == "stop":
                daemon_stop()
            elif args.daemon_command == "status":
                status = daemon_status()
                print(yaml.safe_dump(status, sort_keys=False).strip())
        elif args.command == "runtime":
            if args.runtime_command == "apply":
                runtime_apply(Path(args.model).resolve())
            elif args.runtime_command == "reset":
                runtime_reset()
            elif args.runtime_command == "resync":
                runtime_resync()
            elif args.runtime_command == "status":
                runtime_status()
            elif args.runtime_command == "health":
                runtime_health()
            elif args.runtime_command == "buses":
                runtime_list_buses()
            elif args.runtime_command == "devices":
                runtime_list_devices()
            elif args.runtime_command == "inspect":
                runtime_inspect_device(args.device_id)
            elif args.runtime_command == "stats":
                runtime_stats()
            elif args.runtime_command == "trace":
                runtime_trace(args.limit)
    except ModelError as exc:
        print(f"sensoriumctl: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"sensoriumctl: command failed with exit code {exc.returncode}", file=sys.stderr)
        return exc.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
