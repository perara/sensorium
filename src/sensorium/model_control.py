#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import hashlib
from pathlib import Path

import yaml

from sensorium._paths import REPO_ROOT
from sensorium.runtime.common import (
    RuntimeModelError,
    normalize_runtime_model,
)


MODELS_DIR = REPO_ROOT / "models"
MODULE_PARAMS_DIR = Path("/sys/module/sensorium/parameters")
KBUILD_STATE_DIR = REPO_ROOT / ".cache" / "kbuild"
BUILD_FINGERPRINT_STAMP = KBUILD_STATE_DIR / "last-source-fingerprint"

VALID_ADAPTERS = {"camera", "iio", "runtime"}
VALID_TRANSPORTS = {"virtual", "i2c", "spi", "uart"}
VALID_FAULT_MODES = {"none", "stale-data", "timeout"}
VALID_IIO_PROFILES = {"environment-basic", "environment-plus"}

MODULE_STATE_KEYS = (
    "adapter",
    "transport",
    "instance",
    "transport_device_name",
    "i2c_address",
    "fault_mode",
    "family",
    "sensor",
    "repeat_last_frame",
    "iio_profile",
    "iio_temperature_millic",
    "iio_pressure_pascal",
    "iio_temperature_step_millic",
    "iio_pressure_step_pascal",
    "iio_humidity_millipercent",
    "iio_humidity_step_millipercent",
    "iio_temperature_thresh_rising_millic",
    "update_interval_ms",
)


class ModelError(RuntimeError):
    pass


