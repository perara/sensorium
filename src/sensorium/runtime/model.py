#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from sensorium.runtime.constants import RUNTIME_MODEL_SCHEMA_VERSION, RUNTIME_TRANSPORTS
from sensorium.runtime.model_base import (
    RuntimeModelError,
    expect_bool,
    expect_hex_int,
    expect_int,
    expect_list,
    expect_mapping,
    expect_name,
    expect_runtime_i2c_bus_name,
    expect_runtime_spi_bus_name,
    expect_runtime_uart_port_name,
    expect_string,
    load_yaml,
    normalize_bytes_literal,
    normalize_faults,
    normalize_i2c_registers,
    normalize_runtime_schema_version,
    normalize_spi_settings,
    normalize_uart_settings,
    split_runtime_numeric_suffix,
)
from sensorium.runtime.model_normalize import (
    normalize_runtime_bus_item,
    normalize_runtime_device_item,
)


def normalize_runtime_model(path: Path) -> dict:
    raw = load_yaml(path)
    return normalize_runtime_model_data(raw, source=path)


def normalize_runtime_model_data(raw, *, source="<runtime-model>") -> dict:
    raw = expect_mapping(raw, "model")
    name = expect_string(raw.get("name"), "name")
    adapter = expect_string(raw.get("adapter"), "adapter")
    if adapter != "runtime":
        raise RuntimeModelError("runtime model must use adapter=runtime")
    schema_version = normalize_runtime_schema_version(
        raw.get("schema_version"),
        "schema_version",
        default=RUNTIME_MODEL_SCHEMA_VERSION,
    )

    runtime = expect_mapping(raw.get("runtime"), "runtime")
    buses_raw = expect_list(runtime.get("buses"), "runtime.buses")
    devices_raw = expect_list(runtime.get("devices"), "runtime.devices")

    buses = []
    buses_by_id = {}
    bus_names_by_transport = {transport: set() for transport in RUNTIME_TRANSPORTS}
    for index, item in enumerate(buses_raw):
        bus = normalize_runtime_bus_item(item, f"runtime.buses[{index}]")
        bus_id = bus["id"]
        if bus_id in buses_by_id:
            raise RuntimeModelError(f"duplicate bus id: {bus_id}")
        if bus["name"] in bus_names_by_transport[bus["transport"]]:
            raise RuntimeModelError(
                f"duplicate {bus['transport']} bus name: {bus['name']}"
            )
        buses_by_id[bus_id] = bus
        bus_names_by_transport[bus["transport"]].add(bus["name"])
        buses.append(bus)

    devices = []
    device_ids = set()
    i2c_locations_by_bus = {}
    spi_locations_by_bus = {}
    uart_ports = set()
    for index, item in enumerate(devices_raw):
        device = normalize_runtime_device_item(item, buses_by_id, f"runtime.devices[{index}]")
        device_id = device["id"]
        if device_id in device_ids:
            raise RuntimeModelError(f"duplicate device id: {device_id}")
        device_ids.add(device_id)
        if device["transport"] == "i2c":
            bus_locations = i2c_locations_by_bus.setdefault(device["bus"], set())
            if device["address"] in bus_locations:
                raise RuntimeModelError(
                    f"duplicate i2c address on bus {device['bus']}: 0x{device['address']:02x}"
                )
            bus_locations.add(device["address"])
        elif device["transport"] == "spi":
            bus_locations = spi_locations_by_bus.setdefault(device["bus"], set())
            if device["chip_select"] in bus_locations:
                raise RuntimeModelError(
                    f"duplicate spi chip_select on bus {device['bus']}: {device['chip_select']}"
                )
            bus_locations.add(device["chip_select"])
        elif device["transport"] == "uart":
            if device["port_name"] in uart_ports:
                raise RuntimeModelError(
                    f"duplicate uart port_name: {device['port_name']}"
                )
            uart_ports.add(device["port_name"])
        devices.append(device)

    return {
        "path": str(raw.get("path", source)),
        "name": name,
        "schema_version": schema_version,
        "adapter": adapter,
        "runtime": {"buses": buses, "devices": devices},
    }
