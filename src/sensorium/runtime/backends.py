#!/usr/bin/env python3
from __future__ import annotations

import collections
import copy
import threading
import time

from sensorium.runtime.common import expect_mapping, normalize_runtime_device_item
from sensorium.runtime.daemon_support import (
    CMD_UART_INJECT_RX,
    CMD_UART_SET_MODEM,
    UART_MODEM_BITS,
    UART_MODEM_STRUCT,
    UART_RX_PREFIX_STRUCT,
)


class RuntimeBackendMixin:
    def _merge_patch(self, target: dict, patch: dict):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._merge_patch(target[key], value)
            else:
                target[key] = copy.deepcopy(value)

    def _attach_backend_locked(self, backend_id: str, device_ids):
        queue = self.backend_queues.setdefault(backend_id, collections.deque())
        self.backend_conds.setdefault(backend_id, threading.Condition())
        self._backend_meta_locked(backend_id)["last_attach_ts"] = round(time.time(), 6)
        validated_ids = []
        for device_id in device_ids:
            if device_id not in self.devices:
                raise RuntimeError(f"unknown device id: {device_id}")
            device = self.devices[device_id]
            if device["backend"]["kind"] != "controller":
                raise RuntimeError(f"device {device_id} is not controller-backed")
            if device["backend"].get("worker"):
                raise RuntimeError(
                    f"device {device_id} is broker-managed and cannot attach to an external backend"
                )
            validated_ids.append(device_id)
        for device_id in validated_ids:
            device = self.devices[device_id]
            device["attached_backend"] = backend_id
            if hasattr(self, "_refresh_device_route_locked"):
                self._refresh_device_route_locked(device)
        return {"backend_id": backend_id, "queued_events": len(queue)}

    def update_device(self, device_id: str, patch: dict):
        with self.lock:
            self._assert_mutation_allowed_locked("device.update")
            device = self.devices.get(device_id)
            if device is None:
                raise RuntimeError(f"unknown device id: {device_id}")

            original_identity = {
                key: copy.deepcopy(device.get(key))
                for key in {
                    "bus",
                    "transport",
                    "address",
                    "chip_select",
                    "device_name",
                    "port_name",
                }
            }
            candidate = {
                key: copy.deepcopy(value)
                for key, value in device.items()
                if key in {
                    "id",
                    "bus",
                    "transport",
                    "address",
                    "chip_select",
                    "device_name",
                    "port_name",
                    "backend",
                    "metadata",
                    "faults",
                    "settings",
                }
            }
            self._merge_patch(candidate, expect_mapping(patch, "device.patch"))
            normalized = normalize_runtime_device_item(candidate, self.buses, "device")

            immutable_keys = {
                "bus",
                "transport",
                "address",
                "chip_select",
                "device_name",
                "port_name",
            }
            for key in immutable_keys:
                if normalized.get(key) != original_identity.get(key):
                    raise RuntimeError(f"device field {key} cannot be changed live")

            had_managed_worker = bool(device["backend"].get("worker"))
            device["metadata"] = normalized["metadata"]
            device["faults"] = normalized["faults"]
            device["settings"] = normalized["settings"]
            device["backend"] = normalized["backend"]
            device["template"] = self._build_template(normalized)
            has_managed_worker = bool(device["backend"].get("worker"))
            if has_managed_worker and device.get("attached_backend"):
                raise RuntimeError(
                    f"device {device_id} cannot switch to broker-managed mode while attached to an external backend"
                )
            if hasattr(self, "_stop_managed_worker_locked") and had_managed_worker and not has_managed_worker:
                self._stop_managed_worker_locked(device_id)
            if hasattr(self, "_launch_managed_worker_locked") and has_managed_worker:
                self._launch_managed_worker_locked(device_id)
            if hasattr(self, "_refresh_device_route_locked"):
                self._refresh_device_route_locked(device)
            if device["transport"] == "uart":
                self._sync_uart_defaults(device)
            self._persist_snapshot_locked()
            return self._device_view(device)

    def attach_backend(self, backend_id: str, device_ids):
        with self.lock:
            self._assert_mutation_allowed_locked("backend.attach")
            result = self._attach_backend_locked(backend_id, device_ids)
            self._persist_snapshot_locked()
            return result

    def detach_backend(self, backend_id: str, device_ids=None):
        with self.lock:
            self._assert_mutation_allowed_locked("backend.detach")
            targets = device_ids or list(self.devices.keys())
            detached = []
            for device_id in targets:
                device = self.devices.get(device_id)
                if not device or device["attached_backend"] != backend_id:
                    continue
                device["attached_backend"] = None
                if hasattr(self, "_refresh_device_route_locked"):
                    self._refresh_device_route_locked(device)
                detached.append(device_id)
            self._backend_meta_locked(backend_id)["last_detach_ts"] = round(time.time(), 6)
            self._persist_snapshot_locked()
            return {"backend_id": backend_id, "detached_devices": detached}

    def next_event(self, backend_id: str, timeout: float):
        cond = self.backend_conds.setdefault(backend_id, threading.Condition())
        queue = self.backend_queues.setdefault(backend_id, collections.deque())
        with self.lock:
            self._backend_meta_locked(backend_id)["last_poll_ts"] = round(time.time(), 6)
        deadline = time.monotonic() + timeout
        with cond:
            while not queue:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                cond.wait(timeout=remaining)
            return queue.popleft()

    def note_backend_poll(self, backend_id: str):
        with self.lock:
            self._backend_meta_locked(backend_id)["last_poll_ts"] = round(time.time(), 6)

    def poll_next_event(self, backend_id: str):
        with self.lock:
            queue = self.backend_queues.setdefault(backend_id, collections.deque())
            if not queue:
                return None
            return queue.popleft()

    def reply_event(self, backend_id: str, request_id: int, status: int, data_hex: str):
        with self.lock:
            pending = self.pending_controller.get(request_id)
            if pending is None:
                self._late_reply()
                raise RuntimeError(f"unknown request id: {request_id}")
            self._backend_meta_locked(backend_id)["last_reply_ts"] = round(time.time(), 6)
        self._complete_pending_controller(
            request_id,
            status,
            bytes.fromhex(data_hex) if data_hex else b"",
        )
        return {"request_id": request_id}

    def heartbeat_backend(self, backend_id: str):
        with self.lock:
            meta = self._backend_meta_locked(backend_id)
            meta["last_heartbeat_ts"] = round(time.time(), 6)
            return {"backend_id": backend_id, "last_heartbeat_ts": meta["last_heartbeat_ts"]}

    def inject_uart_rx(self, device_id: str, data_hex: str):
        with self.lock:
            self._assert_mutation_allowed_locked("uart.inject_rx")
            device = self.devices.get(device_id)
            if device is None or device["transport"] != "uart":
                raise RuntimeError(f"unknown UART device: {device_id}")
            data = bytes.fromhex(data_hex)
            payload = UART_RX_PREFIX_STRUCT.pack(device["handle"], 0, len(data)) + data
            self.bridge.write_frame(CMD_UART_INJECT_RX, 0, payload)
            return {"device_id": device_id, "bytes": len(data)}

    def set_uart_modem(self, device_id: str, control_map: dict):
        mask = 0
        values = 0
        for name, enabled in control_map.items():
            bit = UART_MODEM_BITS.get(name.lower())
            if bit is None:
                raise RuntimeError(f"unknown UART modem signal: {name}")
            mask |= bit
            if enabled:
                values |= bit

        with self.lock:
            self._assert_mutation_allowed_locked("uart.control_set")
            device = self.devices.get(device_id)
            if device is None or device["transport"] != "uart":
                raise RuntimeError(f"unknown UART device: {device_id}")
            payload = UART_MODEM_STRUCT.pack(device["handle"], mask, values)
            self.bridge.write_frame(CMD_UART_SET_MODEM, 0, payload)
            return {"device_id": device_id, "mask": mask, "values": values}
