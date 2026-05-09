#!/usr/bin/env python3
from __future__ import annotations

import collections
import errno
import queue
import threading
import time

from sensorium.runtime.daemon_support import (
    CMD_REPLY,
    ControllerPending,
    I2C_REQ_PREFIX_STRUCT,
    REQ_I2C_XFER,
    REQ_SPI_XFER,
    REQ_UART_CFG,
    REQ_UART_CTRL,
    REQ_UART_TX,
    REPLY_PREFIX_STRUCT,
    SPI_REQ_PREFIX_STRUCT,
    UART_CFG_STRUCT,
    UART_REQ_PREFIX_STRUCT,
)
from sensorium.runtime.bridge_protocol import REQUIRED_FEATURES


class RuntimeBridgeMixin:
    def _set_desynced_from_bridge_failure(self, reason: str):
        with self.lock:
            if self.stop_event.is_set():
                return
            self._set_runtime_state_locked("desynced", reason=reason)

    def _route_key_for_device(self, device: dict):
        if device["backend"]["kind"] == "controller" and device["backend"].get("worker"):
            return ("managed-worker", device["id"])
        if device["backend"]["kind"] == "controller" and device.get("attached_backend"):
            return ("backend", device["attached_backend"])
        return ("device", device["id"])

    def _bridge_request_started(self):
        with self.lock:
            self.bridge_stats["inflight"] += 1
            self.bridge_stats["inflight_max"] = max(
                self.bridge_stats["inflight_max"], self.bridge_stats["inflight"]
            )

    def _bridge_request_finished(self, latency_ms: float, *, bridge_error: bool = False):
        with self.lock:
            if self.bridge_stats["inflight"] > 0:
                self.bridge_stats["inflight"] -= 1
            self.bridge_stats["completed"] += 1
            completed = self.bridge_stats["completed"]
            current_avg = self.bridge_stats["latency_ms_avg"]
            self.bridge_stats["latency_ms_avg"] = round(
                ((current_avg * (completed - 1)) + latency_ms) / completed, 3
            )
            self.bridge_stats["latency_ms_max"] = round(
                max(self.bridge_stats["latency_ms_max"], latency_ms), 3
            )
            if bridge_error:
                self.bridge_stats["bridge_errors"] += 1

    def _bridge_busy_rejection(self):
        with self.lock:
            self.bridge_stats["busy_rejections_generation"] += 1
            self.bridge_stats["busy_rejections_total"] += 1

    def _note_queue_depth(self):
        depth = self.request_queue.qsize()
        with self.lock:
            self.bridge_stats["queue_depth"] = depth
            self.bridge_stats["queue_depth_max"] = max(
                self.bridge_stats["queue_depth_max"], depth
            )

    def _late_reply(self):
        with self.lock:
            self.bridge_stats["late_replies"] += 1

    def _bridge_reply_overloaded(self, message_id: int, generation: int):
        reply_payload = REPLY_PREFIX_STRUCT.pack(-errno.EBUSY, 0)
        with self.bridge_write_lock:
            self.bridge.write_frame(
                CMD_REPLY,
                message_id,
                reply_payload,
                generation=generation,
            )
        self._bridge_busy_rejection()

    def _device_handle_for_request(self, msg_type: int, payload: bytes):
        if msg_type == REQ_I2C_XFER and len(payload) >= I2C_REQ_PREFIX_STRUCT.size:
            return I2C_REQ_PREFIX_STRUCT.unpack_from(payload, 0)[0]
        if msg_type == REQ_SPI_XFER and len(payload) >= SPI_REQ_PREFIX_STRUCT.size:
            return SPI_REQ_PREFIX_STRUCT.unpack_from(payload, 0)[0]
        if msg_type in {REQ_UART_TX, REQ_UART_CTRL} and len(payload) >= UART_REQ_PREFIX_STRUCT.size:
            return UART_REQ_PREFIX_STRUCT.unpack_from(payload, 0)[0]
        if msg_type == REQ_UART_CFG and len(payload) >= UART_CFG_STRUCT.size:
            return UART_CFG_STRUCT.unpack_from(payload, 0)[0]
        return None

    def _request_route_lock(self, msg_type: int, payload: bytes):
        device_handle = self._device_handle_for_request(msg_type, payload)
        if device_handle is None:
            return None
        device = self._device_by_handle(device_handle)
        if device is None:
            return None
        return device.get("_route_lock")

    def _route_lock_for(self, route_key):
        if route_key is None:
            return None
        with self.route_lock:
            lock = self.route_locks.get(route_key)
            if lock is None:
                lock = threading.Lock()
                self.route_locks[route_key] = lock
            return lock

    def _refresh_device_route_locked(self, device: dict):
        route_key = self._route_key_for_device(device)
        device["_route_key"] = route_key
        device["_route_lock"] = self._route_lock_for(route_key)

    def start(self):
        self.trace_writer.start()
        try:
            self.bridge.open()
            negotiated = self.bridge.negotiate()
            negotiated_features = int(negotiated.get("features", 0))
            if (negotiated_features & REQUIRED_FEATURES) != REQUIRED_FEATURES:
                missing = REQUIRED_FEATURES & ~negotiated_features
                raise RuntimeError(
                    f"runtime bridge missing required ABI v5 feature bits: 0x{missing:x}"
                )
            self.stop_event.clear()
            with self.lock:
                self.generation = max(self.generation, int(negotiated.get("generation", 0)))
                self.bridge_stats["session_id"] = negotiated.get("session_id")
            for index in range(self.worker_count):
                worker = threading.Thread(
                    target=self._bridge_worker_loop,
                    name=f"sensoriumd-bridge-worker-{index}",
                    daemon=True,
                )
                self.bridge_workers.append(worker)
                worker.start()
            self.bridge_thread = threading.Thread(target=self._bridge_loop, daemon=True)
            self.bridge_thread.start()
            self.restore_snapshot_if_available()
        except Exception:
            self.stop_event.set()
            try:
                self.bridge.close()
            except Exception:
                pass
            self.trace_writer.stop()
            self.bridge_thread = None
            self.bridge_workers = []
            raise

    def shutdown(self):
        self.stop_event.set()
        if hasattr(self, "_stop_all_managed_workers_locked"):
            with self.lock:
                self._stop_all_managed_workers_locked()
        try:
            self.bridge.close()
        except OSError:
            pass
        if self.bridge_thread is not None:
            self.bridge_thread.join(timeout=2.0)
        for _ in self.bridge_workers:
            try:
                self.request_queue.put_nowait(None)
            except Exception:
                break
        for worker in self.bridge_workers:
            worker.join(timeout=2.0)
        self.bridge_workers = []
        self.trace_writer.stop()

    def _bridge_loop(self):
        while not self.stop_event.is_set():
            try:
                msg_type, message_id, generation, payload = self.bridge.read_frame()
            except EOFError:
                break
            except OSError as exc:
                if self.stop_event.is_set():
                    break
                with self.lock:
                    self.bridge_stats["bridge_errors"] += 1
                    self._set_runtime_state_locked(
                        "desynced",
                        reason=f"bridge read failed with OS error: {exc}",
                    )
                break
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                with self.lock:
                    self.bridge_stats["bridge_errors"] += 1
                    self._set_runtime_state_locked(
                        "desynced",
                        reason=f"bridge read failed unexpectedly: {exc}",
                    )
                break

            try:
                try:
                    self.request_queue.put_nowait(
                        (msg_type, message_id, generation, payload, time.monotonic())
                    )
                    self._note_queue_depth()
                except queue.Full:
                    self._bridge_reply_overloaded(message_id, generation)
            except Exception:
                if self.stop_event.is_set():
                    break

    def _bridge_worker_loop(self):
        while True:
            try:
                task = self.request_queue.get(timeout=0.1)
            except queue.Empty:
                if self.stop_event.is_set():
                    return
                continue
            if task is None:
                self.request_queue.task_done()
                return

            msg_type, message_id, generation, payload, started_at = task
            route_lock = self._request_route_lock(msg_type, payload)
            self._bridge_request_started()
            bridge_error = False
            try:
                try:
                    if route_lock is None:
                        status, data = self._handle_bridge_request(msg_type, message_id, payload)
                    else:
                        with route_lock:
                            status, data = self._handle_bridge_request(
                                msg_type, message_id, payload
                            )
                except Exception:
                    status, data = -errno.EIO, b""
                    bridge_error = True

                reply_payload = REPLY_PREFIX_STRUCT.pack(status, len(data)) + data
                try:
                    self._write_bridge_reply(
                        message_id,
                        generation,
                        reply_payload,
                        started_at=started_at,
                    )
                except OSError as exc:
                    bridge_error = True
                    if self.stop_event.is_set():
                        return
                    self._set_desynced_from_bridge_failure(
                        f"bridge reply failed with OS error: {exc}"
                    )
                except RuntimeError as exc:
                    bridge_error = True
                    if self.stop_event.is_set():
                        return
                    self._set_desynced_from_bridge_failure(str(exc))
            finally:
                latency_ms = (time.monotonic() - started_at) * 1000.0
                self._bridge_request_finished(latency_ms, bridge_error=bridge_error)
                self.request_queue.task_done()
                self._note_queue_depth()

    def _device_by_handle(self, handle: int):
        with self.lock:
            return self.devices_by_handle.get(handle)

    def _dispatch_controller(self, device: dict, event: dict):
        if device["backend"].get("worker"):
            pending = ControllerPending(event)
            with self.lock:
                self.pending_controller[event["request_id"]] = pending
            return self._dispatch_managed_controller(device, event)

        backend_id = device.get("attached_backend")
        if not backend_id:
            return -errno.ENODEV, b""

        pending = ControllerPending(event)
        with self.lock:
            self.pending_controller[event["request_id"]] = pending
            queue = self.backend_queues.setdefault(backend_id, collections.deque())
            cond = self.backend_conds.setdefault(backend_id, threading.Condition())
            queue.append(event)
            rpc_server = self.rpc_server
        with cond:
            cond.notify_all()
        if rpc_server is not None:
            rpc_server.notify_backend_event(backend_id)

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

    def _write_bridge_reply(
        self,
        message_id: int,
        generation: int,
        reply_payload: bytes,
        *,
        started_at: float,
    ):
        deadline = started_at + (self.kernel_timeout_ms / 1000.0)
        while True:
            try:
                with self.bridge_write_lock:
                    self.bridge.write_frame(
                        CMD_REPLY,
                        message_id,
                        reply_payload,
                        generation=generation,
                    )
                return
            except BlockingIOError as exc:
                if self.stop_event.is_set():
                    raise RuntimeError("bridge reply write interrupted by shutdown") from exc
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "bridge reply write timed out waiting for reply ring capacity"
                    ) from exc
                time.sleep(min(0.01, max(0.001, remaining)))
