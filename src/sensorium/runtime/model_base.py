#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

import yaml

from sensorium.runtime.constants import (
    RUNTIME_BRIDGE_FRAME_LIMIT,
    RUNTIME_FAULT_MODES,
    UART_PARITY_MODES,
)


class RuntimeModelError(RuntimeError):
    pass


def load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError as exc:
        raise RuntimeModelError(f"model not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeModelError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeModelError(f"model must be a mapping: {path}")
    return data


def expect_mapping(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RuntimeModelError(f"{label} must be a mapping")
    return value


def expect_list(value, label):
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeModelError(f"{label} must be a list")
    return value


def expect_string(value, label, *, strip=True):
    if not isinstance(value, str):
        raise RuntimeModelError(f"{label} must be a non-empty string")
    normalized = value.strip() if strip else value
    if not normalized:
        raise RuntimeModelError(f"{label} must be a non-empty string")
    return normalized


def expect_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeModelError(f"{label} must be an integer")
    return value


def expect_bool(value, label):
    if not isinstance(value, bool):
        raise RuntimeModelError(f"{label} must be a boolean")
    return value


def normalize_runtime_schema_version(value, label, *, default):
    if value is None:
        return default
    version = expect_int(value, label)
    if version != default:
        raise RuntimeModelError(f"{label} must be {default}")
    return version


def expect_hex_int(value, label, *, minimum=0, maximum=None):
    if isinstance(value, str):
        try:
            value = int(value, 0)
        except ValueError as exc:
            raise RuntimeModelError(f"{label} must be an integer or hex literal") from exc
    value = expect_int(value, label)
    if value < minimum:
        raise RuntimeModelError(f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise RuntimeModelError(f"{label} must be <= {maximum}")
    return value


def expect_name(value, label):
    value = expect_string(value, label)
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        raise RuntimeModelError(
            f"{label} must use only letters, numbers, dot, underscore, hyphen, or colon"
        )
    return value


def split_runtime_numeric_suffix(value, label):
    value = expect_name(value, label)
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*?)(\d+)", value)
    if not match:
        raise RuntimeModelError(f"{label} must end in a numeric suffix")
    return value, match.group(1), int(match.group(2), 10)


def expect_runtime_i2c_bus_name(value, label):
    value = expect_name(value, label)
    match = re.fullmatch(r"i2c-(\d+)", value)
    if not match:
        raise RuntimeModelError(f"{label} must use canonical i2c-N naming")
    canonical = f"i2c-{int(match.group(1), 10)}"
    if value != canonical:
        raise RuntimeModelError(f"{label} must use canonical i2c-N naming without leading zeroes")
    return canonical


def expect_runtime_spi_bus_name(value, label):
    value = expect_name(value, label)
    match = re.fullmatch(r"spi(\d+)", value)
    if not match:
        raise RuntimeModelError(f"{label} must use canonical spiN naming")
    canonical = f"spi{int(match.group(1), 10)}"
    if value != canonical:
        raise RuntimeModelError(f"{label} must use canonical spiN naming without leading zeroes")
    return canonical


def expect_runtime_uart_port_name(value, label):
    value, base_name, index = split_runtime_numeric_suffix(value, label)
    canonical = f"{base_name}{index}"
    if value != canonical:
        raise RuntimeModelError(
            f"{label} must use a tty-style name with a canonical numeric suffix"
        )
    return canonical


def normalize_bytes_literal(value, label):
    if isinstance(value, bytes):
        return value.hex()
    if not isinstance(value, str):
        raise RuntimeModelError(f"{label} must be a hex string or text string")
    stripped = value.strip()
    if stripped.startswith("text:"):
        return stripped[5:].encode("utf-8").hex()
    compact = stripped.replace(" ", "").replace("_", "")
    if compact.startswith("0x"):
        compact = compact[2:]
    if len(compact) % 2:
        compact = "0" + compact
    try:
        bytes.fromhex(compact)
    except ValueError as exc:
        raise RuntimeModelError(
            f"{label} must be valid hex or use text:<payload> for UTF-8 text"
        ) from exc
    return compact.lower()


def normalize_i2c_registers(registers, *, maximum=0xFF):
    normalized = {}
    for key, value in expect_mapping(registers, "backend.registers").items():
        reg = expect_hex_int(key, "backend.registers key", minimum=0, maximum=maximum)
        byte = expect_hex_int(value, f"backend.registers[{key!r}]", minimum=0, maximum=0xFF)
        width = 2 if maximum <= 0xFF else 4
        normalized[f"0x{reg:0{width}x}"] = byte
    return normalized


def normalize_faults(value, label):
    faults = expect_mapping(value, label)
    mode = expect_string(faults.get("mode", "none"), f"{label}.mode")
    if mode not in RUNTIME_FAULT_MODES:
        raise RuntimeModelError(f"{label}.mode must be one of {sorted(RUNTIME_FAULT_MODES)}")

    normalized = {
        "mode": mode,
        "remaining": expect_hex_int(faults.get("remaining", 0), f"{label}.remaining", minimum=0),
    }
    if mode == "errno":
        normalized["errno"] = expect_hex_int(
            faults.get("errno", 5),
            f"{label}.errno",
            minimum=1,
            maximum=4095,
        )
    elif "errno" in faults:
        normalized["errno"] = expect_hex_int(
            faults["errno"],
            f"{label}.errno",
            minimum=1,
            maximum=4095,
        )

    if mode == "short-reply" or "reply_data" in faults:
        normalized["reply_data"] = normalize_bytes_literal(
            faults.get("reply_data", ""),
            f"{label}.reply_data",
        )
    return normalized


def normalize_spi_settings(value, label):
    settings = expect_mapping(value, label)
    return {
        "mode": expect_hex_int(settings.get("mode", 0), f"{label}.mode", minimum=0, maximum=3),
        "bits_per_word": expect_hex_int(
            settings.get("bits_per_word", 8), f"{label}.bits_per_word", minimum=1, maximum=32
        ),
        "max_speed_hz": expect_hex_int(
            settings.get("max_speed_hz", 500000), f"{label}.max_speed_hz", minimum=1
        ),
    }


def normalize_uart_settings(value, label):
    settings = expect_mapping(value, label)
    parity = expect_string(settings.get("parity", "none"), f"{label}.parity")
    if parity not in UART_PARITY_MODES:
        raise RuntimeModelError(f"{label}.parity must be one of {sorted(UART_PARITY_MODES)}")

    data_bits = expect_hex_int(settings.get("data_bits", 8), f"{label}.data_bits", minimum=5, maximum=8)
    stop_bits = expect_hex_int(settings.get("stop_bits", 1), f"{label}.stop_bits", minimum=1, maximum=2)
    return {
        "baud_rate": expect_hex_int(settings.get("baud_rate", 115200), f"{label}.baud_rate", minimum=1),
        "data_bits": data_bits,
        "parity": parity,
        "stop_bits": stop_bits,
        "xonxoff": expect_bool(settings.get("xonxoff", False), f"{label}.xonxoff"),
        "rtscts": expect_bool(settings.get("rtscts", False), f"{label}.rtscts"),
    }
