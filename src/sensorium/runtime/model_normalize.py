#!/usr/bin/env python3
from __future__ import annotations

from sensorium.runtime.constants import (
    RUNTIME_BRIDGE_FRAME_LIMIT,
    RUNTIME_TEMPLATE_KINDS,
    RUNTIME_TRANSPORTS,
)
from sensorium.runtime.model_base import (
    RuntimeModelError,
    expect_bool,
    expect_hex_int,
    expect_list,
    expect_mapping,
    expect_name,
    expect_runtime_i2c_bus_name,
    expect_runtime_spi_bus_name,
    expect_runtime_uart_port_name,
    expect_string,
    normalize_bytes_literal,
    normalize_faults,
    normalize_i2c_registers,
    normalize_spi_settings,
    normalize_uart_settings,
)


def normalize_runtime_bus_item(item, label):
    item = expect_mapping(item, label)
    bus_id = expect_name(item.get("id"), f"{label}.id")
    transport = expect_string(item.get("transport"), f"{label}.transport")
    if transport not in RUNTIME_TRANSPORTS:
        raise RuntimeModelError(
            f"{label}.transport must be one of {sorted(RUNTIME_TRANSPORTS)}"
        )
    if transport == "i2c":
        name_value = expect_runtime_i2c_bus_name(item.get("name"), f"{label}.name")
    elif transport == "spi":
        name_value = expect_runtime_spi_bus_name(item.get("name"), f"{label}.name")
    else:
        name_value = expect_name(item.get("name"), f"{label}.name")
    return {
        "id": bus_id,
        "transport": transport,
        "name": name_value,
        "metadata": expect_mapping(item.get("metadata"), f"{label}.metadata"),
    }


