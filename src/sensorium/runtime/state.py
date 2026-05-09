#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import time

from sensorium.runtime.common import (
    RUNTIME_BRIDGE_ABI_VERSION,
    RUNTIME_DAEMON_STATES,
    RUNTIME_MODEL_SCHEMA_VERSION,
    RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    expect_mapping,
    normalize_runtime_model_data,
)
from sensorium.runtime.daemon_support import read_jsonl_tail, write_json_atomic


class RuntimeStateMixin:
    def _fresh_stats(self):
        return {
            "requests": 0,
            "ok": 0,
            "errors": 0,
            "timeouts": 0,
            "bytes_in": 0,
            "bytes_out": 0,
            "last_error": None,
            "last_activity_ts": None,
        }

    def _fresh_bridge_stats(self):
        return {
            "bridge_abi": RUNTIME_BRIDGE_ABI_VERSION,
            "worker_count": self.worker_count,
            "max_pending_requests": self.max_pending_requests,
            "kernel_timeout_ms": self.kernel_timeout_ms,
            "controller_timeout_ms": self.controller_timeout_ms,
            "session_id": None,
            "queue_depth": 0,
            "queue_depth_max": 0,
            "inflight": 0,
            "inflight_max": 0,
            "bridge_errors": 0,
            "busy_rejections_generation": 0,
            "busy_rejections_total": 0,
            "late_replies": 0,
            "completed": 0,
            "latency_ms_avg": 0.0,
            "latency_ms_max": 0.0,
            "ring_queue_depths": {"control": 0, "transport": 0, "reply": 0},
            "ebusy_generation": 0,
            "ebusy_total": 0,
            "request_timeout_generation": 0,
            "request_timeout_total": 0,
            "worker_restarts": 0,
            "last_worker_failure": None,
        }

    def _fresh_persistence(self):
        return {
            "session_started_ts": self.session_started_ts,
            "trace_path": str(self.trace_path) if self.trace_path else None,
            "trace_limit": self.trace_limit,
            "trace_file_limit_bytes": self.trace_file_limit_bytes,
            "trace_queue_limit": self.trace_queue_limit,
            "trace_queue_limit_bytes": self.trace_queue_limit_bytes,
            "trace_loaded": 0,
            "trace_write_errors": 0,
            "last_trace_error": None,
            "trace_drop_count": 0,
            "trace_queue_depth": 0,
            "trace_queue_bytes": 0,
            "trace_queue_depth_max": 0,
            "trace_queue_bytes_max": 0,
            "snapshot_path": str(self.snapshot_path) if self.snapshot_path else None,
            "snapshot_schema_version": RUNTIME_SNAPSHOT_SCHEMA_VERSION,
            "snapshot_restore_enabled": self.restore_snapshot,
            "snapshot_loaded": False,
            "snapshot_write_errors": 0,
            "last_snapshot_error": None,
            "last_snapshot_saved_ts": None,
            "last_snapshot_restored_ts": None,
        }

    def _load_trace_history(self):
        if self.trace_path is None:
            return
        try:
            self.trace = read_jsonl_tail(self.trace_path, self.trace_limit)
            self.persistence["trace_loaded"] = len(self.trace)
            self.persistence["last_trace_error"] = None
        except FileNotFoundError:
            self.trace = []
            self.persistence["trace_loaded"] = 0
            self.persistence["last_trace_error"] = None
        except Exception as exc:
            self.trace = []
            self.persistence["last_trace_error"] = str(exc)

    def _trace_snapshot(self):
        with self.lock:
            return list(self.trace)

    def _set_trace_write_status(self, error: str | None):
        with self.lock:
            if error is None:
                self.persistence["last_trace_error"] = None
            else:
                self.persistence["trace_write_errors"] += 1
                self.persistence["last_trace_error"] = error

    def _refresh_trace_metrics_locked(self):
        stats = self.trace_writer.stats()
        self.persistence["trace_drop_count"] = stats["drop_count"]
        self.persistence["trace_queue_depth"] = stats["queue_depth"]
        self.persistence["trace_queue_bytes"] = stats["queue_bytes"]
        self.persistence["trace_queue_depth_max"] = stats["max_queue_depth"]
        self.persistence["trace_queue_bytes_max"] = stats["max_queue_bytes"]

    def _refresh_bridge_metrics_locked(self):
        try:
            metrics = self.bridge.metrics()
        except Exception:
            metrics = {}
        if not metrics:
            return
        self.bridge_stats["session_id"] = metrics.get("session_id")
        self.bridge_stats["inflight"] = metrics.get("inflight_requests", 0)
        self.bridge_stats["inflight_max"] = max(
            self.bridge_stats["inflight_max"], self.bridge_stats["inflight"]
        )
        self.bridge_stats["ring_queue_depths"] = copy.deepcopy(
            metrics.get("queue_depths", {"control": 0, "transport": 0, "reply": 0})
        )
        self.bridge_stats["ebusy_generation"] = metrics.get("ebusy_generation", 0)
        self.bridge_stats["ebusy_total"] = metrics.get("ebusy_total", 0)
        self.bridge_stats["request_timeout_generation"] = metrics.get(
            "request_timeout_generation", 0
        )
        self.bridge_stats["request_timeout_total"] = metrics.get("request_timeout_total", 0)
        if metrics.get("desynced") and self.runtime_state != "desynced":
            self._set_runtime_state_locked(
                "desynced",
                reason="kernel bridge reported desynced runtime session",
            )

    def _assert_mutation_allowed_locked(self, operation: str):
        if self.runtime_state == "desynced":
            raise RuntimeError(
                f"runtime is desynced; {operation} is blocked until resync succeeds"
            )

    def _record_trace(self, record: dict):
        record = {**record, "ts": round(time.time(), 6)}
        with self.lock:
            self.trace.append(record)
            if len(self.trace) > self.trace_limit:
                del self.trace[: len(self.trace) - self.trace_limit]
        self.trace_writer.enqueue(record)

    def flush_trace_writes(self):
        self.trace_writer.flush()

    def _set_runtime_state_locked(self, state: str, *, reason: str | None = None):
        if state not in RUNTIME_DAEMON_STATES:
            raise RuntimeError(f"invalid runtime state: {state}")
        self.runtime_state = state
        self.desync_reason = reason

    def _health_summary_locked(self):
        status = self.HEALTH_OK
        reasons = []
        rpc_metrics = self.rpc_server.rpc_metrics() if self.rpc_server else {}
        self._refresh_bridge_metrics_locked()

        if self.runtime_state == "desynced":
            status = self.HEALTH_ERROR
            if self.desync_reason:
                reasons.append(self.desync_reason)
        elif self.runtime_state == "degraded":
            status = self.HEALTH_WARN
            if self.desync_reason:
                reasons.append(self.desync_reason)

        if self.persistence.get("last_snapshot_error"):
            if status == self.HEALTH_OK:
                status = self.HEALTH_WARN
            reasons.append(f"snapshot:{self.persistence['last_snapshot_error']}")
        if self.persistence.get("last_trace_error"):
            if status == self.HEALTH_OK:
                status = self.HEALTH_WARN
            reasons.append(f"trace:{self.persistence['last_trace_error']}")
        if self.bridge_stats.get("late_replies"):
            if status == self.HEALTH_OK:
                status = self.HEALTH_WARN
            reasons.append(f"late_replies={self.bridge_stats['late_replies']}")
        if self.bridge_stats.get("busy_rejections_generation"):
            if status == self.HEALTH_OK:
                status = self.HEALTH_WARN
            reasons.append(
                f"busy_rejections={self.bridge_stats['busy_rejections_generation']}"
            )
        if self.bridge_stats.get("ebusy_generation"):
            if status == self.HEALTH_OK:
                status = self.HEALTH_WARN
            reasons.append(f"bridge_ebusy={self.bridge_stats['ebusy_generation']}")
        if self.bridge_stats.get("request_timeout_generation"):
            if status == self.HEALTH_OK:
                status = self.HEALTH_WARN
            reasons.append(
                f"bridge_timeouts={self.bridge_stats['request_timeout_generation']}"
            )
        if self.bridge_stats.get("worker_restarts"):
            if status == self.HEALTH_OK:
                status = self.HEALTH_WARN
            reasons.append(f"worker_restarts={self.bridge_stats['worker_restarts']}")
        if self.persistence.get("trace_drop_count"):
            if status == self.HEALTH_OK:
                status = self.HEALTH_WARN
            reasons.append(f"trace_drop_count={self.persistence['trace_drop_count']}")
        if rpc_metrics.get("busy_rejections_generation"):
            if status == self.HEALTH_OK:
                status = self.HEALTH_WARN
            reasons.append(
                f"rpc_busy_rejections={rpc_metrics['busy_rejections_generation']}"
            )

        return {
            "status": status,
            "state": self.runtime_state,
            "generation": self.generation,
            "desync_reason": self.desync_reason,
            "reasons": reasons,
        }

    def _snapshot_bus(self, bus: dict):
        return {
            key: copy.deepcopy(bus[key])
            for key in ("id", "transport", "name")
            if key in bus
        }

    def _snapshot_device(self, device: dict):
        return {
            key: copy.deepcopy(device[key])
            for key in (
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
            )
            if key in device
        }

    def _snapshot_backend_attachments_locked(self):
        attachments = {}
        for device_id, device in self.devices.items():
            backend_id = device.get("attached_backend")
            if not backend_id:
                continue
            attachments.setdefault(backend_id, []).append(device_id)
        return {
            backend_id: sorted(device_ids)
            for backend_id, device_ids in sorted(attachments.items())
        }

    def _backend_meta_locked(self, backend_id: str):
        meta = self.backend_meta.get(backend_id)
        if meta is None:
            meta = {
                "created_ts": round(time.time(), 6),
                "last_attach_ts": None,
                "last_detach_ts": None,
                "last_poll_ts": None,
                "last_reply_ts": None,
                "last_heartbeat_ts": None,
                "managed": False,
            }
            self.backend_meta[backend_id] = meta
        return meta

    def _export_model_locked(self):
        return {
            "name": self.model_name or "runtime",
            "schema_version": RUNTIME_MODEL_SCHEMA_VERSION,
            "adapter": "runtime",
            "runtime": {
                "buses": [self._snapshot_bus(bus) for bus in self.buses.values()],
                "devices": [self._snapshot_device(device) for device in self.devices.values()],
            },
        }

    def _snapshot_payload_locked(self):
        return {
            "schema_version": RUNTIME_SNAPSHOT_SCHEMA_VERSION,
            "saved_at": round(time.time(), 6),
            "generation": self.generation,
            "runtime_state": self.runtime_state,
            "desync_reason": self.desync_reason,
            "health": self._health_summary_locked(),
            "model": self._export_model_locked(),
            "backend_attachments": self._snapshot_backend_attachments_locked(),
        }

    def _persist_snapshot_locked(self):
        with self.lock:
            if self.snapshot_path is None:
                return
            try:
                if not self.buses and not self.devices:
                    try:
                        self.snapshot_path.unlink()
                    except FileNotFoundError:
                        pass
                    self.persistence["last_snapshot_saved_ts"] = None
                    self.persistence["last_snapshot_error"] = None
                    return
                snapshot = self._snapshot_payload_locked()
                write_json_atomic(self.snapshot_path, snapshot)
                self.persistence["last_snapshot_saved_ts"] = snapshot["saved_at"]
                self.persistence["last_snapshot_error"] = None
            except OSError as exc:
                self.persistence["snapshot_write_errors"] += 1
                self.persistence["last_snapshot_error"] = str(exc)

    def _restore_snapshot_model(self, snapshot: dict):
        snapshot_version = snapshot.get("schema_version")
        if snapshot_version != RUNTIME_SNAPSHOT_SCHEMA_VERSION:
            raise RuntimeError(
                f"snapshot schema_version must be {RUNTIME_SNAPSHOT_SCHEMA_VERSION}, got {snapshot_version!r}"
            )
        model = expect_mapping(snapshot.get("model"), "snapshot.model")
        try:
            return normalize_runtime_model_data(model, source="<snapshot>")
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

    def _validate_backend_attachments_for_devices(self, attachments, devices_by_id):
        normalized = {}
        attached_by_backend = {}
        for backend_id, device_ids in attachments.items():
            if not isinstance(device_ids, list):
                raise RuntimeError(f"snapshot backend attachment for {backend_id!r} must be a list")
            backend_id = str(backend_id)
            validated_ids = []
            for device_id in device_ids:
                device_id = str(device_id)
                device = devices_by_id.get(device_id)
                if device is None:
                    raise RuntimeError(f"unknown device id: {device_id}")
                if device["backend"]["kind"] != "controller":
                    raise RuntimeError(f"device {device_id} is not controller-backed")
                if device["backend"].get("worker"):
                    raise RuntimeError(
                        f"device {device_id} is broker-managed and cannot restore an external backend attachment"
                    )
                previous = attached_by_backend.get(device_id)
                if previous is not None and previous != backend_id:
                    raise RuntimeError(
                        f"device {device_id} cannot be attached to multiple backends"
                    )
                attached_by_backend[device_id] = backend_id
                validated_ids.append(device_id)
            normalized[backend_id] = validated_ids
        return normalized

    def restore_snapshot_if_available(self):
        if not self.restore_snapshot or self.snapshot_path is None or not self.snapshot_path.exists():
            return False
        try:
            snapshot = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            restored_model = self._restore_snapshot_model(snapshot)
            attachments = expect_mapping(snapshot.get("backend_attachments"), "snapshot.backend_attachments")
            devices_by_id = {
                device["id"]: device for device in restored_model["runtime"]["devices"]
            }
            attachments = self._validate_backend_attachments_for_devices(
                attachments,
                devices_by_id,
            )
            self.apply_model(restored_model)
            for backend_id, device_ids in attachments.items():
                if not device_ids:
                    continue
                self.attach_backend(backend_id, list(device_ids))
            with self.lock:
                self.persistence["snapshot_loaded"] = True
                self.persistence["last_snapshot_restored_ts"] = round(time.time(), 6)
                self.persistence["last_snapshot_error"] = None
            return True
        except Exception as exc:
            if isinstance(exc, RuntimeError) and "snapshot schema_version" in str(exc):
                try:
                    self.snapshot_path.unlink()
                except OSError:
                    pass
            with self.lock:
                self.persistence["snapshot_loaded"] = False
                self.persistence["last_snapshot_error"] = str(exc)
            return False

    def health(self):
        with self.lock:
            self._refresh_trace_metrics_locked()
            self._refresh_bridge_metrics_locked()
            rpc_metrics = self.rpc_server.rpc_metrics() if self.rpc_server else None
            return {
                "health": self._health_summary_locked(),
                "bridge": copy.deepcopy(self.bridge_stats),
                "rpc": copy.deepcopy(rpc_metrics),
                "persistence": copy.deepcopy(self.persistence),
            }

    def resync_runtime(self):
        with self.lock:
            snapshot = self._snapshot_payload_locked()
        model = self._restore_snapshot_model(snapshot)
        attachments = self._validate_backend_attachments_for_devices(
            expect_mapping(snapshot.get("backend_attachments"), "snapshot.backend_attachments"),
            {device["id"]: device for device in model["runtime"]["devices"]},
        )
        try:
            self.bridge.write_frame(self.CMD_RESET, 0, b"")
            with self.lock:
                self._clear_state()
                self._set_runtime_state_locked("applying")
                self._apply_model_locked(
                    model,
                    generation=self.generation + 1,
                    attachments=attachments,
                )
        except Exception as exc:
            with self.lock:
                self.last_apply_error = str(exc)
                self._set_runtime_state_locked("desynced", reason=f"resync failed: {exc}")
                self._persist_snapshot_locked()
            raise
        return {
            "ok": True,
            "generation": self.generation,
            "state": self.runtime_state,
        }

    def status(self):
        with self.lock:
            self._refresh_trace_metrics_locked()
            self._refresh_bridge_metrics_locked()
            rpc_metrics = self.rpc_server.rpc_metrics() if self.rpc_server else None
            return {
                "model": self.model_name,
                "schema_version": RUNTIME_MODEL_SCHEMA_VERSION,
                "snapshot_schema_version": RUNTIME_SNAPSHOT_SCHEMA_VERSION,
                "state": self.runtime_state,
                "generation": self.generation,
                "desync_reason": self.desync_reason,
                "health": self._health_summary_locked(),
                "bridge": str(self.bridge.path),
                "bridge_runtime": copy.deepcopy(self.bridge_stats),
                "queue_depths": {
                    "bridge_requests": self.bridge_stats.get("queue_depth", 0),
                    "bridge_control": self.bridge_stats.get("ring_queue_depths", {}).get(
                        "control", 0
                    ),
                    "bridge_transport": self.bridge_stats.get("ring_queue_depths", {}).get(
                        "transport", 0
                    ),
                    "bridge_reply": self.bridge_stats.get("ring_queue_depths", {}).get(
                        "reply", 0
                    ),
                    "trace_records": self.persistence.get("trace_queue_depth", 0),
                    "rpc_clients": (rpc_metrics or {}).get("active_clients", 0),
                },
                "rpc": copy.deepcopy(rpc_metrics),
                "bus_count": len(self.buses),
                "device_count": len(self.devices),
                "backend_count": len(self.backend_queues),
                "stats": copy.deepcopy(self.stats_totals),
                "persistence": copy.deepcopy(self.persistence),
                "buses": list(self.buses.values()),
                "devices": [self._device_view(device) for device in self.devices.values()],
            }

    def list_backends(self):
        with self.lock:
            return {
                "backends": [
                    {
                        "backend_id": backend_id,
                        "queued_events": len(self.backend_queues.get(backend_id, [])),
                        "pending_requests": sum(
                            1
                            for pending in self.pending_controller.values()
                            if pending.event.get("backend_id") == backend_id
                        ),
                        "meta": copy.deepcopy(self._backend_meta_locked(backend_id)),
                        "attached_devices": sorted(
                            device_id
                            for device_id, device in self.devices.items()
                            if device.get("attached_backend") == backend_id
                        ),
                    }
                    for backend_id in sorted(self.backend_queues.keys())
                ]
            }

    def get_device(self, device_id: str):
        with self.lock:
            device = self.devices.get(device_id)
            if device is None:
                raise RuntimeError(f"unknown device id: {device_id}")
            return self._device_view(device)

    def get_stats(self):
        with self.lock:
            self._refresh_trace_metrics_locked()
            self._refresh_bridge_metrics_locked()
            rpc_metrics = self.rpc_server.rpc_metrics() if self.rpc_server else None
            return {
                "state": self.runtime_state,
                "generation": self.generation,
                "desync_reason": self.desync_reason,
                "bridge": copy.deepcopy(self.bridge_stats),
                "rpc": copy.deepcopy(rpc_metrics),
                "runtime": copy.deepcopy(self.stats_totals),
                "persistence": copy.deepcopy(self.persistence),
                "devices": {
                    device_id: copy.deepcopy(device["stats"])
                    for device_id, device in self.devices.items()
                },
            }

    def get_trace(self, limit: int = 32):
        with self.lock:
            limit = max(1, min(int(limit), self.trace_limit))
            return {"events": copy.deepcopy(self.trace[-limit:])}
