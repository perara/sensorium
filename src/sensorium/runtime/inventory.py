#!/usr/bin/env python3
from __future__ import annotations

import copy

from sensorium.runtime.common import expect_mapping, normalize_runtime_model_data
from sensorium.runtime.daemon_support import (
    BUS_CMD_STRUCT,
    CMD_BUS_ADD,
    CMD_DEVICE_ADD,
    CMD_RESET,
    CMD_UART_SET_MODEM,
    DEVICE_CMD_STRUCT,
    TRANSPORT_IDS,
    UART_MODEM_STRUCT,
    encode_c_string,
    parse_tty_name,
    I2CRegisterBankTemplate,
    SPIScriptTemplate,
    UARTScriptTemplate,
)


class RuntimeInventoryMixin:
    def _build_template(self, device: dict):
        backend = device["backend"]
        if backend["kind"] != "template":
            return None
        template_name = backend["template"]
        if template_name == "i2c-register-bank":
            return I2CRegisterBankTemplate(backend)
        if template_name == "spi-script":
            return SPIScriptTemplate(backend)
        if template_name == "uart-script":
            return UARTScriptTemplate(backend)
        raise RuntimeError(f"unsupported template {template_name}")

    def _sync_uart_defaults(self, device: dict):
        template = device.get("template")
        if not isinstance(template, UARTScriptTemplate):
            return
        mask, values = template.modem_defaults()
        if mask:
            payload = UART_MODEM_STRUCT.pack(device["handle"], mask, values)
            self.bridge.write_frame(CMD_UART_SET_MODEM, 0, payload)

    def _clear_state(self):
        current_session_id = self.bridge_stats.get("session_id")
        current_bridge_abi = self.bridge_stats.get("bridge_abi")
        current_busy_rejections_total = self.bridge_stats.get("busy_rejections_total", 0)
        current_ebusy_total = self.bridge_stats.get("ebusy_total", 0)
        current_request_timeout_total = self.bridge_stats.get("request_timeout_total", 0)
        current_completed_total = self.bridge_stats.get("completed", 0)
        current_bridge_errors = self.bridge_stats.get("bridge_errors", 0)
        current_late_replies = self.bridge_stats.get("late_replies", 0)
        self.trace_writer.reset_counters()
        self.model_name = None
        self.next_bus_handle = 1
        self.next_device_handle = 1024
        if hasattr(self, "_stop_all_managed_workers_locked"):
            self._stop_all_managed_workers_locked()
        self.buses = {}
        self.devices = {}
        self.devices_by_handle = {}
        self.pending_controller = {}
        self.stats_totals = self._fresh_stats()
        self.bridge_stats = self._fresh_bridge_stats()
        self.bridge_stats["session_id"] = current_session_id
        self.bridge_stats["bridge_abi"] = current_bridge_abi
        self.bridge_stats["busy_rejections_total"] = current_busy_rejections_total
        self.bridge_stats["ebusy_total"] = current_ebusy_total
        self.bridge_stats["request_timeout_total"] = current_request_timeout_total
        self.bridge_stats["completed"] = current_completed_total
        self.bridge_stats["bridge_errors"] = current_bridge_errors
        self.bridge_stats["late_replies"] = current_late_replies
        if self.rpc_server is not None:
            self.rpc_server.reset_generation_metrics()
        self.last_apply_error = None
        self.route_locks = {}
        for backend_id, queue in self.backend_queues.items():
            queue.clear()
            cond = self.backend_conds.get(backend_id)
            if cond is not None:
                with cond:
                    cond.notify_all()

    def reset_runtime(self):
        with self.lock:
            self._assert_mutation_allowed_locked("runtime.reset")
        self.bridge.write_frame(CMD_RESET, 0, b"")
        with self.lock:
            self._clear_state()
            self.generation += 1
            self._set_runtime_state_locked("empty")
            self.persistence["last_snapshot_error"] = None
            self._persist_snapshot_locked()

    def _discard_runtime_state_after_failure(self):
        reset_error = None
        try:
            self.bridge.write_frame(CMD_RESET, 0, b"")
        except Exception as exc:
            reset_error = exc
        with self.lock:
            if reset_error is None:
                self._clear_state()
                self._set_runtime_state_locked("empty")
            else:
                self._set_runtime_state_locked(
                    "desynced",
                    reason=f"runtime reset after apply failure failed: {reset_error}",
                )
            self._persist_snapshot_locked()
        return reset_error

    def _add_bus_locked(self, bus: dict):
        handle = self.next_bus_handle
        self.next_bus_handle += 1
        payload = BUS_CMD_STRUCT.pack(
            handle,
            TRANSPORT_IDS[bus["transport"]],
            0,
            encode_c_string(bus["name"], 64),
        )
        self.bridge.write_frame(CMD_BUS_ADD, 0, payload)
        self.buses[bus["id"]] = {
            **copy.deepcopy(bus),
            "handle": handle,
        }
        return self.buses[bus["id"]]

    def _add_device_locked(self, device: dict):
        device = copy.deepcopy(device)
        device.setdefault("metadata", {})
        device.setdefault("faults", {"mode": "none", "remaining": 0})
        if "settings" not in device:
            if device["transport"] == "spi":
                device["settings"] = {"mode": 0, "bits_per_word": 8, "max_speed_hz": 500000}
            elif device["transport"] == "uart":
                device["settings"] = {
                    "baud_rate": 115200,
                    "data_bits": 8,
                    "parity": "none",
                    "stop_bits": 1,
                    "xonxoff": False,
                    "rtscts": False,
                }
            else:
                device["settings"] = {}
        handle = self.next_device_handle
        self.next_device_handle += 1
        bus = self.buses[device["bus"]]
        location = 0
        node_name = device["id"]
        if device["transport"] == "i2c":
            location = device["address"]
            node_name = device["id"]
        elif device["transport"] == "spi":
            location = device["chip_select"]
            node_name = device["device_name"]
        elif device["transport"] == "uart":
            node_name = device["port_name"]
            _base_name, location = parse_tty_name(node_name)

        spi_mode = 0
        spi_bits_per_word = 0
        spi_max_speed_hz = 0
        if device["transport"] == "spi":
            spi_mode = int(device["settings"].get("mode", 0))
            spi_bits_per_word = int(device["settings"].get("bits_per_word", 8))
            spi_max_speed_hz = int(device["settings"].get("max_speed_hz", 500000))

        payload = DEVICE_CMD_STRUCT.pack(
            handle,
            TRANSPORT_IDS[device["transport"]],
            bus["handle"],
            location,
            0,
            spi_max_speed_hz,
            spi_mode,
            spi_bits_per_word,
            encode_c_string(node_name, 64),
        )
        self.bridge.write_frame(CMD_DEVICE_ADD, 0, payload)

        stored = {
            **copy.deepcopy(device),
            "handle": handle,
            "bus_handle": bus["handle"],
            "kernel_name": node_name,
            "template": self._build_template(device),
            "attached_backend": None,
            "stats": self._fresh_stats(),
        }
        if hasattr(self, "_refresh_device_route_locked"):
            self._refresh_device_route_locked(stored)
        self.devices[device["id"]] = stored
        self.devices_by_handle[handle] = stored
        if device["transport"] == "uart":
            self._sync_uart_defaults(stored)
        return stored

    def _apply_model_locked(self, model: dict, *, generation: int, attachments=None):
        self.model_name = model["name"]
        for bus in model["runtime"]["buses"]:
            self._add_bus_locked(bus)
        for device in model["runtime"]["devices"]:
            self._add_device_locked(device)
        if hasattr(self, "_start_managed_workers_locked"):
            self._start_managed_workers_locked()
        if attachments:
            for backend_id, device_ids in attachments.items():
                if device_ids:
                    self._attach_backend_locked(backend_id, device_ids)
        self.generation = generation
        self.last_apply_error = None
        self._set_runtime_state_locked("ready")
        self.persistence["last_snapshot_error"] = None
        self._persist_snapshot_locked()

    def apply_model(self, model: dict):
        model = normalize_runtime_model_data(model, source="<runtime.apply>")
        previous_snapshot = None
        previous_generation = 0
        with self.lock:
            self._assert_mutation_allowed_locked("runtime.apply")
            if self.buses or self.devices or self.model_name:
                previous_snapshot = self._snapshot_payload_locked()
            previous_generation = self.generation
            self.last_apply_error = None
            self._set_runtime_state_locked("applying")

        try:
            self.bridge.write_frame(CMD_RESET, 0, b"")
            with self.lock:
                self.model_name = model["name"]
                self._clear_state()
                self._apply_model_locked(
                    model,
                    generation=previous_generation + 1,
                )
        except Exception as exc:
            with self.lock:
                self.last_apply_error = str(exc)
            cleanup_error = self._discard_runtime_state_after_failure()
            rollback_error = None
            if cleanup_error is None and previous_snapshot is not None:
                try:
                    restored_model = self._restore_snapshot_model(previous_snapshot)
                    attachments = self._validate_backend_attachments_for_devices(
                        expect_mapping(
                            previous_snapshot.get("backend_attachments"),
                            "snapshot.backend_attachments",
                        ),
                        {
                            device["id"]: device
                            for device in restored_model["runtime"]["devices"]
                        },
                    )
                    with self.lock:
                        self._clear_state()
                        self._apply_model_locked(
                            restored_model,
                            generation=previous_generation,
                            attachments=attachments,
                        )
                except Exception as rollback_exc:
                    rollback_error = rollback_exc

            if cleanup_error is not None or rollback_error is not None:
                reason_parts = [str(exc)]
                if cleanup_error is not None:
                    reason_parts.append(
                        f"failed to reset runtime after apply failure: {cleanup_error}"
                    )
                if rollback_error is not None:
                    reason_parts.append(
                        f"failed to restore previous runtime generation: {rollback_error}"
                    )
                with self.lock:
                    self._set_runtime_state_locked(
                        "desynced",
                        reason="; ".join(reason_parts),
                    )
                    self._persist_snapshot_locked()
                raise RuntimeError("; ".join(reason_parts)) from exc
            raise