def normalize_runtime_device_item(item, buses_by_id, label):
    item = expect_mapping(item, label)
    device_id = expect_name(item.get("id"), f"{label}.id")
    bus_id = expect_name(item.get("bus"), f"{label}.bus")
    bus = buses_by_id.get(bus_id)
    if bus is None:
        raise RuntimeModelError(f"{label}.bus refers to unknown bus {bus_id!r}")

    transport = expect_string(item.get("transport"), f"{label}.transport")
    if transport not in RUNTIME_TRANSPORTS:
        raise RuntimeModelError(
            f"{label}.transport must be one of {sorted(RUNTIME_TRANSPORTS)}"
        )
    if transport != bus["transport"]:
        raise RuntimeModelError(
            f"{label}.transport {transport!r} does not match bus {bus_id!r} transport {bus['transport']!r}"
        )

    backend = expect_mapping(item.get("backend"), f"{label}.backend")
    backend_kind = expect_string(backend.get("kind"), f"{label}.backend.kind")
    normalized_backend = {"kind": backend_kind}
    if backend_kind == "template":
        template = expect_string(backend.get("template"), f"{label}.backend.template")
        if template not in RUNTIME_TEMPLATE_KINDS:
            raise RuntimeModelError(
                f"{label}.backend.template must be one of {sorted(RUNTIME_TEMPLATE_KINDS)}"
            )
        normalized_backend["template"] = template
    elif backend_kind != "controller":
        raise RuntimeModelError(f"{label}.backend.kind must be 'template' or 'controller'")
    else:
        worker = backend.get("worker")
        if worker is not None:
            worker = expect_mapping(worker, f"{label}.backend.worker")
            command = [
                expect_string(item, f"{label}.backend.worker.command[]")
                for item in expect_list(worker.get("command"), f"{label}.backend.worker.command")
            ]
            if not command:
                raise RuntimeModelError(f"{label}.backend.worker.command must not be empty")
            normalized_backend["worker"] = {
                "command": command,
                "restart_limit": expect_hex_int(
                    worker.get("restart_limit", 3),
                    f"{label}.backend.worker.restart_limit",
                    minimum=0,
                    maximum=255,
                ),
                "restart_backoff_ms": expect_hex_int(
                    worker.get("restart_backoff_ms", 250),
                    f"{label}.backend.worker.restart_backoff_ms",
                    minimum=0,
                    maximum=60000,
                ),
            }
            if "cwd" in worker:
                normalized_backend["worker"]["cwd"] = expect_string(
                    worker.get("cwd"), f"{label}.backend.worker.cwd"
                )
            env_map = {}
            for key, value in expect_mapping(
                worker.get("env"), f"{label}.backend.worker.env"
            ).items():
                env_map[expect_string(key, f"{label}.backend.worker.env key")] = expect_string(
                    value, f"{label}.backend.worker.env[{key!r}]"
                )
            if env_map:
                normalized_backend["worker"]["env"] = env_map

    normalized_device = {
        "id": device_id,
        "bus": bus_id,
        "transport": transport,
        "backend": normalized_backend,
        "metadata": expect_mapping(item.get("metadata"), f"{label}.metadata"),
        "faults": normalize_faults(item.get("faults"), f"{label}.faults"),
    }

    if transport == "i2c":
        normalized_device["address"] = expect_hex_int(
            item.get("address"),
            f"{label}.address",
            minimum=0,
            maximum=0x7F,
        )
        normalized_device["settings"] = {}
        if backend_kind == "template":
            size = expect_hex_int(
                backend.get("size", 256),
                f"{label}.backend.size",
                minimum=1,
                maximum=RUNTIME_BRIDGE_FRAME_LIMIT,
            )
            pointer_width = expect_hex_int(
                backend.get("pointer_width", 1),
                f"{label}.backend.pointer_width",
                minimum=1,
                maximum=2,
            )
            normalized_backend["size"] = size
            normalized_backend["pointer_width"] = pointer_width
            normalized_backend["auto_increment"] = expect_bool(
                backend.get("auto_increment", True),
                f"{label}.backend.auto_increment",
            )
            normalized_backend["registers"] = normalize_i2c_registers(
                backend.get("registers", {}),
                maximum=size - 1,
            )
            clear_on_read = []
            for item in expect_list(
                backend.get("clear_on_read"),
                f"{label}.backend.clear_on_read",
            ):
                register = expect_hex_int(
                    item,
                    f"{label}.backend.clear_on_read[]",
                    minimum=0,
                    maximum=size - 1,
                )
                clear_on_read.append(f"0x{register:0{2 if size <= 0x100 else 4}x}")
            normalized_backend["clear_on_read"] = sorted(set(clear_on_read))
            write_effects = {}
            for key, effect_map in expect_mapping(
                backend.get("write_effects"),
                f"{label}.backend.write_effects",
            ).items():
                register = expect_hex_int(
                    key,
                    f"{label}.backend.write_effects key",
                    minimum=0,
                    maximum=size - 1,
                )
                width = 2 if size <= 0x100 else 4
                write_effects[f"0x{register:0{width}x}"] = normalize_i2c_registers(
                    effect_map,
                    maximum=size - 1,
                )
            normalized_backend["write_effects"] = write_effects
    elif transport == "spi":
        chip_select = expect_hex_int(
            item.get("chip_select"),
            f"{label}.chip_select",
            minimum=0,
            maximum=255,
        )
        bus_index = int(bus["name"][3:], 10)
        expected_device_name = f"spidev{bus_index}.{chip_select}"
        normalized_device["chip_select"] = chip_select
        normalized_device["device_name"] = expect_name(
            item.get("device_name", expected_device_name),
            f"{label}.device_name",
        )
        if normalized_device["device_name"] != expected_device_name:
            raise RuntimeModelError(
                f"{label}.device_name must match {expected_device_name!r} for bus {bus['name']!r}"
            )
        normalized_device["settings"] = normalize_spi_settings(item.get("settings"), f"{label}.settings")
        if backend_kind == "template":
            responses = {}
            for tx, rx in expect_mapping(
                backend.get("responses"), f"{label}.backend.responses"
            ).items():
                tx_hex = normalize_bytes_literal(tx, "backend.responses key")
                rx_hex = normalize_bytes_literal(rx, f"{label}.backend.responses[{tx!r}]")
                responses[tx_hex] = rx_hex
            prefix_responses = {}
            for tx, rx in expect_mapping(
                backend.get("prefix_responses"), f"{label}.backend.prefix_responses"
            ).items():
                tx_hex = normalize_bytes_literal(tx, "backend.prefix_responses key")
                rx_hex = normalize_bytes_literal(rx, f"{label}.backend.prefix_responses[{tx!r}]")
                prefix_responses[tx_hex] = rx_hex
            normalized_backend["responses"] = responses
            normalized_backend["prefix_responses"] = prefix_responses
            normalized_backend["default_response"] = normalize_bytes_literal(
                backend.get("default_response", ""),
                f"{label}.backend.default_response",
            )
            normalized_backend["echo"] = expect_bool(
                backend.get("echo", False),
                f"{label}.backend.echo",
            )
            if "flash_jedec_id" in backend:
                normalized_backend["flash_jedec_id"] = normalize_bytes_literal(
                    backend.get("flash_jedec_id", ""),
                    f"{label}.backend.flash_jedec_id",
                )
                normalized_backend["flash_status_register"] = expect_hex_int(
                    backend.get("flash_status_register", 0),
                    f"{label}.backend.flash_status_register",
                    minimum=0,
                    maximum=0xFF,
                )
                normalized_backend["flash_write_busy_cycles"] = expect_hex_int(
                    backend.get("flash_write_busy_cycles", 0),
                    f"{label}.backend.flash_write_busy_cycles",
                    minimum=0,
                    maximum=255,
                )
    elif transport == "uart":
        normalized_device["port_name"] = expect_runtime_uart_port_name(
            item.get("port_name", item.get("device_name")),
            f"{label}.port_name",
        )
        normalized_device["settings"] = normalize_uart_settings(item.get("settings"), f"{label}.settings")
        if backend_kind == "template":
            line_responses = {}
            for rx, tx in expect_mapping(
                backend.get("line_responses"), f"{label}.backend.line_responses"
            ).items():
                line_responses[expect_string(rx, "backend.line_responses key")] = expect_string(
                    tx,
                    f"{label}.backend.line_responses[{rx!r}]",
                    strip=False,
                )
            binary_responses = {}
            for rx, tx in expect_mapping(
                backend.get("binary_responses"), f"{label}.backend.binary_responses"
            ).items():
                rx_hex = normalize_bytes_literal(rx, "backend.binary_responses key")
                tx_hex = normalize_bytes_literal(tx, f"{label}.backend.binary_responses[{rx!r}]")
                binary_responses[rx_hex] = tx_hex
            normalized_backend["echo"] = expect_bool(
                backend.get("echo", True),
                f"{label}.backend.echo",
            )
            normalized_backend["line_responses"] = line_responses
            normalized_backend["binary_responses"] = binary_responses
            normalized_backend["default_response"] = normalize_bytes_literal(
                backend.get("default_response", ""),
                f"{label}.backend.default_response",
            )
            control_defaults = {}
            for key, value in expect_mapping(
                backend.get("control_defaults"),
                f"{label}.backend.control_defaults",
            ).items():
                control_defaults[expect_string(key, "backend.control_defaults key")] = expect_bool(
                    value,
                    f"{label}.backend.control_defaults[{key!r}]",
                )
            normalized_backend["control_defaults"] = control_defaults
            normalized_backend["cts_follows_rts"] = expect_bool(
                backend.get("cts_follows_rts", False),
                f"{label}.backend.cts_follows_rts",
            )
            normalized_backend["carrier_follows_dtr"] = expect_bool(
                backend.get("carrier_follows_dtr", False),
                f"{label}.backend.carrier_follows_dtr",
            )

    return normalized_device
