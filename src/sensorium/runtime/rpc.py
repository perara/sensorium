#!/usr/bin/env python3
from __future__ import annotations

import collections
import errno
import json
import os
import selectors
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from sensorium.runtime.daemon_support import ensure_parent


DEFAULT_DAEMON_LOG_LIMIT_BYTES = 1024 * 1024


def _busy_response():
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32001, "message": "sensoriumd RPC capacity exceeded"},
    }


def _error_response(request, message: str):
    return {
        "jsonrpc": "2.0",
        "id": request.get("id") if isinstance(request, dict) else None,
        "error": {"code": -32000, "message": message},
    }


@dataclass
class _ClientState:
    sock: socket.socket
    inbuf: bytearray = field(default_factory=bytearray)
    outbuf: bytearray = field(default_factory=bytearray)
    pending_requests: collections.deque = field(default_factory=collections.deque)
    active_request: bool = False
    waiting_for_event: dict | None = None
    closed: bool = False


class _BoundedLogWriter:
    def __init__(self, path: Path, *, limit_bytes: int = DEFAULT_DAEMON_LOG_LIMIT_BYTES):
        self.path = Path(path)
        self.limit_bytes = max(4096, int(limit_bytes))
        ensure_parent(self.path)
        self._stream = open(self.path, "a", encoding="utf-8", buffering=1)
        self.encoding = self._stream.encoding
        self._lock = threading.Lock()
        self._trim_locked()

    def _trim_locked(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size <= self.limit_bytes * 2:
            return
        keep = self.limit_bytes
        with open(self.path, "rb") as source:
            source.seek(max(0, size - keep))
            tail = source.read()
        self._stream.close()
        with open(self.path, "wb") as target:
            target.write(tail)
        self._stream = open(self.path, "a", encoding="utf-8", buffering=1)

    def write(self, data):
        if not data:
            return 0
        with self._lock:
            written = self._stream.write(data)
            self._stream.flush()
            self._trim_locked()
            return written

    def flush(self):
        with self._lock:
            self._stream.flush()

    def isatty(self):
        return False

    def fileno(self):
        return self._stream.fileno()

    def close(self):
        with self._lock:
            self._stream.close()


def configure_bounded_stdio(log_path: Path, *, limit_bytes: int = DEFAULT_DAEMON_LOG_LIMIT_BYTES):
    writer = _BoundedLogWriter(log_path, limit_bytes=limit_bytes)
    sys.stdout = writer
    sys.stderr = writer
    return writer


def prepare_socket_path(socket_path: Path):
    ensure_parent(socket_path)
    if not socket_path.exists():
        return
    try:
        mode = socket_path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f"refusing to remove non-socket path: {socket_path}")

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.2)
    try:
        probe.connect(str(socket_path))
    except OSError as exc:
        if exc.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
            raise RuntimeError(
                f"refusing to replace socket {socket_path}: {exc}"
            ) from exc
    else:
        raise RuntimeError(f"refusing to replace active socket: {socket_path}")
    finally:
        probe.close()

    socket_path.unlink()


