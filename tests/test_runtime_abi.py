import errno
import importlib.machinery
import importlib.util
import re
import struct
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sensorium.cli import sensoriumd
from sensorium.runtime import common as runtime_common

TEST_MANAGER_KWARGS = {
    "trace_path": None,
    "snapshot_path": None,
    "restore_snapshot": False,
}

EXPECTED_FRAME_LIMIT = 262144
EXPECTED_PAYLOAD_LIMIT = 4 * 1024 * 1024
EXPECTED_DEFAULT_PAYLOAD_ARENA = 8 * 1024 * 1024
EXPECTED_MAX_I2C_MSGS = 256
EXPECTED_MAX_SPI_XFERS = 256

ABI_DESC_STRUCT = struct.Struct("<IIIHHIIIiI")
ABI_CONTROL_STRUCT = struct.Struct("<39I")
ABI_SETUP_STRUCT = struct.Struct("<20I")
ABI_EVENTFDS_STRUCT = struct.Struct("<ii")
ABI_I2C_REQ_PREFIX_STRUCT = struct.Struct("<IIII")
ABI_I2C_REQ_MSG_STRUCT = struct.Struct("<HHHH")
ABI_SPI_REQ_PREFIX_STRUCT = struct.Struct("<IIIIII")
ABI_SPI_REQ_XFER_STRUCT = struct.Struct("<IIHBBBBBBB2x")
ABI_UART_REQ_PREFIX_STRUCT = struct.Struct("<IIIII")
ABI_UART_CFG_STRUCT = struct.Struct("<IIIIII")
ABI_BUS_CMD_STRUCT = struct.Struct("<III64s")
ABI_DEVICE_CMD_STRUCT = struct.Struct("<IIIIIIBB2x64s")
ABI_UART_MODEM_STRUCT = struct.Struct("<III")


class FakeBridge:
    def __init__(self, path=Path("/dev/fake-runtime-bridge")):
        self.path = path
        self.writes = []
        self.negotiated = False

    def open(self):
        return None

    def negotiate(self):
        self.negotiated = True
        return {
            "generation": 0,
            "features": sensoriumd.REQUIRED_FEATURES,
            "session_id": 1,
        }

    def close(self):
        return None

    def read_frame(self):
        raise EOFError("fake bridge has no queued frames")

    def write_frame(self, msg_type, message_id, payload, *, generation=0):
        self.writes.append((msg_type, message_id, generation, payload))

    def metrics(self):
        return {
            "session_id": 1,
            "generation": 0,
            "inflight_requests": 0,
            "inflight_credit_limit": 128,
            "queue_depths": {"control": 0, "transport": 0, "reply": 0},
            "ebusy_generation": 0,
            "ebusy_total": 0,
            "request_timeout_generation": 0,
            "request_timeout_total": 0,
            "request_completed_total": 0,
            "desynced": False,
        }


def build_spi_payload(device, tx_hex: str) -> bytes:
    tx = bytes.fromhex(tx_hex)
    payload = bytearray(
        ABI_SPI_REQ_PREFIX_STRUCT.pack(
            device["handle"],
            device["bus_handle"],
            0,
            1,
            len(tx),
            device.get("chip_select", 0),
        )
    )
    payload.extend(
        ABI_SPI_REQ_XFER_STRUCT.pack(
            len(tx),
            500000,
            0,
            8,
            0,
            1,
            1,
            0,
            1,
            1,
        )
    )
    payload.extend(tx)
    return bytes(payload)


