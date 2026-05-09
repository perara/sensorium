#!/usr/bin/env python3
from __future__ import annotations

import errno
import time


class RuntimeInternalMixin:
    def _device_view(self, device):
        return {
            key: value
            for key, value in device.items()
            if key not in {"template", "_route_key", "_route_lock"}
        }

    def _record_stats(self, device: dict | None, *, status: int, bytes_in: int, bytes_out: int):
        buckets = [self.stats_totals]
        if device is not None:
            buckets.append(device["stats"])

        now = round(time.time(), 6)
        for bucket in buckets:
            bucket["requests"] += 1
            bucket["bytes_in"] += bytes_in
            bucket["bytes_out"] += bytes_out
            bucket["last_activity_ts"] = now
            if status == 0:
                bucket["ok"] += 1
                bucket["last_error"] = None
            else:
                bucket["errors"] += 1
                bucket["last_error"] = status
                if status == -errno.ETIMEDOUT:
                    bucket["timeouts"] += 1

    def _fault_is_active(self, device: dict):
        fault = device.get("faults", {"mode": "none"})
        if fault.get("mode") == "none":
            return None
        return fault

    def _consume_fault(self, device: dict):
        fault = device.get("faults", {})
        remaining = int(fault.get("remaining", 0))
        if remaining > 0:
            remaining -= 1
            fault["remaining"] = remaining
            if remaining == 0:
                device["faults"] = {"mode": "none", "remaining": 0}
            self._persist_snapshot_locked()

    def _apply_fault_pre(self, device: dict):
        fault = self._fault_is_active(device)
        if not fault:
            return None
        mode = fault["mode"]
        if mode == "timeout":
            self._consume_fault(device)
            return -errno.ETIMEDOUT, b""
        if mode == "disconnect":
            self._consume_fault(device)
            return -errno.ENODEV, b""
        if mode == "errno":
            self._consume_fault(device)
            return -int(fault.get("errno", errno.EIO)), b""
        return None

    def _apply_fault_post(self, device: dict, status: int, data: bytes):
        fault = self._fault_is_active(device)
        if not fault or status != 0 or fault["mode"] != "short-reply":
            return status, data

        self._consume_fault(device)
        if fault.get("reply_data"):
            data = bytes.fromhex(fault["reply_data"])
        elif len(data) > 1:
            data = data[: max(1, len(data) // 2)]
        return status, data