class RuntimeRpcServer:
    allow_reuse_address = True

    def __init__(
        self,
        socket_path: Path,
        dispatch_fn,
        *,
        max_workers: int = 8,
        max_clients: int = 32,
        max_request_bytes: int = 1024 * 1024,
        max_pending_requests_per_client: int = 32,
        next_event_fn=None,
        note_backend_poll_fn=None,
    ):
        self.socket_path = Path(socket_path)
        self._dispatch_fn = dispatch_fn
        self._next_event_fn = next_event_fn
        self._note_backend_poll_fn = note_backend_poll_fn
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="sensoriumd-rpc",
        )
        self._max_workers = max(1, int(max_workers))
        self._max_clients = max(1, int(max_clients))
        self._max_request_bytes = max(256, int(max_request_bytes))
        self._max_pending_requests_per_client = max(1, int(max_pending_requests_per_client))
        self._worker_slots = threading.BoundedSemaphore(self._max_workers)
        self._selector = selectors.DefaultSelector()
        self._stop = threading.Event()
        self._completions = collections.deque()
        self._completion_lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._active_clients = 0
        self._busy_rejections_generation = 0
        self._busy_rejections_total = 0
        self._wake_read, self._wake_write = socket.socketpair()
        self._wake_read.setblocking(False)
        self._wake_write.setblocking(False)
        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.setblocking(False)
        self._server_socket.bind(str(self.socket_path))
        self._server_socket.listen()
        self._selector.register(self._server_socket, selectors.EVENT_READ, self._accept_client)
        self._selector.register(self._wake_read, selectors.EVENT_READ, self._drain_wake)

    def fileno(self):
        return self._server_socket.fileno()

    def rpc_metrics(self):
        with self._client_lock:
            return {
                "active_clients": self._active_clients,
                "max_clients": self._max_clients,
                "busy_rejections_generation": self._busy_rejections_generation,
                "busy_rejections_total": self._busy_rejections_total,
            }

    def reset_generation_metrics(self):
        with self._client_lock:
            self._busy_rejections_generation = 0

    def dispatch(self, request):
        return self._dispatch_fn(request)

    def serve_forever(self):
        while not self._stop.is_set():
            timeout = self._next_timeout()
            try:
                events = self._selector.select(timeout)
            except (OSError, ValueError):
                if self._stop.is_set():
                    break
                raise
            for key, mask in events:
                if callable(key.data):
                    key.data(key.fileobj, mask)
                else:
                    self._service_client(key.fileobj, mask)
            self._drain_completions()
            self._service_event_waiters()

    def handle_request(self):
        try:
            events = self._selector.select(timeout=0.5)
        except (OSError, ValueError):
            if self._stop.is_set():
                return
            raise
        for key, mask in events:
            if callable(key.data):
                key.data(key.fileobj, mask)
            else:
                self._service_client(key.fileobj, mask)
        self._drain_completions()
        self._service_event_waiters()

    def shutdown(self):
        self._stop.set()
        self._wake()

    def server_close(self):
        self.shutdown()
        try:
            selector_map = self._selector.get_map()
        except (OSError, ValueError, AttributeError):
            selector_map = None
        if selector_map is not None:
            for key in list(selector_map.values()):
                state = key.data if isinstance(key.data, _ClientState) else None
                if state is not None:
                    self._close_client(state)
        try:
            self._selector.unregister(self._server_socket)
        except Exception:
            pass
        try:
            self._selector.unregister(self._wake_read)
        except Exception:
            pass
        try:
            self._server_socket.close()
        except Exception:
            pass
        try:
            self._wake_read.close()
            self._wake_write.close()
        except Exception:
            pass
        self._selector.close()
        self._executor.shutdown(wait=True, cancel_futures=False)

    def notify_backend_event(self, backend_id: str | None = None):
        self._wake()

    def _wake(self):
        try:
            self._wake_write.send(b"x")
        except OSError:
            pass

    def _drain_wake(self, sock, mask):
        try:
            while sock.recv(4096):
                pass
        except BlockingIOError:
            return
        except OSError:
            return

    def _next_timeout(self):
        deadline = None
        try:
            selector_map = self._selector.get_map()
        except (OSError, ValueError, AttributeError):
            return 0.5
        if selector_map is None:
            return 0.5
        for key in selector_map.values():
            state = key.data if isinstance(key.data, _ClientState) else None
            if state and state.waiting_for_event:
                waiter_deadline = state.waiting_for_event["deadline"]
                if deadline is None or waiter_deadline < deadline:
                    deadline = waiter_deadline
        if deadline is None:
            return 0.5
        return max(0.0, min(0.5, deadline - time.monotonic()))

    def _accept_client(self, server_sock, mask):
        try:
            client_sock, _ = server_sock.accept()
        except OSError:
            return

        with self._client_lock:
            if self._active_clients >= self._max_clients:
                self._busy_rejections_generation += 1
                self._busy_rejections_total += 1
                try:
                    client_sock.sendall((json.dumps(_busy_response()) + "\n").encode("utf-8"))
                finally:
                    client_sock.close()
                return
            self._active_clients += 1

        client_sock.setblocking(False)
        state = _ClientState(client_sock)
        self._selector.register(client_sock, selectors.EVENT_READ, state)

    def _close_client(self, state: _ClientState):
        if state.closed:
            return
        state.closed = True
        try:
            self._selector.unregister(state.sock)
        except Exception:
            pass
        try:
            state.sock.close()
        except Exception:
            pass
        with self._client_lock:
            if self._active_clients > 0:
                self._active_clients -= 1

    def _queue_response(self, state: _ClientState, response):
        if state.closed:
            return
        state.outbuf.extend((json.dumps(response) + "\n").encode("utf-8"))
        self._update_interest(state)

    def _send_error_and_close(self, state: _ClientState, message: str):
        if state.closed:
            return
        try:
            state.sock.sendall(
                (json.dumps(_error_response(None, message)) + "\n").encode("utf-8")
            )
        except OSError:
            pass
        self._close_client(state)

    def _update_interest(self, state: _ClientState):
        if state.closed:
            return
        events = selectors.EVENT_READ
        if state.outbuf:
            events |= selectors.EVENT_WRITE
        try:
            self._selector.modify(state.sock, events, state)
        except Exception:
            self._close_client(state)

    def _service_client(self, sock, mask):
        key = self._selector.get_key(sock)
        state: _ClientState = key.data
        if mask & selectors.EVENT_READ:
            try:
                chunk = sock.recv(65536)
            except BlockingIOError:
                chunk = None
            except OSError:
                self._close_client(state)
                return
            if chunk == b"":
                self._close_client(state)
                return
            if chunk:
                state.inbuf.extend(chunk)
                if b"\n" not in state.inbuf and len(state.inbuf) > self._max_request_bytes:
                    self._send_error_and_close(
                        state,
                        f"RPC request exceeded maximum size of {self._max_request_bytes} bytes",
                    )
                    return
                while b"\n" in state.inbuf:
                    line, _, remainder = state.inbuf.partition(b"\n")
                    state.inbuf = bytearray(remainder)
                    if not line.strip():
                        continue
                    if len(line) > self._max_request_bytes:
                        self._send_error_and_close(
                            state,
                            f"RPC request exceeded maximum size of {self._max_request_bytes} bytes",
                        )
                        return
                    try:
                        request = json.loads(line.decode("utf-8"))
                    except Exception as exc:
                        self._queue_response(state, _error_response(None, str(exc)))
                        continue
                    if len(state.pending_requests) >= self._max_pending_requests_per_client:
                        self._send_error_and_close(
                            state,
                            "RPC client exceeded maximum queued request count",
                        )
                        return
                    state.pending_requests.append(request)
                self._dispatch_pending(state)
        if mask & selectors.EVENT_WRITE:
            self._flush_writes(state)

    def _flush_writes(self, state: _ClientState):
        if state.closed or not state.outbuf:
            self._update_interest(state)
            return
        try:
            written = state.sock.send(state.outbuf)
        except BlockingIOError:
            return
        except OSError:
            self._close_client(state)
            return
        del state.outbuf[:written]
        self._update_interest(state)

    def _dispatch_pending(self, state: _ClientState):
        while (
            not state.closed
            and not state.active_request
            and state.waiting_for_event is None
            and state.pending_requests
        ):
            request = state.pending_requests.popleft()
            if (
                isinstance(request, dict)
                and request.get("method") == "backend.next_event"
                and self._next_event_fn is not None
            ):
                self._begin_next_event_wait(state, request)
                return
            if not self._worker_slots.acquire(blocking=False):
                with self._client_lock:
                    self._busy_rejections_generation += 1
                    self._busy_rejections_total += 1
                self._queue_response(state, _busy_response())
                continue
            state.active_request = True
            self._executor.submit(self._run_request, state, request)

    def _run_request(self, state: _ClientState, request):
        try:
            response = self.dispatch(request)
        except Exception as exc:
            response = _error_response(request, str(exc))
        finally:
            self._worker_slots.release()
        with self._completion_lock:
            self._completions.append((state, response))
        self._wake()

    def _drain_completions(self):
        while True:
            with self._completion_lock:
                if not self._completions:
                    return
                state, response = self._completions.popleft()
            if state.closed:
                continue
            state.active_request = False
            self._queue_response(state, response)
            self._dispatch_pending(state)

    def _begin_next_event_wait(self, state: _ClientState, request):
        params = request.get("params", {}) if isinstance(request, dict) else {}
        backend_id = params.get("backend_id")
        timeout = float(params.get("timeout", 30.0))
        if self._note_backend_poll_fn is not None:
            self._note_backend_poll_fn(backend_id)
        event = self._next_event_fn(backend_id)
        if event is not None:
            self._queue_response(
                state,
                {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": {"event": event},
                },
            )
            return
        state.waiting_for_event = {
            "request": request,
            "backend_id": backend_id,
            "deadline": time.monotonic() + max(0.0, timeout),
        }

    def _service_event_waiters(self):
        now = time.monotonic()
        try:
            selector_map = self._selector.get_map()
        except (OSError, ValueError, AttributeError):
            return
        if selector_map is None:
            return
        for key in list(selector_map.values()):
            state = key.data if isinstance(key.data, _ClientState) else None
            if not state or state.closed or state.waiting_for_event is None:
                continue
            waiter = state.waiting_for_event
            event = self._next_event_fn(waiter["backend_id"])
            if event is not None or waiter["deadline"] <= now:
                state.waiting_for_event = None
                self._queue_response(
                    state,
                    {
                        "jsonrpc": "2.0",
                        "id": waiter["request"].get("id"),
                        "result": {"event": event},
                    },
                )
                self._dispatch_pending(state)