class RuntimeAbiContractTests(unittest.TestCase):
    def test_python_struct_sizes_match_v5_contract(self):
        self.assertEqual(ABI_DESC_STRUCT.size, 36)
        self.assertEqual(ABI_CONTROL_STRUCT.size, 156)
        self.assertEqual(ABI_SETUP_STRUCT.size, 80)
        self.assertEqual(ABI_EVENTFDS_STRUCT.size, 8)
        self.assertEqual(ABI_I2C_REQ_PREFIX_STRUCT.size, 16)
        self.assertEqual(ABI_I2C_REQ_MSG_STRUCT.size, 8)
        self.assertEqual(ABI_SPI_REQ_PREFIX_STRUCT.size, 24)
        self.assertEqual(ABI_SPI_REQ_XFER_STRUCT.size, 19)
        self.assertEqual(ABI_UART_REQ_PREFIX_STRUCT.size, 20)
        self.assertEqual(ABI_UART_CFG_STRUCT.size, 24)

        self.assertEqual(sensoriumd.V5_DESC_STRUCT.size, ABI_DESC_STRUCT.size)
        self.assertEqual(sensoriumd.V5_CONTROL_STRUCT.size, ABI_CONTROL_STRUCT.size)
        self.assertEqual(sensoriumd.V5_SETUP_STRUCT.size, ABI_SETUP_STRUCT.size)
        self.assertEqual(sensoriumd.V5_EVENTFDS_STRUCT.size, ABI_EVENTFDS_STRUCT.size)
        self.assertEqual(sensoriumd.I2C_REQ_PREFIX_STRUCT.size, ABI_I2C_REQ_PREFIX_STRUCT.size)
        self.assertEqual(sensoriumd.I2C_REQ_MSG_STRUCT.size, ABI_I2C_REQ_MSG_STRUCT.size)
        self.assertEqual(sensoriumd.SPI_REQ_PREFIX_STRUCT.size, ABI_SPI_REQ_PREFIX_STRUCT.size)
        self.assertEqual(sensoriumd.SPI_REQ_XFER_STRUCT.size, ABI_SPI_REQ_XFER_STRUCT.size)
        self.assertEqual(sensoriumd.UART_REQ_PREFIX_STRUCT.size, ABI_UART_REQ_PREFIX_STRUCT.size)
        self.assertEqual(sensoriumd.UART_CFG_STRUCT.size, ABI_UART_CFG_STRUCT.size)

    def test_runtime_limits_match_v5_contract(self):
        self.assertEqual(runtime_common.RUNTIME_BRIDGE_ABI_VERSION, 5)
        self.assertEqual(runtime_common.RUNTIME_BRIDGE_FRAME_LIMIT, EXPECTED_FRAME_LIMIT)
        self.assertEqual(runtime_common.RUNTIME_MAX_PAYLOAD, EXPECTED_PAYLOAD_LIMIT)
        self.assertEqual(
            runtime_common.RUNTIME_BRIDGE_DEFAULT_PAYLOAD_ARENA_SIZE,
            EXPECTED_DEFAULT_PAYLOAD_ARENA,
        )
        self.assertEqual(runtime_common.RUNTIME_MAX_I2C_MSGS, EXPECTED_MAX_I2C_MSGS)
        self.assertEqual(runtime_common.RUNTIME_MAX_SPI_XFERS, EXPECTED_MAX_SPI_XFERS)

    def test_kernel_source_exposes_expected_v5_contract(self):
        source = (REPO_ROOT / "kernel" / "sensorium-runtime.c").read_text(encoding="utf-8")
        internal = (
            REPO_ROOT / "kernel" / "sensorium-runtime-internal.h"
        ).read_text(encoding="utf-8")

        self.assertRegex(internal, r"#define SENSORIUM_RUNTIME_VERSION 5U")
        self.assertRegex(internal, r"struct sensorium_runtime_v5_desc")
        self.assertRegex(internal, r"struct sensorium_runtime_v5_control")
        self.assertRegex(internal, r"struct sensorium_runtime_v5_setup")
        self.assertRegex(internal, r"SENSORIUM_RUNTIME_IOCTL_SETUP_V5")
        self.assertRegex(internal, r"SENSORIUM_RUNTIME_IOCTL_REGISTER_EVENTFDS")
        self.assertRegex(internal, r"SENSORIUM_RUNTIME_IOCTL_START_V5")
        self.assertRegex(internal, r"SENSORIUM_RUNTIME_IOCTL_SUBMIT_CONTROL")
        self.assertRegex(internal, r"SENSORIUM_RUNTIME_IOCTL_SUBMIT_REPLY")
        self.assertRegex(internal, r"#define SENSORIUM_RUNTIME_MAX_I2C_MSGS 256U")
        self.assertRegex(internal, r"#define SENSORIUM_RUNTIME_MAX_SPI_XFERS 256U")
        self.assertRegex(
            internal,
            r"#define SENSORIUM_RUNTIME_REQUIRED_FEATURES\s*\\\s*\n\s*\(SENSORIUM_RUNTIME_FEATURE_SHARED_RINGS \|",
        )
        self.assertRegex(source, r"sensorium_runtime_drain_control_ring")
        self.assertRegex(source, r"sensorium_runtime_drain_reply_ring")
        self.assertRegex(source, r"sensorium_runtime_bridge_mmap")

    def test_daemon_exposes_v5_feature_contract(self):
        self.assertEqual(
            sensoriumd.REQUIRED_FEATURES,
            sensoriumd.FEATURE_SHARED_RINGS
            | sensoriumd.FEATURE_EVENTFD_NOTIFY
            | sensoriumd.FEATURE_INDEXED_REQUESTS,
        )
        self.assertEqual(sensoriumd.QUEUE_CLASS_CONTROL, 1)
        self.assertEqual(sensoriumd.QUEUE_CLASS_TRANSPORT, 2)
        self.assertEqual(sensoriumd.QUEUE_CLASS_REPLY, 3)

    def test_daemon_validates_fixed_width_control_commands(self):
        sensoriumd.validate_command_payload(sensoriumd.CMD_RESET, b"")
        sensoriumd.validate_command_payload(sensoriumd.CMD_BUS_ADD, b"\0" * ABI_BUS_CMD_STRUCT.size)
        sensoriumd.validate_command_payload(
            sensoriumd.CMD_DEVICE_ADD, b"\0" * ABI_DEVICE_CMD_STRUCT.size
        )
        sensoriumd.validate_command_payload(
            sensoriumd.CMD_UART_SET_MODEM, b"\0" * ABI_UART_MODEM_STRUCT.size
        )

        with self.assertRaises(ValueError):
            sensoriumd.validate_command_payload(sensoriumd.CMD_RESET, b"\0")
        with self.assertRaises(ValueError):
            sensoriumd.validate_command_payload(sensoriumd.CMD_BUS_ADD, b"\0" * (ABI_BUS_CMD_STRUCT.size - 1))
        with self.assertRaises(ValueError):
            sensoriumd.validate_command_payload(
                sensoriumd.CMD_DEVICE_ADD, b"\0" * (ABI_DEVICE_CMD_STRUCT.size - 1)
            )
        with self.assertRaises(ValueError):
            sensoriumd.validate_command_payload(
                sensoriumd.CMD_UART_SET_MODEM, b"\0" * (ABI_UART_MODEM_STRUCT.size - 1)
            )

    def test_independent_spi_payload_reaches_template_backend(self):
        model = runtime_common.normalize_runtime_model(
            REPO_ROOT / "models" / "runtime" / "rpi-multibus.yaml"
        )
        manager = sensoriumd.RuntimeManager(Path("/dev/fake-runtime-bridge"), **TEST_MANAGER_KWARGS)
        manager.bridge = FakeBridge(manager.bridge.path)
        manager.apply_model(model)
        device = manager.devices["flash-spi"]

        status, data = manager._handle_bridge_request(
            sensoriumd.REQ_SPI_XFER,
            41,
            build_spi_payload(device, "9f0000"),
        )

        self.assertEqual(status, 0)
        self.assertEqual(data, bytes.fromhex("ef4018"))

    def test_spi_abi_mismatch_is_captured_in_trace(self):
        model = runtime_common.normalize_runtime_model(
            REPO_ROOT / "models" / "runtime" / "rpi-multibus.yaml"
        )
        manager = sensoriumd.RuntimeManager(Path("/dev/fake-runtime-bridge"), **TEST_MANAGER_KWARGS)
        manager.bridge = FakeBridge(manager.bridge.path)
        manager.apply_model(model)
        device = manager.devices["flash-spi"]

        bad_xfer_struct = struct.Struct("<IIHBBBBBBB3x")
        tx = bytes.fromhex("9f0000")
        payload = bytearray(
            ABI_SPI_REQ_PREFIX_STRUCT.pack(
                device["handle"],
                device["bus_handle"],
                0,
                1,
                len(tx),
                device.get("chip_select", 0),
            )
        )
        payload.extend(bad_xfer_struct.pack(len(tx), 500000, 0, 8, 0, 1, 1, 0, 1, 1))
        payload.extend(tx)

        status, data = manager._handle_bridge_request(sensoriumd.REQ_SPI_XFER, 42, bytes(payload))

        self.assertEqual(status, -errno.EPROTO)
        self.assertEqual(data, b"")
        event = manager.get_trace(1)["events"][0]
        self.assertEqual(event["transport"], "spi")
        self.assertEqual(event["status"], -errno.EPROTO)
        self.assertEqual(event["request"]["reason"], "data-length-mismatch")


if __name__ == "__main__":
    unittest.main()
