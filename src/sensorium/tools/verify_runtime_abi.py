#!/usr/bin/env python3
import re
import struct
import sys

from sensorium._paths import REPO_ROOT
from sensorium.cli import sensoriumd
from sensorium.runtime import common as runtime_common

EXPECTED = {
    "frame_limit": 262144,
    "payload_limit": 4 * 1024 * 1024,
    "payload_arena": 8 * 1024 * 1024,
    "max_i2c_msgs": 256,
    "max_spi_xfers": 256,
    "desc_size": 36,
    "control_size": 156,
    "setup_size": 80,
    "eventfds_size": 8,
    "i2c_req_prefix_size": 16,
    "i2c_req_msg_size": 8,
    "spi_req_prefix_size": 24,
    "spi_req_xfer_size": 19,
    "uart_req_prefix_size": 20,
    "uart_cfg_size": 24,
    "bus_cmd_size": 76,
    "device_cmd_size": 92,
    "uart_modem_size": 12,
}

ABI_STRUCTS = {
    "desc_size": struct.Struct("<IIIHHIIIiI"),
    "control_size": struct.Struct("<39I"),
    "setup_size": struct.Struct("<20I"),
    "eventfds_size": struct.Struct("<ii"),
    "i2c_req_prefix_size": struct.Struct("<IIII"),
    "i2c_req_msg_size": struct.Struct("<HHHH"),
    "spi_req_prefix_size": struct.Struct("<IIIIII"),
    "spi_req_xfer_size": struct.Struct("<IIHBBBBBBB2x"),
    "uart_req_prefix_size": struct.Struct("<IIIII"),
    "uart_cfg_size": struct.Struct("<IIIIII"),
    "bus_cmd_size": struct.Struct("<III64s"),
    "device_cmd_size": struct.Struct("<IIIIIIBB2x64s"),
    "uart_modem_size": struct.Struct("<III"),
}