def dispatch_runtime_request(manager, request):
    method = request.get("method")
    params = request.get("params", {})
    result = None

    if method == "status":
        result = manager.status()
    elif method == "health.get":
        result = manager.health()
    elif method == "runtime.apply":
        manager.apply_model(params["model"])
        result = manager.status()
    elif method == "runtime.reset":
        manager.reset_runtime()
        result = manager.status()
    elif method == "runtime.resync":
        result = manager.resync_runtime()
    elif method == "bus.list":
        result = {"buses": list(manager.buses.values())}
    elif method == "device.list":
        result = {"devices": [manager._device_view(device) for device in manager.devices.values()]}
    elif method == "device.get":
        result = {"device": manager.get_device(params["device_id"])}
    elif method == "device.update":
        result = {"device": manager.update_device(params["device_id"], params.get("patch", {}))}
    elif method == "stats.get":
        result = manager.get_stats()
    elif method == "trace.list":
        result = manager.get_trace(int(params.get("limit", 32)))
    elif method == "backend.list":
        result = manager.list_backends()
    elif method == "backend.attach":
        result = manager.attach_backend(params["backend_id"], params["device_ids"])
    elif method == "backend.detach":
        result = manager.detach_backend(params["backend_id"], params.get("device_ids"))
    elif method == "backend.next_event":
        timeout = float(params.get("timeout", 30))
        result = {"event": manager.next_event(params["backend_id"], timeout)}
    elif method == "backend.reply":
        result = manager.reply_event(
            params["backend_id"],
            int(params["request_id"]),
            int(params.get("status", 0)),
            params.get("data", ""),
        )
    elif method == "backend.heartbeat":
        result = manager.heartbeat_backend(params["backend_id"])
    elif method == "uart.inject_rx":
        result = manager.inject_uart_rx(params["device_id"], params["data"])
    elif method == "uart.control_set":
        result = manager.set_uart_modem(params["device_id"], params["signals"])
    elif method == "daemon.stop":
        threading.Thread(target=manager.stop_event.set, daemon=True).start()
        result = {"ok": True}
    else:
        raise RuntimeError(f"unknown method: {method}")

    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": result,
    }