def load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError as exc:
        raise ModelError(f"model not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ModelError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ModelError(f"model must be a mapping: {path}")
    return data


def expect_mapping(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ModelError(f"{label} must be a mapping")
    return value


def expect_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ModelError(f"{label} must be a non-empty string")
    return value.strip()


def expect_bool(value, label):
    if not isinstance(value, bool):
        raise ModelError(f"{label} must be a boolean")
    return value


def expect_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelError(f"{label} must be an integer")
    return value


def expect_i2c_address(value, label):
    if isinstance(value, str):
        try:
            value = int(value, 0)
        except ValueError as exc:
            raise ModelError(f"{label} must be an integer or hex literal") from exc
    value = expect_int(value, label)
    if not 0 <= value <= 0x7F:
        raise ModelError(f"{label} must be a 7-bit I2C address (0x00-0x7f)")
    return value


def expect_device_name(value, label):
    value = expect_string(value, label)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ModelError(
            f"{label} must use only letters, numbers, dot, underscore, or hyphen"
        )
    return value


def normalize_model(path: Path) -> dict:
    raw = load_yaml(path)
    name = expect_string(raw.get("name"), "name")
    adapter = expect_string(raw.get("adapter"), "adapter")
    if adapter not in VALID_ADAPTERS:
        raise ModelError(f"unsupported adapter '{adapter}'")
    if adapter == "runtime":
        try:
            return normalize_runtime_model(path)
        except RuntimeModelError as exc:
            raise ModelError(str(exc)) from exc

    transport = raw.get("transport", "virtual" if adapter == "camera" else "i2c")
    transport = expect_string(transport, "transport")
    if transport not in VALID_TRANSPORTS:
        raise ModelError(f"unsupported transport '{transport}'")

    if adapter == "camera" and transport != "virtual":
        raise ModelError("camera models currently require transport=virtual")
    if adapter == "iio" and transport == "virtual":
        raise ModelError("iio models must use i2c, spi, or uart transport")

    timing = expect_mapping(raw.get("timing"), "timing")
    faults = expect_mapping(raw.get("faults"), "faults")
    config = expect_mapping(raw.get("config"), "config")
    transport_config = expect_mapping(config.get("transport"), "config.transport")
    registers = expect_mapping(raw.get("registers"), "registers")

    fault_mode = expect_string(faults.get("mode", "none"), "faults.mode")
    if fault_mode not in VALID_FAULT_MODES:
        raise ModelError(f"unsupported fault mode '{fault_mode}'")

    normalized = {
        "path": str(path),
        "name": name,
        "adapter": adapter,
        "transport": transport,
        "timing": {},
        "faults": {"mode": fault_mode},
        "registers": registers,
        "config": {"transport": {}},
    }

    if "device_name" in transport_config:
        normalized["config"]["transport"]["device_name"] = expect_device_name(
            transport_config["device_name"],
            "config.transport.device_name",
        )
    elif transport == "i2c":
        normalized["config"]["transport"]["device_name"] = "i2c-1"
    elif transport == "spi":
        normalized["config"]["transport"]["device_name"] = "spidev0.0"
    elif transport == "uart":
        normalized["config"]["transport"]["device_name"] = "ttyAMA0"

    if transport == "i2c":
        normalized["config"]["transport"]["address"] = expect_i2c_address(
            transport_config.get("address", 0x76),
            "config.transport.address",
        )

    if adapter == "camera":
        camera = expect_mapping(config.get("camera"), "config.camera")
        family = expect_string(camera.get("family"), "config.camera.family")
        sensor = expect_string(camera.get("sensor"), "config.camera.sensor")
        repeat_last_frame = timing.get("repeat_last_frame", True)
        repeat_last_frame = expect_bool(repeat_last_frame, "timing.repeat_last_frame")
        normalized["timing"]["repeat_last_frame"] = repeat_last_frame
        normalized["config"]["camera"] = {
            "family": family,
            "sensor": sensor,
        }
    elif adapter == "iio":
        iio = expect_mapping(config.get("iio"), "config.iio")
        profile = expect_string(
            iio.get("profile", "environment-basic"), "config.iio.profile"
        )
        if profile not in VALID_IIO_PROFILES:
            raise ModelError(f"unsupported IIO profile '{profile}'")
        normalized["config"]["iio"] = {
            "profile": profile,
            "temperature_millic": expect_int(
                iio.get("temperature_millic", 21500),
                "config.iio.temperature_millic",
            ),
            "pressure_pascal": expect_int(
                iio.get("pressure_pascal", 101325),
                "config.iio.pressure_pascal",
            ),
            "temperature_step_millic": expect_int(
                iio.get("temperature_step_millic", 250),
                "config.iio.temperature_step_millic",
            ),
            "pressure_step_pascal": expect_int(
                iio.get("pressure_step_pascal", 120),
                "config.iio.pressure_step_pascal",
            ),
            "humidity_millipercent": expect_int(
                iio.get("humidity_millipercent", 45500),
                "config.iio.humidity_millipercent",
            ),
            "humidity_step_millipercent": expect_int(
                iio.get("humidity_step_millipercent", 350),
                "config.iio.humidity_step_millipercent",
            ),
            "temperature_thresh_rising_millic": expect_int(
                iio.get("temperature_thresh_rising_millic", 26000),
                "config.iio.temperature_thresh_rising_millic",
            ),
            "update_interval_ms": expect_int(
                timing.get("update_interval_ms", 1000),
                "timing.update_interval_ms",
            ),
        }

    return normalized


def build_apply_env(model: dict) -> dict:
    env = os.environ.copy()
    env["SENSORIUM_ADAPTER"] = model["adapter"]
    env["SENSORIUM_TRANSPORT"] = model["transport"]
    env["SENSORIUM_INSTANCE"] = model["name"]
    env["SENSORIUM_TRANSPORT_DEVICE_NAME"] = model["config"]["transport"].get(
        "device_name", ""
    )
    if model["transport"] == "i2c":
        env["SENSORIUM_I2C_ADDRESS"] = hex(model["config"]["transport"]["address"])
    env["SENSORIUM_FAULT_MODE"] = model["faults"]["mode"]

    module_args = []
    if env.get("SENSORIUM_INSMOD_ARGS"):
        module_args.append(env["SENSORIUM_INSMOD_ARGS"])

    if model["adapter"] == "camera":
        env["SENSORIUM_FAMILY"] = model["config"]["camera"]["family"]
        env["SENSORIUM_SENSOR"] = model["config"]["camera"]["sensor"]
        if not model["timing"]["repeat_last_frame"]:
            module_args.append("repeat_last_frame=0")
    elif model["adapter"] == "iio":
        iio = model["config"]["iio"]
        module_args.extend(
            [
                f"iio_profile={iio['profile']}",
                f"iio_temperature_millic={iio['temperature_millic']}",
                f"iio_pressure_pascal={iio['pressure_pascal']}",
                f"iio_temperature_step_millic={iio['temperature_step_millic']}",
                f"iio_pressure_step_pascal={iio['pressure_step_pascal']}",
                f"iio_humidity_millipercent={iio['humidity_millipercent']}",
                f"iio_humidity_step_millipercent={iio['humidity_step_millipercent']}",
                f"iio_temperature_thresh_rising_millic={iio['temperature_thresh_rising_millic']}",
                f"update_interval_ms={iio['update_interval_ms']}",
            ]
        )

    env["SENSORIUM_INSMOD_ARGS"] = " ".join(module_args).strip()
    return env


def read_module_state() -> dict | None:
    if not MODULE_PARAMS_DIR.exists():
        return None

    state = {}
    for key in MODULE_STATE_KEYS:
        path = MODULE_PARAMS_DIR / key
        if path.exists():
            state[key] = path.read_text().strip()
    return state


def _parse_module_int(value):
    if value is None or value == "":
        return None
    try:
        return int(str(value), 0)
    except ValueError:
        return None


def _parse_module_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "y", "yes", "true", "on"}:
        return True
    if normalized in {"0", "n", "no", "false", "off"}:
        return False
    return None


def model_matches_module_state(model: dict, state: dict | None) -> bool:
    if not state:
        return False
    if model["adapter"] == "runtime":
        return False

    expected_pairs = {
        "adapter": model["adapter"],
        "transport": model["transport"],
        "instance": model["name"],
        "transport_device_name": model["config"]["transport"].get("device_name", ""),
        "fault_mode": model["faults"]["mode"],
    }
    for key, expected in expected_pairs.items():
        if str(state.get(key, "")).strip() != str(expected):
            return False

    if model["transport"] == "i2c":
        if _parse_module_int(state.get("i2c_address")) != model["config"]["transport"]["address"]:
            return False

    if model["adapter"] == "camera":
        camera = model["config"]["camera"]
        if str(state.get("family", "")).strip() != camera["family"]:
            return False
        if str(state.get("sensor", "")).strip() != camera["sensor"]:
            return False
        if _parse_module_bool(state.get("repeat_last_frame")) != model["timing"]["repeat_last_frame"]:
            return False

    if model["adapter"] == "iio":
        iio = model["config"]["iio"]
        if str(state.get("iio_profile", "")).strip() != iio["profile"]:
            return False
        int_expectations = {
            "iio_temperature_millic": iio["temperature_millic"],
            "iio_pressure_pascal": iio["pressure_pascal"],
            "iio_temperature_step_millic": iio["temperature_step_millic"],
            "iio_pressure_step_pascal": iio["pressure_step_pascal"],
            "iio_humidity_millipercent": iio["humidity_millipercent"],
            "iio_humidity_step_millipercent": iio["humidity_step_millipercent"],
            "iio_temperature_thresh_rising_millic": iio["temperature_thresh_rising_millic"],
            "update_interval_ms": iio["update_interval_ms"],
        }
        for key, expected in int_expectations.items():
            if _parse_module_int(state.get(key)) != expected:
                return False

    return True


def iter_model_paths() -> list[Path]:
    if not MODELS_DIR.exists():
        return []
    return sorted(MODELS_DIR.rglob("*.yaml"))


def compute_kernel_source_fingerprint() -> str:
    digest = hashlib.sha256()
    files = [
        REPO_ROOT / "Makefile",
        REPO_ROOT / "kernel" / "Makefile",
        *sorted((REPO_ROOT / "kernel").glob("*.c")),
        *sorted((REPO_ROOT / "kernel").glob("*.h")),
        *sorted((REPO_ROOT / "kernel").glob("*.inc")),
    ]
    for path in files:
        digest.update(str(path.relative_to(REPO_ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def kernel_build_is_current() -> bool:
    if not BUILD_FINGERPRINT_STAMP.exists():
        return False
    return BUILD_FINGERPRINT_STAMP.read_text().strip() == compute_kernel_source_fingerprint()
