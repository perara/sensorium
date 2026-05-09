#!/usr/bin/env python3
from __future__ import annotations

import errno
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from sensorium.runtime.common import REPO_ROOT
from sensorium.runtime.daemon_support import ensure_parent
from sensorium.runtime.paths import runtime_state_root
from sensorium.runtime.worker_protocol import recv_worker_message, send_worker_message

MANAGED_WORKER_LOG_LIMIT_BYTES = 256 * 1024


class RuntimeManagedWorkerMixin:
    def _managed_worker_log_root(self) -> Path:
        return Path(getattr(self, "state_root", runtime_state_root()))

    def _managed_worker_backend_id(self, device_id: str):
        return f"managed:{device_id}"

    def _managed_worker_config(self, device: dict):
        if not device:
            return None
        backend = device.get("backend", {})
        if backend.get("kind") != "controller":
            return None
        worker = backend.get("worker")
        return worker if isinstance(worker, dict) else None

    def _resolve_worker_command(self, command):
        resolved = []
        for index, item in enumerate(command):
            path = Path(item)
            if path.is_absolute():
                resolved.append(str(path))
                continue
            if index > 0 and ("/" in item or item.endswith(".py")):
                candidate = REPO_ROOT / item
                if candidate.exists():
                    resolved.append(str(candidate))
                    continue
            resolved.append(item)
        return resolved

    def _refresh_managed_worker_runtime_state_locked(self):
        if self.runtime_state == "desynced":
            return
        reasons = [
            f"{device_id}:{device.get('degraded_reason')}"
            for device_id, device in sorted(self.devices.items())
            if device.get("degraded_reason")
        ]
        if reasons:
            self._set_runtime_state_locked(
                "degraded", reason=f"managed workers: {', '.join(reasons)}"
            )
        elif self.runtime_state == "degraded" and (self.desync_reason or "").startswith(
            "managed workers:"
        ):
            self._set_runtime_state_locked("ready")

    def _complete_pending_controller(self, request_id: int, status: int, data: bytes):
        with self.lock:
            pending = self.pending_controller.get(request_id)
            if pending is None:
                self._late_reply()
                return False
        with pending.cond:
            pending.status = status
            pending.data = data
            pending.done = True
            pending.cond.notify_all()
        return True

    def _fail_managed_pending_locked(self, device_id: str, status: int):
        targets = [
            request_id
            for request_id, pending in self.pending_controller.items()
            if pending.event.get("device_id") == device_id
        ]
        for request_id in targets:
            pending = self.pending_controller.pop(request_id, None)
            if pending is None:
                continue
            with pending.cond:
                pending.status = status
                pending.data = b""
                pending.done = True
                pending.cond.notify_all()

    def _update_managed_worker_status_locked(
        self,
        device_id: str,
        *,
        status: str,
        pid: int | None = None,
        restart_count: int = 0,
        last_heartbeat_ts=None,
        log_path: str | None = None,
    ):
        device = self.devices.get(device_id)
        if device is None:
            return
        current = device.get("managed_worker", {})
        device["managed_worker"] = {
            "backend_id": self._managed_worker_backend_id(device_id),
            "status": status,
            "pid": pid,
            "restart_count": restart_count,
            "last_heartbeat_ts": last_heartbeat_ts,
            "log_path": log_path or current.get("log_path"),
        }

    def _managed_worker_log_path(self, device_id: str) -> Path:
        return self._managed_worker_log_root() / "sensoriumd-workers" / f"{device_id}.log"

    def _open_managed_worker_log(self, device_id: str):
        log_path = self._managed_worker_log_path(device_id)
        ensure_parent(log_path)
        return str(log_path)

    def _trim_managed_worker_log_handle(self, handle):
        try:
            size = handle.tell()
        except OSError:
            return
        if size <= MANAGED_WORKER_LOG_LIMIT_BYTES:
            return
        keep = MANAGED_WORKER_LOG_LIMIT_BYTES
        try:
            handle.seek(max(0, size - keep))
            tail = handle.read()
            handle.seek(0)
            handle.truncate()
            handle.write(tail)
            handle.flush()
            handle.seek(0, os.SEEK_END)
        except OSError:
            return

    def _managed_worker_log_pump(self, log_path: str, stream):
        path = Path(log_path)
        ensure_parent(path)
        mode = "r+b" if path.exists() else "w+b"
        output = open(path, mode, buffering=0)
        output.seek(0, os.SEEK_END)
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                output.write(chunk)
                output.flush()
                self._trim_managed_worker_log_handle(output)
        finally:
            try:
                output.close()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def _record_managed_worker_failure_locked(self, device_id: str, message: str):
        self.bridge_stats["worker_restarts"] += 1
        self.bridge_stats["last_worker_failure"] = {
            "device_id": device_id,
            "message": message,
            "ts": round(time.time(), 6),
        }
        device = self.devices.get(device_id)
        if device is not None:
            device["degraded_reason"] = message
        self._refresh_managed_worker_runtime_state_locked()

    def _force_managed_worker_restart_locked(self, device_id: str, state: dict, message: str):
        current = self.managed_workers.get(device_id)
        if current is not state:
            return
        state["failure_reason"] = message
        try:
            state["sock"].close()
        except Exception:
            pass
        try:
            state["proc"].kill()
        except Exception:
            pass

    def _managed_worker_reader_loop(self, device_id: str, state: dict):
        sock = state["sock"]
        while True:
            try:
                message = recv_worker_message(sock)
            except Exception:
                break
            if message is None:
                break
            msg_type = message.get("type")
            if msg_type == "reply":
                request_id = int(message.get("request_id", 0))
                status = int(message.get("status", -errno.EIO))
                data_hex = message.get("data", "")
                self._complete_pending_controller(
                    request_id,
                    status,
                    bytes.fromhex(data_hex) if data_hex else b"",
                )
            elif msg_type == "heartbeat":
                with self.lock:
                    current = self.managed_workers.get(device_id)
                    if current is state:
                        self._update_managed_worker_status_locked(
                            device_id,
                            status="running",
                            pid=state["proc"].pid,
                            restart_count=state["restart_count"],
                            last_heartbeat_ts=round(time.time(), 6),
                            log_path=state.get("log_path"),
                        )

    def _managed_worker_monitor_loop(self, device_id: str, state: dict):
        proc = state["proc"]
        exit_code = proc.wait()
        try:
            state["sock"].close()
        except Exception:
            pass
        with self.lock:
            current = self.managed_workers.get(device_id)
            if current is not state:
                return
            self.managed_workers.pop(device_id, None)
            if state["stop_requested"] or self.stop_event.is_set():
                self._update_managed_worker_status_locked(
                    device_id,
                    status="stopped",
                    pid=None,
                    restart_count=state["restart_count"],
                    log_path=state.get("log_path"),
                )
                device = self.devices.get(device_id)
                if device is not None:
                    device["degraded_reason"] = None
                self._refresh_managed_worker_runtime_state_locked()
                return

            message = state.pop("failure_reason", None) or f"worker exited with code {exit_code}"
            self._record_managed_worker_failure_locked(device_id, message)
            self._update_managed_worker_status_locked(
                device_id,
                status="restarting",
                pid=None,
                restart_count=state["restart_count"] + 1,
                log_path=state.get("log_path"),
            )
            self._fail_managed_pending_locked(device_id, -errno.EPIPE)
            worker_config = self._managed_worker_config(self.devices.get(device_id))
            restart_limit = state["restart_limit"]
            restart_count = state["restart_count"] + 1
            restart_backoff = state["restart_backoff_ms"]

        if (
            worker_config is None
            or restart_count > restart_limit
            or self.stop_event.is_set()
        ):
            with self.lock:
                self._update_managed_worker_status_locked(
                    device_id,
                    status="failed",
                    pid=None,
                    restart_count=restart_count,
                    log_path=state.get("log_path"),
                )
                self._refresh_managed_worker_runtime_state_locked()
            return

        time.sleep(restart_backoff / 1000.0)
        with self.lock:
            if self.stop_event.is_set() or device_id not in self.devices:
                return
            self._launch_managed_worker_locked(device_id, restart_count=restart_count)

    def _launch_managed_worker_locked(self, device_id: str, *, restart_count: int = 0):
        device = self.devices.get(device_id)
        worker = self._managed_worker_config(device)
        if device is None or worker is None:
            return
        self._stop_managed_worker_locked(device_id)

        parent_sock, child_sock = socket.socketpair()
        parent_sock.setblocking(True)
        child_sock.setblocking(True)
        command = self._resolve_worker_command(worker["command"])
        cwd = Path(worker.get("cwd") or REPO_ROOT)
        if not cwd.is_absolute():
            cwd = REPO_ROOT / cwd
        env = os.environ.copy()
        env.update(worker.get("env", {}))
        env["SENSORIUM_WORKER_FD"] = str(child_sock.fileno())
        env["SENSORIUM_WORKER_DEVICE_ID"] = device_id
        env["SENSORIUM_WORKER_BACKEND_ID"] = self._managed_worker_backend_id(device_id)
        env["SENSORIUM_RUNTIME_SOCKET_PATH"] = str(getattr(self.rpc_server, "socket_path", ""))
        log_path = self._open_managed_worker_log(device_id)
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            pass_fds=(child_sock.fileno(),),
            close_fds=True,
            bufsize=0,
        )
        child_sock.close()
        state = {
            "proc": proc,
            "sock": parent_sock,
            "send_lock": threading.Lock(),
            "stop_requested": False,
            "restart_limit": int(worker.get("restart_limit", 3)),
            "restart_backoff_ms": int(worker.get("restart_backoff_ms", 250)),
            "restart_count": restart_count,
            "log_path": log_path,
        }
        self.managed_workers[device_id] = state
        device["degraded_reason"] = None
        self._update_managed_worker_status_locked(
            device_id,
            status="running",
            pid=proc.pid,
            restart_count=restart_count,
            log_path=log_path,
        )
        self._refresh_managed_worker_runtime_state_locked()
        threading.Thread(
            target=self._managed_worker_log_pump,
            args=(log_path, proc.stdout),
            name=f"sensoriumd-managed-log-{device_id}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._managed_worker_reader_loop,
            args=(device_id, state),
            name=f"sensoriumd-managed-reader-{device_id}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._managed_worker_monitor_loop,
            args=(device_id, state),
            name=f"sensoriumd-managed-monitor-{device_id}",
            daemon=True,
        ).start()

    def _start_managed_workers_locked(self):
        for device_id, device in sorted(self.devices.items()):
            if self._managed_worker_config(device):
                try:
                    self._launch_managed_worker_locked(device_id)
                except Exception as exc:
                    self._record_managed_worker_failure_locked(
                        device_id, f"worker start failed: {exc}"
                    )
                    self._update_managed_worker_status_locked(
                        device_id,
                        status="failed",
                        pid=None,
                        restart_count=0,
                        log_path=self._managed_worker_log_path(device_id).as_posix(),
                    )

    def _stop_managed_worker_locked(self, device_id: str):
        state = self.managed_workers.pop(device_id, None)
        if state is None:
            return
        state["stop_requested"] = True
        try:
            with state["send_lock"]:
                send_worker_message(state["sock"], {"type": "shutdown"})
        except Exception:
            pass
        try:
            state["sock"].close()
        except Exception:
            pass
        proc = state["proc"]
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)

    def _stop_all_managed_workers_locked(self):
        for device_id in list(self.managed_workers.keys()):
            self._stop_managed_worker_locked(device_id)

    def _dispatch_managed_controller(self, device: dict, event: dict):
        device_id = device["id"]
        with self.lock:
            state = self.managed_workers.get(device_id)
            if state is None:
                return -errno.EPIPE, b""
            pending = self.pending_controller[event["request_id"]]
            payload = {
                "type": "event",
                "event": {
                    **event,
                    "backend_id": self._managed_worker_backend_id(device_id),
                },
            }
        try:
            with state["send_lock"]:
                send_worker_message(state["sock"], payload)
        except Exception:
            with self.lock:
                self._force_managed_worker_restart_locked(
                    device_id, state, "worker event send failed"
                )
                self._fail_managed_pending_locked(device_id, -errno.EPIPE)
            return -errno.EPIPE, b""

        deadline = time.monotonic() + (self.controller_timeout_ms / 1000.0)
        with pending.cond:
            while not pending.done:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                pending.cond.wait(timeout=remaining)
        with self.lock:
            self.pending_controller.pop(event["request_id"], None)
        if not pending.done:
            return -errno.ETIMEDOUT, b""
        return pending.status, pending.data