def install_signal_handlers(stop_event):
    def handler(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def daemonize_runtime(script_path: Path, repo_root: Path, args):
    cmd = [
        sys.executable,
        str(script_path.resolve()),
        "--foreground",
        "--socket-path",
        str(args.socket_path),
        "--pidfile",
        str(args.pidfile),
        "--bridge",
        str(args.bridge),
        "--bridge-workers",
        str(args.bridge_workers),
        "--bridge-queue-depth",
        str(args.bridge_queue_depth),
        "--rpc-workers",
        str(args.rpc_workers),
        "--rpc-max-clients",
        str(args.rpc_max_clients),
        "--trace-limit",
        str(args.trace_limit),
        "--trace-file-limit-bytes",
        str(args.trace_file_limit_bytes),
        "--trace-queue-limit",
        str(args.trace_queue_limit),
        "--trace-queue-limit-bytes",
        str(args.trace_queue_limit_bytes),
    ]
    if args.controller_timeout_ms is not None:
        cmd.extend(["--controller-timeout-ms", str(args.controller_timeout_ms)])
    if args.trace_path is not None:
        cmd.extend(["--trace-path", str(args.trace_path)])
    if args.snapshot_path is not None:
        cmd.extend(["--snapshot-path", str(args.snapshot_path)])
    if args.no_restore_snapshot:
        cmd.append("--no-restore-snapshot")
    if getattr(args, "daemon_log_path", None) is not None:
        cmd.extend(["--daemon-log-path", str(args.daemon_log_path)])
    subprocess.Popen(
        cmd,
        cwd=repo_root,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def serve_runtime_daemon(args, manager, dispatch_fn):
    prepare_socket_path(args.socket_path)
    ensure_parent(args.pidfile)

    manager.start()
    install_signal_handlers(manager.stop_event)

    server = RuntimeRpcServer(
        args.socket_path,
        dispatch_fn,
        max_workers=args.rpc_workers,
        max_clients=args.rpc_max_clients,
        next_event_fn=manager.poll_next_event,
        note_backend_poll_fn=manager.note_backend_poll,
    )
    manager.rpc_server = server

    socket_uid = os.environ.get("SENSORIUMD_SOCKET_UID")
    socket_gid = os.environ.get("SENSORIUMD_SOCKET_GID")
    if socket_uid and socket_gid:
        os.chown(args.socket_path, int(socket_uid), int(socket_gid))
    os.chmod(args.socket_path, 0o660)

    args.pidfile.write_text(f"{os.getpid()}\n")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        manager.shutdown()
        try:
            args.pidfile.unlink()
        except FileNotFoundError:
            pass
        try:
            args.socket_path.unlink()
        except FileNotFoundError:
            pass