def fail(message: str):
    print(f"ABI verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def require(condition: bool, message: str):
    if not condition:
        fail(message)


def main():
    kernel_source = (REPO_ROOT / "kernel" / "sensorium-runtime.c").read_text(
        encoding="utf-8"
    )
    kernel_internal = (
        REPO_ROOT / "kernel" / "sensorium-runtime-internal.h"
    ).read_text(encoding="utf-8")

    require(
        runtime_common.RUNTIME_BRIDGE_ABI_VERSION == 5,
        "runtime_common bridge ABI version is not 5",
    )
    require(
        runtime_common.RUNTIME_BRIDGE_FRAME_LIMIT == EXPECTED["frame_limit"],
        "runtime_common frame limit changed unexpectedly",
    )
    require(
        runtime_common.RUNTIME_MAX_PAYLOAD == EXPECTED["payload_limit"],
        "runtime_common payload limit changed unexpectedly",
    )
    require(
        runtime_common.RUNTIME_BRIDGE_DEFAULT_PAYLOAD_ARENA_SIZE == EXPECTED["payload_arena"],
        "runtime_common default payload arena changed unexpectedly",
    )
    require(
        runtime_common.RUNTIME_MAX_I2C_MSGS == EXPECTED["max_i2c_msgs"],
        "runtime_common max I2C message count changed unexpectedly",
    )
    require(
        runtime_common.RUNTIME_MAX_SPI_XFERS == EXPECTED["max_spi_xfers"],
        "runtime_common max SPI transfer count changed unexpectedly",
    )

    require(
        re.search(r"#define SENSORIUM_RUNTIME_VERSION 5U", kernel_internal),
        "kernel bridge ABI version is not 5",
    )
    require(
        re.search(r"struct sensorium_runtime_v5_desc", kernel_internal),
        "kernel v5 descriptor struct missing",
    )
    require(
        re.search(r"struct sensorium_runtime_v5_control", kernel_internal),
        "kernel v5 control page struct missing",
    )
    require(
        re.search(r"struct sensorium_runtime_v5_setup", kernel_internal),
        "kernel v5 setup struct missing",
    )
    require(
        re.search(r"SENSORIUM_RUNTIME_IOCTL_SETUP_V5", kernel_internal),
        "kernel SETUP_V5 ioctl missing",
    )
    require(
        re.search(r"SENSORIUM_RUNTIME_IOCTL_REGISTER_EVENTFDS", kernel_internal),
        "kernel REGISTER_EVENTFDS ioctl missing",
    )
    require(
        re.search(r"SENSORIUM_RUNTIME_IOCTL_START_V5", kernel_internal),
        "kernel START_V5 ioctl missing",
    )
    require(
        re.search(r"SENSORIUM_RUNTIME_IOCTL_SUBMIT_CONTROL", kernel_internal),
        "kernel SUBMIT_CONTROL ioctl missing",
    )
    require(
        re.search(r"SENSORIUM_RUNTIME_IOCTL_SUBMIT_REPLY", kernel_internal),
        "kernel SUBMIT_REPLY ioctl missing",
    )
    require(
        re.search(r"sensorium_runtime_drain_control_ring", kernel_source),
        "kernel control-ring drain path missing",
    )
    require(
        re.search(r"sensorium_runtime_drain_reply_ring", kernel_source),
        "kernel reply-ring drain path missing",
    )
    require(
        re.search(r"sensorium_runtime_bridge_mmap", kernel_source),
        "kernel mmap bridge path missing",
    )

    daemon_structs = {
        "desc_size": sensoriumd.V5_DESC_STRUCT.size,
        "control_size": sensoriumd.V5_CONTROL_STRUCT.size,
        "setup_size": sensoriumd.V5_SETUP_STRUCT.size,
        "eventfds_size": sensoriumd.V5_EVENTFDS_STRUCT.size,
        "i2c_req_prefix_size": sensoriumd.I2C_REQ_PREFIX_STRUCT.size,
        "i2c_req_msg_size": sensoriumd.I2C_REQ_MSG_STRUCT.size,
        "spi_req_prefix_size": sensoriumd.SPI_REQ_PREFIX_STRUCT.size,
        "spi_req_xfer_size": sensoriumd.SPI_REQ_XFER_STRUCT.size,
        "uart_req_prefix_size": sensoriumd.UART_REQ_PREFIX_STRUCT.size,
        "uart_cfg_size": sensoriumd.UART_CFG_STRUCT.size,
        "bus_cmd_size": sensoriumd.BUS_CMD_STRUCT.size,
        "device_cmd_size": sensoriumd.DEVICE_CMD_STRUCT.size,
        "uart_modem_size": sensoriumd.UART_MODEM_STRUCT.size,
    }

    for key, abi_struct in ABI_STRUCTS.items():
        expected_size = EXPECTED[key]
        require(
            abi_struct.size == expected_size,
            f"independent ABI size for {key} changed unexpectedly: {abi_struct.size}",
        )
        require(
            daemon_structs[key] == expected_size,
            f"sensoriumd ABI size mismatch for {key}: expected {expected_size}, got {daemon_structs[key]}",
        )

    require(
        sensoriumd.REQUIRED_FEATURES
        == sensoriumd.FEATURE_SHARED_RINGS
        | sensoriumd.FEATURE_EVENTFD_NOTIFY
        | sensoriumd.FEATURE_INDEXED_REQUESTS,
        "daemon required feature mask no longer matches v5 contract",
    )

    sensoriumd.validate_command_payload(sensoriumd.CMD_RESET, b"")
    sensoriumd.validate_command_payload(sensoriumd.CMD_BUS_ADD, b"\0" * EXPECTED["bus_cmd_size"])
    sensoriumd.validate_command_payload(
        sensoriumd.CMD_DEVICE_ADD, b"\0" * EXPECTED["device_cmd_size"]
    )
    sensoriumd.validate_command_payload(
        sensoriumd.CMD_UART_SET_MODEM, b"\0" * EXPECTED["uart_modem_size"]
    )

    print("Runtime ABI verification passed.")


if __name__ == "__main__":
    main()
