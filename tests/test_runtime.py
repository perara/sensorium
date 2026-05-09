import errno
import importlib.machinery
import importlib.util
import json
import os
import queue
import socket
import struct
import shutil
import sys
import tempfile
import termios
import threading
import time
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
RUNTIME_SCRIPTS_DIR = SCRIPTS_DIR / "runtime"
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sensorium.cli import sensoriumctl, sensoriumd
from sensorium.runtime import client as runtime_client
from sensorium.runtime import common as runtime_common
from sensorium.runtime import managed_workers as runtime_workers
from sensorium.runtime import rpc as runtime_rpc


ABI_I2C_REQ_PREFIX_STRUCT = struct.Struct("<IIII")
ABI_I2C_REQ_MSG_STRUCT = struct.Struct("<HHHH")
ABI_SPI_REQ_PREFIX_STRUCT = struct.Struct("<IIIIII")
ABI_SPI_REQ_XFER_STRUCT = struct.Struct("<IIHBBBBBBB2x")
ABI_UART_REQ_PREFIX_STRUCT = struct.Struct("<IIIII")
ABI_MAX_I2C_MSGS = runtime_common.RUNTIME_MAX_I2C_MSGS
ABI_MAX_SPI_XFERS = runtime_common.RUNTIME_MAX_SPI_XFERS


TEST_MANAGER_KWARGS = {
    "trace_path": None,
    "snapshot_path": None,
    "restore_snapshot": False,
}


class FakeBridge:
    def __init__(self, path=Path("/dev/fake-runtime-bridge")):
        self.path = path
        self.writes = []
        self.opened = False
        self.negotiated = False
        self.negotiated_features = sensoriumd.REQUIRED_FEATURES
        self.metrics_payload = {}

    def open(self):
        self.opened = True

    def negotiate(self):
        self.negotiated = True
        return {
            "generation": 0,
            "features": self.negotiated_features,
            "session_id": 1,
        }

    def close(self):
        self.opened = False

    def read_frame(self):
        raise EOFError("fake bridge has no queued frames")

    def write_frame(self, msg_type, message_id, payload, *, generation=0):
        self.writes.append((msg_type, message_id, generation, payload))

    def metrics(self):
        return dict(self.metrics_payload)


class FailingBridge(FakeBridge):
    def __init__(
        self,
        path=Path("/dev/fake-runtime-bridge"),
        *,
        fail_type,
        fail_on_match=1,
        exc=None,
    ):
        super().__init__(path)
        self.fail_type = fail_type
        self.fail_on_match = fail_on_match
        self.exc = exc or RuntimeError("simulated bridge write failure")
        self.match_count = 0

    def write_frame(self, msg_type, message_id, payload, *, generation=0):
        if msg_type == self.fail_type:
            self.match_count += 1
            if self.match_count == self.fail_on_match:
                raise self.exc
        super().write_frame(msg_type, message_id, payload, generation=generation)


class ScriptedFailBridge(FakeBridge):
    def __init__(self, path=Path("/dev/fake-runtime-bridge"), failures=None):
        super().__init__(path)
        self.failures = list(failures or [])
        self.match_counts = {}

    def write_frame(self, msg_type, message_id, payload, *, generation=0):
        count = self.match_counts.get(msg_type, 0) + 1
        self.match_counts[msg_type] = count
        for fail_type, fail_on_match, exc in self.failures:
            if msg_type == fail_type and count == fail_on_match:
                raise exc
        super().write_frame(msg_type, message_id, payload, generation=generation)


class QueuedBridge(FakeBridge):
    def __init__(self, path=Path("/dev/fake-runtime-bridge")):
        super().__init__(path)
        self.reads = queue.Queue()
        self.write_cond = threading.Condition()

    def close(self):
        super().close()
        self.reads.put(None)

    def read_frame(self):
        item = self.reads.get(timeout=2.0)
        if item is None:
            raise EOFError("bridge closed")
        return item

    def write_frame(self, msg_type, message_id, payload, *, generation=0):
        super().write_frame(msg_type, message_id, payload, generation=generation)
        with self.write_cond:
            self.write_cond.notify_all()

    def push_frame(self, msg_type, message_id, payload, *, generation=0):
        self.reads.put((msg_type, message_id, generation, payload))

    def wait_for_write(self, message_id, timeout=2.0):
        deadline = time.monotonic() + timeout
        with self.write_cond:
            while True:
                for item in self.writes:
                    if item[1] == message_id:
                        return item
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.write_cond.wait(timeout=remaining)


class ExplodingReadBridge(FakeBridge):
    def __init__(self, path=Path("/dev/fake-runtime-bridge"), *, exc=None):
        super().__init__(path)
        self.exc = exc or RuntimeError("simulated bridge read failure")
        self.raised = False

    def read_frame(self):
        if not self.raised:
            self.raised = True
            raise self.exc
        raise EOFError("bridge closed after failure")


class ReplyBusyBridge(FakeBridge):
    def __init__(
        self,
        path=Path("/dev/fake-runtime-bridge"),
        *,
        failures_before_success=0,
        fail_forever=False,
    ):
        super().__init__(path)
        self.failures_before_success = max(0, int(failures_before_success))
        self.fail_forever = bool(fail_forever)
        self.reply_failures = 0

    def write_frame(self, msg_type, message_id, payload, *, generation=0):
        if msg_type == sensoriumd.CMD_REPLY:
            if self.fail_forever or self.reply_failures < self.failures_before_success:
                self.reply_failures += 1
                raise BlockingIOError(errno.EBUSY, "simulated reply ring full")
        super().write_frame(msg_type, message_id, payload, generation=generation)


class FakeRpcManager:
    def __init__(self):
        self.stop_event = threading.Event()
        self.buses = {"bus-1": {"id": "bus-1"}}
        self.devices = {"dev-1": {"id": "dev-1"}}
        self.applied_models = []
        self.updated = []

    def status(self):
        return {"state": "ready", "applied_models": len(self.applied_models)}

    def health(self):
        return {"health": "ok"}

    def apply_model(self, model):
        self.applied_models.append(model)

    def reset_runtime(self):
        self.applied_models.clear()

    def resync_runtime(self):
        return {"state": "ready", "resynced": True}

    def _device_view(self, device):
        return {"id": device["id"]}

    def get_device(self, device_id):
        return {"id": device_id}

    def update_device(self, device_id, patch):
        self.updated.append((device_id, patch))
        return {"id": device_id, "patch": patch}

    def get_stats(self):
        return {"requests": 1}

    def get_trace(self, limit=32):
        return {"entries": [], "limit": limit}

    def list_backends(self):
        return {"backends": []}

    def attach_backend(self, backend_id, device_ids):
        return {"backend_id": backend_id, "device_ids": list(device_ids)}

    def detach_backend(self, backend_id, device_ids):
        return {"backend_id": backend_id, "device_ids": list(device_ids or [])}

    def next_event(self, backend_id, timeout):
        return {"backend_id": backend_id, "timeout": timeout}

    def reply_event(self, backend_id, request_id, status, data):
        return {
            "backend_id": backend_id,
            "request_id": request_id,
            "status": status,
            "data": data,
        }

    def heartbeat_backend(self, backend_id):
        return {"backend_id": backend_id, "ok": True}

    def inject_uart_rx(self, device_id, data):
        return {"device_id": device_id, "data": data}

    def set_uart_modem(self, device_id, signals):
        return {"device_id": device_id, "signals": signals}


def write_yaml(path: Path, content: str):
    path.write_text(content, encoding="utf-8")


def build_spi_payload(device, transfers, mode=0):
    payload = bytearray(
        ABI_SPI_REQ_PREFIX_STRUCT.pack(
            device["handle"],
            device["bus_handle"],
            mode,
            len(transfers),
            sum(item["len"] for item in transfers),
            device.get("chip_select", 0),
        )
    )
    for item in transfers:
        payload.extend(
            ABI_SPI_REQ_XFER_STRUCT.pack(
                item["len"],
                item.get("speed_hz", 500000),
                item.get("delay_usecs", 0),
                item.get("bits_per_word", 8),
                item.get("cs_change", 0),
                item.get("tx_nbits", 0),
                item.get("rx_nbits", 0),
                item.get("word_delay_usecs", 0),
                1 if item.get("has_tx", True) else 0,
                1 if item.get("has_rx", True) else 0,
            )
        )
    for item in transfers:
        payload.extend(bytes.fromhex(item["tx"]))
    return bytes(payload)


class RuntimeModelNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="sensorium-runtime-test-"))

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_shipped_runtime_model_normalizes(self):
        model = runtime_common.normalize_runtime_model(
            REPO_ROOT / "models" / "runtime" / "rpi-multibus.yaml"
        )

        self.assertEqual(model["adapter"], "runtime")
        self.assertEqual(model["schema_version"], runtime_common.RUNTIME_MODEL_SCHEMA_VERSION)
        self.assertEqual(len(model["runtime"]["buses"]), 3)
        self.assertEqual(len(model["runtime"]["devices"]), 6)
        spi_names = sorted(
            device["device_name"]
            for device in model["runtime"]["devices"]
            if device["transport"] == "spi"
        )
        uart_names = sorted(
            device["port_name"]
            for device in model["runtime"]["devices"]
            if device["transport"] == "uart"
        )
        self.assertEqual(spi_names, ["spidev0.0", "spidev0.1"])
        self.assertEqual(uart_names, ["ttyAMA0", "ttyAMA1"])
        flash_device = next(
            device for device in model["runtime"]["devices"] if device["id"] == "flash-spi"
        )
        console_uart = next(
            device for device in model["runtime"]["devices"] if device["id"] == "console-uart"
        )
        self.assertEqual(flash_device["backend"]["flash_jedec_id"], "ef4018")
        self.assertTrue(console_uart["backend"]["cts_follows_rts"])
        self.assertTrue(console_uart["backend"]["carrier_follows_dtr"])

    def test_runtime_model_normalizes_defaults_and_text_literals(self):
        path = self.tempdir / "runtime.yaml"
        write_yaml(
            path,
            """
name: demo
adapter: runtime
runtime:
  buses:
    - id: i2c-main
      transport: i2c
      name: i2c-7
    - id: spi-main
      transport: spi
      name: spi3
    - id: uart-main
      transport: uart
      name: uart0
  devices:
    - id: env0
      bus: i2c-main
      transport: i2c
      address: 0x20
      faults:
        mode: short-reply
        reply_data: "be"
      backend:
        kind: template
        template: i2c-register-bank
        size: 512
        pointer_width: 2
        registers:
          0x01: 0x11
    - id: flash0
      bus: spi-main
      transport: spi
      chip_select: 2
      settings:
        mode: 3
        bits_per_word: 16
        max_speed_hz: 2000000
      backend:
        kind: template
        template: spi-script
        responses:
          "text:ab": "text:cd"
        prefix_responses:
          "9f": "ef4018"
        echo: false
    - id: tty0
      bus: uart-main
      transport: uart
      device_name: ttyAMA4
      settings:
        baud_rate: 57600
        data_bits: 7
        parity: even
        stop_bits: 2
        xonxoff: true
      backend:
        kind: template
        template: uart-script
        echo: true
        binary_responses:
          "text:\\x01\\x02": "aabb"
        line_responses:
          "PING": "PONG\\r\\n"
""",
        )

        model = runtime_common.normalize_runtime_model(path)

        spi = next(device for device in model["runtime"]["devices"] if device["transport"] == "spi")
        uart = next(device for device in model["runtime"]["devices"] if device["transport"] == "uart")
        i2c = next(device for device in model["runtime"]["devices"] if device["transport"] == "i2c")
        self.assertEqual(spi["device_name"], "spidev3.2")
        self.assertEqual(spi["backend"]["responses"]["6162"], "6364")
        self.assertEqual(spi["backend"]["prefix_responses"]["9f"], "ef4018")
        self.assertEqual(spi["settings"]["bits_per_word"], 16)
        self.assertEqual(i2c["backend"]["size"], 512)
        self.assertEqual(i2c["backend"]["pointer_width"], 2)
        self.assertEqual(i2c["faults"]["mode"], "short-reply")
        self.assertEqual(uart["port_name"], "ttyAMA4")
        self.assertEqual(uart["settings"]["baud_rate"], 57600)
        self.assertEqual(uart["settings"]["parity"], "even")

    def test_runtime_model_rejects_unknown_bus(self):
        path = self.tempdir / "bad-runtime.yaml"
        write_yaml(
            path,
            """
name: bad
adapter: runtime
runtime:
  buses:
    - id: i2c-main
      transport: i2c
      name: i2c-1
  devices:
    - id: env0
      bus: missing
      transport: i2c
      address: 0x10
      backend:
        kind: controller
""",
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "unknown bus"):
            runtime_common.normalize_runtime_model(path)

    def test_runtime_model_rejects_device_transport_mismatch_with_bus(self):
        path = self.tempdir / "bad-transport-mismatch.yaml"
        write_yaml(
            path,
            """
name: bad
adapter: runtime
runtime:
  buses:
    - id: i2c-main
      transport: i2c
      name: i2c-1
  devices:
    - id: flash0
      bus: i2c-main
      transport: spi
      chip_select: 0
      device_name: spidev0.0
      backend:
        kind: template
        template: spi-script
""",
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "does not match bus"):
            runtime_common.normalize_runtime_model(path)

    def test_runtime_model_rejects_invalid_i2c_address(self):
        path = self.tempdir / "bad-address.yaml"
        write_yaml(
            path,
            """
name: bad
adapter: runtime
runtime:
  buses:
    - id: i2c-main
      transport: i2c
      name: i2c-1
  devices:
    - id: env0
      bus: i2c-main
      transport: i2c
      address: 0x80
      backend:
        kind: controller
""",
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "0x7F|<= 127"):
            runtime_common.normalize_runtime_model(path)

    def test_runtime_model_rejects_duplicate_transport_locations(self):
        path = self.tempdir / "duplicate-locations.yaml"
        write_yaml(
            path,
            """
name: bad
adapter: runtime
runtime:
  buses:
    - id: i2c-main
      transport: i2c
      name: i2c-1
    - id: spi-main
      transport: spi
      name: spi0
    - id: uart-main
      transport: uart
      name: uart0
  devices:
    - id: env0
      bus: i2c-main
      transport: i2c
      address: 0x20
      backend:
        kind: template
        template: i2c-register-bank
    - id: env1
      bus: i2c-main
      transport: i2c
      address: 0x20
      backend:
        kind: template
        template: i2c-register-bank
""",
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "duplicate i2c address"):
            runtime_common.normalize_runtime_model(path)

        write_yaml(
            path,
            """
name: bad
adapter: runtime
runtime:
  buses:
    - id: spi-main
      transport: spi
      name: spi0
  devices:
    - id: flash0
      bus: spi-main
      transport: spi
      chip_select: 0
      device_name: spidev0.0
      backend:
        kind: template
        template: spi-script
    - id: flash1
      bus: spi-main
      transport: spi
      chip_select: 0
      device_name: spidev0.0
      backend:
        kind: template
        template: spi-script
""",
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "duplicate spi chip_select"):
            runtime_common.normalize_runtime_model(path)

        write_yaml(
            path,
            """
name: bad
adapter: runtime
runtime:
  buses:
    - id: uart-main
      transport: uart
      name: uart0
  devices:
    - id: console0
      bus: uart-main
      transport: uart
      port_name: ttyAMA0
      backend:
        kind: template
        template: uart-script
    - id: console1
      bus: uart-main
      transport: uart
      port_name: ttyAMA0
      backend:
        kind: template
        template: uart-script
""",
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "duplicate uart port_name"):
            runtime_common.normalize_runtime_model(path)

    def test_runtime_model_rejects_invalid_transport_specific_names(self):
        path = self.tempdir / "bad-names.yaml"
        write_yaml(
            path,
            """
name: bad
adapter: runtime
runtime:
  buses:
    - id: i2c-main
      transport: i2c
      name: i2c-01
  devices: []
""",
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "canonical i2c-N"):
            runtime_common.normalize_runtime_model(path)

        write_yaml(
            path,
            """
name: bad
adapter: runtime
runtime:
  buses:
    - id: spi-main
      transport: spi
      name: foo1
  devices: []
""",
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "canonical spiN"):
            runtime_common.normalize_runtime_model(path)

        write_yaml(
            path,
            """
name: bad
adapter: runtime
runtime:
  buses:
    - id: uart-main
      transport: uart
      name: uart0
  devices:
    - id: console
      bus: uart-main
      transport: uart
      port_name: ttyAMA01
      backend:
        kind: template
        template: uart-script
""",
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "canonical numeric suffix"):
            runtime_common.normalize_runtime_model(path)

    def test_runtime_model_rejects_spi_device_name_mismatch(self):
        path = self.tempdir / "bad-spi-device-name.yaml"
        write_yaml(
            path,
            """
name: bad
adapter: runtime
runtime:
  buses:
    - id: spi-main
      transport: spi
      name: spi3
  devices:
    - id: flash0
      bus: spi-main
      transport: spi
      chip_select: 0
      device_name: spidev0.0
      backend:
        kind: template
        template: spi-script
""",
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "must match 'spidev3.0'"):
            runtime_common.normalize_runtime_model(path)

    def test_runtime_model_rejects_duplicate_bus_names(self):
        path = self.tempdir / "duplicate-buses.yaml"
        write_yaml(
            path,
            """
name: bad
adapter: runtime
runtime:
  buses:
    - id: i2c-main-a
      transport: i2c
      name: i2c-1
    - id: i2c-main-b
      transport: i2c
      name: i2c-1
  devices: []
""",
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "duplicate i2c bus name"):
            runtime_common.normalize_runtime_model(path)

    def test_runtime_model_accepts_high_uart_index(self):
        path = self.tempdir / "high-uart.yaml"
        write_yaml(
            path,
            """
name: high-uart
adapter: runtime
runtime:
  buses:
    - id: uart-main
      transport: uart
      name: uart0
  devices:
    - id: console
      bus: uart-main
      transport: uart
      port_name: ttyAMA511
      backend:
        kind: template
        template: uart-script
        line_responses:
          "ATI": "OK\\r\\n"
""",
        )

        model = runtime_common.normalize_runtime_model(path)
        device = model["runtime"]["devices"][0]
        self.assertEqual(device["port_name"], "ttyAMA511")
        self.assertEqual(device["settings"]["baud_rate"], 115200)
        self.assertEqual(device["backend"]["line_responses"]["ATI"], "OK\r\n")


class RuntimeTemplateTests(unittest.TestCase):
    def test_i2c_register_bank_handles_write_and_sequential_read(self):
        template = sensoriumd.I2CRegisterBankTemplate(
            {"registers": {"0x10": 0x22, "0x11": 0x33}}
        )

        response = template.handle_i2c(
            [
                {"addr": 0x76, "flags": 0, "len": 3, "data": bytes([0x10, 0xAA, 0xBB])},
                {"addr": 0x76, "flags": 0, "len": 1, "data": bytes([0x10])},
                {"addr": 0x76, "flags": 1, "len": 2, "data": b""},
            ]
        )

        self.assertEqual(response, bytes([0xAA, 0xBB]))
        self.assertEqual(template.registers[0x10], 0xAA)
        self.assertEqual(template.registers[0x11], 0xBB)

    def test_i2c_register_bank_supports_16bit_pointers(self):
        template = sensoriumd.I2CRegisterBankTemplate(
            {
                "size": 512,
                "pointer_width": 2,
                "auto_increment": True,
                "registers": {"0x0100": 0xDE, "0x0101": 0xAD},
            }
        )

        response = template.handle_i2c(
            [
                {"addr": 0x44, "flags": 0, "len": 2, "data": bytes([0x01, 0x00])},
                {"addr": 0x44, "flags": 1, "len": 2, "data": b""},
            ]
        )
        self.assertEqual(response, bytes.fromhex("dead"))

    def test_i2c_register_bank_supports_clear_on_read_and_write_effects(self):
        template = sensoriumd.I2CRegisterBankTemplate(
            {
                "registers": {"0x10": 0x22, "0x20": 0x34},
                "clear_on_read": ["0x20"],
                "write_effects": {"0x10": {"0x21": 0x99}},
            }
        )

        response = template.handle_i2c(
            [
                {"addr": 0x76, "flags": 0, "len": 2, "data": bytes([0x10, 0xAA])},
                {"addr": 0x76, "flags": 0, "len": 1, "data": bytes([0x20])},
                {"addr": 0x76, "flags": 1, "len": 1, "data": b""},
                {"addr": 0x76, "flags": 0, "len": 1, "data": bytes([0x20])},
                {"addr": 0x76, "flags": 1, "len": 1, "data": b""},
            ]
        )

        self.assertEqual(response, bytes([0x34, 0x00]))
        self.assertEqual(template.registers[0x10], 0xAA)
        self.assertEqual(template.registers[0x21], 0x99)

    def test_spi_script_template_prefers_exact_response_then_echo_or_default(self):
        template = sensoriumd.SPIScriptTemplate(
            {
                "responses": {"9f0000": "ef4018"},
                "default_response": "0102",
                "echo": False,
            }
        )
        echo_template = sensoriumd.SPIScriptTemplate({"responses": {}, "default_response": "", "echo": True})

        self.assertEqual(template.handle_spi(bytes.fromhex("9f0000"), 3), bytes.fromhex("ef4018"))
        self.assertEqual(template.handle_spi(bytes.fromhex("aa55"), 4), bytes.fromhex("01020000"))
        self.assertEqual(echo_template.handle_spi(bytes.fromhex("aa55"), 2), bytes.fromhex("aa55"))

    def test_spi_script_template_supports_prefix_matches(self):
        template = sensoriumd.SPIScriptTemplate(
            {
                "responses": {},
                "prefix_responses": {"9f": "ef4018"},
                "default_response": "",
                "echo": False,
            }
        )
        self.assertEqual(template.handle_spi(bytes.fromhex("9f0000"), 3), bytes.fromhex("ef4018"))

    def test_spi_script_template_supports_stateful_flash_helpers(self):
        template = sensoriumd.SPIScriptTemplate(
            {
                "flash_jedec_id": "ef4018",
                "flash_status_register": 0x1C,
                "flash_write_busy_cycles": 2,
                "default_response": "",
                "echo": True,
            }
        )

        self.assertEqual(template.handle_spi(bytes.fromhex("9f0000"), 3), bytes.fromhex("ef4018"))
        self.assertEqual(template.handle_spi(bytes.fromhex("06"), 1), b"\x00")
        self.assertEqual(template.handle_spi(bytes.fromhex("05"), 1), b"\x1e")
        self.assertEqual(template.handle_spi(bytes.fromhex("011c"), 2), b"\x00\x00")
        self.assertEqual(template.handle_spi(bytes.fromhex("05"), 1), b"\x1d")
        self.assertEqual(template.handle_spi(bytes.fromhex("05"), 1), b"\x1d")
        self.assertEqual(template.handle_spi(bytes.fromhex("05"), 1), b"\x1c")

    def test_uart_script_template_supports_echo_line_responses_and_modem_defaults(self):
        template = sensoriumd.UARTScriptTemplate(
            {
                "echo": True,
                "line_responses": {"AT": "OK\r\n"},
                "control_defaults": {"cts": True, "dsr": True, "ri": False},
            }
        )

        self.assertEqual(template.handle_uart(b"AT\r\n"), b"AT\r\nOK\r\n")
        mask, values = template.modem_defaults()
        self.assertTrue(mask & sensoriumd.UART_MODEM_BITS["cts"])
        self.assertTrue(values & sensoriumd.UART_MODEM_BITS["cts"])
        self.assertTrue(mask & sensoriumd.UART_MODEM_BITS["dsr"])
        self.assertFalse(values & sensoriumd.UART_MODEM_BITS["ri"])

    def test_uart_script_template_supports_binary_and_default_responses(self):
        template = sensoriumd.UARTScriptTemplate(
            {
                "echo": False,
                "binary_responses": {"0102": "aabb"},
                "default_response": "cc",
            }
        )
        self.assertEqual(template.handle_uart(bytes.fromhex("0102")), bytes.fromhex("aabb"))
        self.assertEqual(template.handle_uart(b"\xff"), bytes.fromhex("cc"))

    def test_uart_script_template_can_follow_rts_and_dtr(self):
        template = sensoriumd.UARTScriptTemplate(
            {
                "echo": False,
                "cts_follows_rts": True,
                "carrier_follows_dtr": True,
            }
        )

        reply, updates = template.handle_uart_control(
            sensoriumd.UART_MODEM_BITS["rts"] | sensoriumd.UART_MODEM_BITS["dtr"],
            sensoriumd.UART_MODEM_BITS["rts"],
        )
        self.assertEqual(reply, b"")
        self.assertEqual(
            updates,
            {"cd": False, "cts": True, "dsr": False},
        )


class RuntimeManagerTests(unittest.TestCase):
    def setUp(self):
        self.model = runtime_common.normalize_runtime_model(
            REPO_ROOT / "models" / "runtime" / "rpi-multibus.yaml"
        )
        self.manager = sensoriumd.RuntimeManager(Path("/dev/fake-runtime-bridge"), **TEST_MANAGER_KWARGS)
        self.manager.bridge = FakeBridge(self.manager.bridge.path)

    def _decode_bus_add(self, payload):
        handle, transport, index, raw_name = sensoriumd.BUS_CMD_STRUCT.unpack(payload)
        return handle, transport, index, sensoriumd.decode_c_string(raw_name)

    def _decode_device_add(self, payload):
        (
            handle,
            transport,
            bus_handle,
            location,
            flags,
            max_speed_hz,
            spi_mode,
            spi_bits_per_word,
            raw_name,
        ) = sensoriumd.DEVICE_CMD_STRUCT.unpack(payload)
        return (
            handle,
            transport,
            bus_handle,
            location,
            flags,
            sensoriumd.decode_c_string(raw_name),
            max_speed_hz,
            spi_mode,
            spi_bits_per_word,
        )

    def test_apply_model_emits_bridge_commands_and_tracks_inventory(self):
        self.manager.persistence["last_snapshot_error"] = "stale startup error"
        self.manager.apply_model(self.model)

        writes = self.manager.bridge.writes
        self.assertEqual(writes[0][0], sensoriumd.CMD_RESET)
        self.assertEqual(len([item for item in writes if item[0] == sensoriumd.CMD_BUS_ADD]), 3)
        self.assertEqual(len([item for item in writes if item[0] == sensoriumd.CMD_DEVICE_ADD]), 6)
        self.assertTrue(any(item[0] == sensoriumd.CMD_UART_SET_MODEM for item in writes))

        bus_write = next(item for item in writes if item[0] == sensoriumd.CMD_BUS_ADD)
        _, transport, _, name = self._decode_bus_add(bus_write[3])
        self.assertEqual(transport, sensoriumd.TRANSPORT_IDS["i2c"])
        self.assertEqual(name, "i2c-1")

        spi_write = next(
            item for item in writes if item[0] == sensoriumd.CMD_DEVICE_ADD and self._decode_device_add(item[3])[5] == "spidev0.0"
        )
        _, transport, _, location, _, node_name, max_speed_hz, spi_mode, spi_bits_per_word = self._decode_device_add(spi_write[3])
        self.assertEqual(transport, sensoriumd.TRANSPORT_IDS["spi"])
        self.assertEqual(location, 0)
        self.assertEqual(node_name, "spidev0.0")
        self.assertEqual(max_speed_hz, 500000)
        self.assertEqual(spi_mode, 0)
        self.assertEqual(spi_bits_per_word, 8)

        aux_spi_write = next(
            item
            for item in writes
            if item[0] == sensoriumd.CMD_DEVICE_ADD and self._decode_device_add(item[3])[5] == "spidev0.1"
        )
        _, transport, _, location, _, node_name, max_speed_hz, spi_mode, spi_bits_per_word = self._decode_device_add(aux_spi_write[3])
        self.assertEqual(transport, sensoriumd.TRANSPORT_IDS["spi"])
        self.assertEqual(location, 1)
        self.assertEqual(node_name, "spidev0.1")
        self.assertEqual(max_speed_hz, 1000000)
        self.assertEqual(spi_mode, 3)
        self.assertEqual(spi_bits_per_word, 8)

        aux_uart_write = next(
            item
            for item in writes
            if item[0] == sensoriumd.CMD_DEVICE_ADD and self._decode_device_add(item[3])[5] == "ttyAMA1"
        )
        _, transport, _, location, _, node_name, _, _, _ = self._decode_device_add(aux_uart_write[3])
        self.assertEqual(transport, sensoriumd.TRANSPORT_IDS["uart"])
        self.assertEqual(location, 1)
        self.assertEqual(node_name, "ttyAMA1")

        status = self.manager.status()
        self.assertEqual(status["model"], "rpi-multibus")
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["generation"], 1)
        self.assertEqual(status["schema_version"], runtime_common.RUNTIME_MODEL_SCHEMA_VERSION)
        self.assertEqual(status["bus_count"], 3)
        self.assertEqual(status["device_count"], 6)
        self.assertEqual(len(self.manager.devices_by_handle), 6)
        console_uart = self.manager.devices["console-uart"]
        self.assertIs(
            self.manager.devices_by_handle[console_uart["handle"]],
            console_uart,
        )
        self.assertIn("bridge_runtime", status)
        self.assertEqual(status["health"]["status"], sensoriumd.HEALTH_OK)
        self.assertEqual(status["bridge_runtime"]["kernel_timeout_ms"], sensoriumd.DEFAULT_KERNEL_TIMEOUT_MS)
        self.assertEqual(status["bridge_runtime"]["bridge_abi"], runtime_common.RUNTIME_BRIDGE_ABI_VERSION)
        self.assertIn("session_id", status["bridge_runtime"])
        self.assertIn("queue_depths", status)
        self.assertIn("rpc", status)
        self.assertEqual(
            status["bridge_runtime"]["controller_timeout_ms"],
            sensoriumd.DEFAULT_KERNEL_TIMEOUT_MS - sensoriumd.DEFAULT_CONTROLLER_TIMEOUT_MARGIN_MS,
        )
        self.assertIsNone(status["persistence"]["last_snapshot_error"])

    def test_start_records_bridge_session_id_in_status(self):
        self.manager.start()
        try:
            status = self.manager.status()
        finally:
            self.manager.shutdown()

        self.assertEqual(status["bridge_runtime"]["session_id"], 1)
        self.assertEqual(status["bridge_runtime"]["bridge_abi"], runtime_common.RUNTIME_BRIDGE_ABI_VERSION)

    def test_bridge_read_failure_marks_runtime_desynced(self):
        self.manager.bridge = ExplodingReadBridge(self.manager.bridge.path)
        self.manager.start()
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                health = self.manager.health()
                if health["health"]["status"] == sensoriumd.HEALTH_ERROR:
                    break
                time.sleep(0.02)
            status = self.manager.status()
        finally:
            self.manager.shutdown()

        self.assertEqual(status["state"], "desynced")
        self.assertIn("bridge read failed unexpectedly", status["desync_reason"])
        self.assertEqual(status["health"]["status"], sensoriumd.HEALTH_ERROR)
        self.assertGreaterEqual(status["bridge_runtime"]["bridge_errors"], 1)

    def test_bridge_os_error_marks_runtime_desynced(self):
        self.manager.bridge = ExplodingReadBridge(
            self.manager.bridge.path,
            exc=OSError(errno.EIO, "simulated bridge os error"),
        )
        self.manager.start()
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                status = self.manager.status()
                if status["state"] == "desynced":
                    break
                time.sleep(0.02)
        finally:
            self.manager.shutdown()

        self.assertEqual(status["state"], "desynced")
        self.assertIn("bridge read failed with OS error", status["desync_reason"])

    def test_reply_ring_backpressure_is_retried(self):
        self.manager.bridge = ReplyBusyBridge(
            self.manager.bridge.path,
            failures_before_success=1,
        )
        self.manager.apply_model(self.model)
        i2c_device = self.manager.devices["env-bme280"]
        payload = (
            ABI_I2C_REQ_PREFIX_STRUCT.pack(i2c_device["handle"], i2c_device["bus_handle"], 2, 1)
            + ABI_I2C_REQ_MSG_STRUCT.pack(0x76, 0, 1, 0)
            + ABI_I2C_REQ_MSG_STRUCT.pack(0x76, 1, 1, 0)
            + bytes([0x10])
        )

        worker = threading.Thread(target=self.manager._bridge_worker_loop, daemon=True)
        worker.start()
        try:
            self.manager.request_queue.put(
                (sensoriumd.REQ_I2C_XFER, 123, self.manager.generation, payload, time.monotonic())
            )
            deadline = time.monotonic() + 2.0
            reply_write = None
            while time.monotonic() < deadline:
                reply_write = next(
                    (
                        item
                        for item in self.manager.bridge.writes
                        if item[0] == sensoriumd.CMD_REPLY and item[1] == 123
                    ),
                    None,
                )
                if reply_write is not None:
                    break
                time.sleep(0.02)
        finally:
            self.manager.stop_event.set()
            self.manager.request_queue.put(None)
            worker.join(timeout=1.0)

        self.assertIsNotNone(reply_write)
        reply_payload = reply_write[3]
        status_code, payload_len = sensoriumd.REPLY_PREFIX_STRUCT.unpack_from(reply_payload, 0)
        self.assertEqual(status_code, 0)
        self.assertEqual(payload_len, 1)
        self.assertEqual(reply_payload[sensoriumd.REPLY_PREFIX_STRUCT.size :], bytes([0x23]))
        self.assertEqual(self.manager.status()["state"], "ready")
        self.assertEqual(self.manager.bridge.reply_failures, 1)

    def test_reply_ring_backpressure_timeout_marks_runtime_desynced(self):
        self.manager.bridge = ReplyBusyBridge(
            self.manager.bridge.path,
            fail_forever=True,
        )
        self.manager.kernel_timeout_ms = 25
        self.manager.apply_model(self.model)
        i2c_device = self.manager.devices["env-bme280"]
        payload = (
            ABI_I2C_REQ_PREFIX_STRUCT.pack(i2c_device["handle"], i2c_device["bus_handle"], 2, 1)
            + ABI_I2C_REQ_MSG_STRUCT.pack(0x76, 0, 1, 0)
            + ABI_I2C_REQ_MSG_STRUCT.pack(0x76, 1, 1, 0)
            + bytes([0x10])
        )

        worker = threading.Thread(target=self.manager._bridge_worker_loop, daemon=True)
        worker.start()
        try:
            self.manager.request_queue.put(
                (sensoriumd.REQ_I2C_XFER, 124, self.manager.generation, payload, time.monotonic())
            )
            deadline = time.monotonic() + 2.0
            status = None
            while time.monotonic() < deadline:
                status = self.manager.status()
                if status["state"] == "desynced":
                    break
                time.sleep(0.02)
        finally:
            self.manager.stop_event.set()
            self.manager.request_queue.put(None)
            worker.join(timeout=1.0)

        self.assertIsNotNone(status)
        self.assertEqual(status["state"], "desynced")
        self.assertIn("reply ring capacity", status["desync_reason"])
        self.assertGreaterEqual(status["bridge_runtime"]["bridge_errors"], 1)

    def test_apply_model_rejects_invalid_model_without_resetting_runtime(self):
        self.manager.apply_model(self.model)
        writes_before = list(self.manager.bridge.writes)

        bad_model = json.loads(json.dumps(self.model))
        bad_model["runtime"]["devices"].append(
            {
                "id": "flash-spi-dup",
                "bus": "spi-main",
                "transport": "spi",
                "chip_select": 0,
                "device_name": "spidev0.0",
                "backend": {"kind": "template", "template": "spi-script"},
                "metadata": {},
                "faults": {"mode": "none", "remaining": 0},
                "settings": {"mode": 0, "bits_per_word": 8, "max_speed_hz": 500000},
            }
        )

        with self.assertRaisesRegex(runtime_common.RuntimeModelError, "duplicate spi chip_select"):
            self.manager.apply_model(bad_model)

        self.assertEqual(self.manager.model_name, "rpi-multibus")
        self.assertEqual(len(self.manager.buses), 3)
        self.assertEqual(len(self.manager.devices), 6)
        self.assertEqual(self.manager.bridge.writes, writes_before)

    def test_apply_model_clears_partial_state_after_late_bridge_failure(self):
        self.manager.bridge = FailingBridge(
            self.manager.bridge.path,
            fail_type=sensoriumd.CMD_DEVICE_ADD,
            fail_on_match=2,
            exc=RuntimeError("simulated late device add failure"),
        )

        with self.assertRaisesRegex(RuntimeError, "simulated late device add failure"):
            self.manager.apply_model(self.model)

        status = self.manager.status()
        self.assertIsNone(self.manager.model_name)
        self.assertEqual(status["state"], "empty")
        self.assertEqual(status["bus_count"], 0)
        self.assertEqual(status["device_count"], 0)
        self.assertEqual(self.manager.bridge.writes[0][0], sensoriumd.CMD_RESET)
        self.assertEqual(self.manager.bridge.writes[-1][0], sensoriumd.CMD_RESET)

    def test_apply_model_preserves_partial_state_if_cleanup_reset_fails(self):
        self.manager.bridge = ScriptedFailBridge(
            self.manager.bridge.path,
            failures=[
                (
                    sensoriumd.CMD_DEVICE_ADD,
                    2,
                    RuntimeError("simulated late device add failure"),
                ),
                (
                    sensoriumd.CMD_RESET,
                    2,
                    RuntimeError("simulated cleanup reset failure"),
                ),
            ],
        )

        with self.assertRaisesRegex(
            RuntimeError, "failed to reset runtime after apply failure"
        ):
            self.manager.apply_model(self.model)

        status = self.manager.status()
        self.assertEqual(self.manager.model_name, "rpi-multibus")
        self.assertEqual(status["state"], "desynced")
        self.assertIn("cleanup reset failure", status["desync_reason"])
        self.assertEqual(status["health"]["status"], sensoriumd.HEALTH_ERROR)
        self.assertEqual(status["bus_count"], 3)
        self.assertEqual(status["device_count"], 1)
        self.assertEqual(self.manager.bridge.writes[0][0], sensoriumd.CMD_RESET)
        self.assertEqual(
            len([item for item in self.manager.bridge.writes if item[0] == sensoriumd.CMD_DEVICE_ADD]),
            1,
        )

    def test_runtime_resync_recovers_from_desynced_partial_state(self):
        self.manager.bridge = ScriptedFailBridge(
            self.manager.bridge.path,
            failures=[
                (
                    sensoriumd.CMD_DEVICE_ADD,
                    2,
                    RuntimeError("simulated late device add failure"),
                ),
                (
                    sensoriumd.CMD_RESET,
                    2,
                    RuntimeError("simulated cleanup reset failure"),
                ),
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "cleanup reset failure"):
            self.manager.apply_model(self.model)

        repaired_bridge = FakeBridge(self.manager.bridge.path)
        self.manager.bridge = repaired_bridge
        result = self.manager.resync_runtime()

        self.assertTrue(result["ok"])
        self.assertEqual(self.manager.status()["state"], "ready")
        self.assertEqual(self.manager.status()["device_count"], 1)

    def test_desynced_runtime_blocks_mutating_operations_until_resync(self):
        self.manager.apply_model(self.model)
        with self.manager.lock:
            self.manager._set_runtime_state_locked("desynced", reason="simulated desync")

        with self.assertRaisesRegex(RuntimeError, "desynced"):
            self.manager.reset_runtime()
        with self.assertRaisesRegex(RuntimeError, "desynced"):
            self.manager.update_device("console-uart", {"metadata": {"x": 1}})
        with self.assertRaisesRegex(RuntimeError, "desynced"):
            self.manager.inject_uart_rx("console-uart", "41")

    def test_template_requests_are_answered_locally(self):
        self.manager.apply_model(self.model)
        i2c_device = self.manager.devices["env-bme280"]
        spi_device = self.manager.devices["flash-spi"]
        uart_device = self.manager.devices["console-uart"]

        i2c_payload = (
            ABI_I2C_REQ_PREFIX_STRUCT.pack(i2c_device["handle"], i2c_device["bus_handle"], 2, 1)
            + ABI_I2C_REQ_MSG_STRUCT.pack(0x76, 0, 1, 0)
            + ABI_I2C_REQ_MSG_STRUCT.pack(0x76, 1, 1, 0)
            + bytes([0x10])
        )
        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_I2C_XFER, 10, i2c_payload)
        self.assertEqual(status, 0)
        self.assertEqual(data, bytes([0x23]))

        spi_payload = build_spi_payload(
            spi_device,
            [{"len": 3, "speed_hz": 500000, "bits_per_word": 8, "tx": "9f0000"}],
        )
        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_SPI_XFER, 11, spi_payload)
        self.assertEqual(status, 0)
        self.assertEqual(data, bytes.fromhex("ef4018"))

        uart_payload = ABI_UART_REQ_PREFIX_STRUCT.pack(
            uart_device["handle"], 0, 4, 0, 0
        ) + b"AT\r\n"
        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_UART_TX, 12, uart_payload)
        self.assertEqual(status, 0)
        self.assertEqual(data, b"AT\r\nOK\r\n")

    def test_i2c_request_supports_more_than_legacy_message_limit(self):
        self.manager.apply_model(self.model)
        i2c_device = self.manager.devices["env-bme280"]

        descs = []
        tx_chunks = []
        expected = bytearray()
        registers = [0x10, 0x20] * 5
        expected_values = [0x23, 0x34] * 5
        for register in registers:
            descs.append(ABI_I2C_REQ_MSG_STRUCT.pack(0x76, 0, 1, 0))
            tx_chunks.append(bytes([register]))
            descs.append(ABI_I2C_REQ_MSG_STRUCT.pack(0x76, 1, 1, 0))
        for value in expected_values:
            expected.append(value)
        payload = (
            ABI_I2C_REQ_PREFIX_STRUCT.pack(
                i2c_device["handle"],
                i2c_device["bus_handle"],
                len(registers) * 2,
                sum(len(chunk) for chunk in tx_chunks),
            )
            + b"".join(descs)
            + b"".join(tx_chunks)
        )

        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_I2C_XFER, 13, payload)

        self.assertEqual(status, 0)
        self.assertEqual(data, bytes(expected))

    def test_spi_request_supports_more_than_legacy_transfer_limit(self):
        self.manager.apply_model(self.model)
        spi_device = next(
            device
            for device in self.manager.devices.values()
            if (
                device["transport"] == "spi"
                and device["backend"].get("echo")
                and not device["backend"].get("flash_jedec_id")
            )
        )

        transfers = [
            {"len": 1, "speed_hz": 500000 + index, "bits_per_word": 8, "tx": f"{index % 256:02x}"}
            for index in range(20)
        ]
        payload = build_spi_payload(spi_device, transfers)

        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_SPI_XFER, 14, payload)

        self.assertEqual(status, 0)
        self.assertEqual(data, bytes(index % 256 for index in range(20)))

    def test_apply_model_preserves_high_uart_index_location(self):
        model = {
            "name": "high-uart",
            "adapter": "runtime",
            "runtime": {
                "buses": [{"id": "uart-main", "transport": "uart", "name": "uart0"}],
                "devices": [
                    {
                        "id": "console-high",
                        "bus": "uart-main",
                        "transport": "uart",
                        "port_name": "ttyAMA511",
                        "settings": {
                            "baud_rate": 115200,
                            "data_bits": 8,
                            "parity": "none",
                            "stop_bits": 1,
                            "xonxoff": False,
                            "rtscts": False,
                        },
                        "backend": {
                            "kind": "template",
                            "template": "uart-script",
                            "echo": True,
                            "line_responses": {"ATI": "HIGH\r\n"},
                            "binary_responses": {},
                            "default_response": "",
                            "control_defaults": {},
                        },
                        "metadata": {},
                        "faults": {"mode": "none", "remaining": 0},
                    }
                ],
            },
        }

        self.manager.apply_model(model)

        uart_write = next(
            item
            for item in self.manager.bridge.writes
            if item[0] == sensoriumd.CMD_DEVICE_ADD
            and self._decode_device_add(item[3])[5] == "ttyAMA511"
        )
        _, transport, _, location, _, node_name, _, _, _ = self._decode_device_add(uart_write[3])
        self.assertEqual(transport, sensoriumd.TRANSPORT_IDS["uart"])
        self.assertEqual(location, 511)
        self.assertEqual(node_name, "ttyAMA511")

    def test_controller_backend_receives_event_and_can_reply(self):
        controller_model = {
            "name": "controller-demo",
            "adapter": "runtime",
            "runtime": {
                "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                "devices": [
                    {
                        "id": "controller-spi",
                        "bus": "spi-main",
                        "transport": "spi",
                        "chip_select": 1,
                        "device_name": "spidev0.1",
                        "backend": {"kind": "controller"},
                        "metadata": {},
                    }
                ],
            },
        }
        self.manager.apply_model(controller_model)
        self.manager.attach_backend("backend-a", ["controller-spi"])
        device = self.manager.devices["controller-spi"]

        results = {}

        def issue_request():
            payload = build_spi_payload(
                device,
                [
                    {
                        "len": 2,
                        "speed_hz": 1000000,
                        "delay_usecs": 7,
                        "bits_per_word": 8,
                        "cs_change": 1,
                        "tx_nbits": 1,
                        "rx_nbits": 1,
                        "word_delay_usecs": 3,
                        "tx": "a55a",
                    }
                ],
            )
            results["response"] = self.manager._handle_bridge_request(
                sensoriumd.REQ_SPI_XFER, 77, payload
            )

        worker = threading.Thread(target=issue_request)
        worker.start()

        event = self.manager.next_event("backend-a", timeout=1.0)
        self.assertIsNotNone(event)
        self.assertEqual(event["device_id"], "controller-spi")
        self.assertEqual(event["tx"], "a55a")
        self.assertEqual(event["transfers"][0]["delay_usecs"], 7)
        self.assertEqual(event["transfers"][0]["word_delay_usecs"], 3)
        self.assertEqual(event["transfers"][0]["cs_change"], 1)

        self.manager.reply_event("backend-a", event["request_id"], 0, "beef")
        worker.join(timeout=2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results["response"], (0, bytes.fromhex("beef")))

    def test_managed_controller_worker_receives_event_and_can_reply(self):
        controller_model = {
            "name": "managed-controller-demo",
            "adapter": "runtime",
            "runtime": {
                "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                "devices": [
                    {
                        "id": "controller-spi",
                        "bus": "spi-main",
                        "transport": "spi",
                        "chip_select": 1,
                        "device_name": "spidev0.1",
                        "backend": {
                            "kind": "controller",
                            "worker": {
                                "command": [
                                    sys.executable,
                                    str(RUNTIME_SCRIPTS_DIR / "runtime-controller-spi-flash.py"),
                                ],
                                "env": {"PYTHONPATH": str(SRC_DIR)},
                                "restart_limit": 1,
                                "restart_backoff_ms": 10,
                            },
                        },
                        "metadata": {},
                    }
                ],
            },
        }
        self.manager.apply_model(controller_model)
        device = self.manager.devices["controller-spi"]
        self.assertEqual(device["managed_worker"]["status"], "running")
        self.assertTrue(device["managed_worker"]["log_path"].endswith("controller-spi.log"))
        self.assertTrue(Path(device["managed_worker"]["log_path"]).exists())

        payload = build_spi_payload(
            device,
            [{"len": 2, "speed_hz": 1000000, "bits_per_word": 8, "tx": "9f00"}],
        )
        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_SPI_XFER, 88, payload)
        self.assertEqual(status, 0)
        self.assertEqual(data, bytes.fromhex("ef4018"))

    def test_managed_controller_worker_crash_updates_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            worker_script = Path(tmpdir) / "crash-worker.py"
            worker_script.write_text(
                "import os\nos._exit(7)\n",
                encoding="utf-8",
            )
            controller_model = {
                "name": "managed-controller-crash",
                "adapter": "runtime",
                "runtime": {
                    "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                    "devices": [
                        {
                            "id": "controller-spi",
                            "bus": "spi-main",
                            "transport": "spi",
                            "chip_select": 1,
                            "device_name": "spidev0.1",
                            "backend": {
                                "kind": "controller",
                                "worker": {
                                    "command": [sys.executable, str(worker_script)],
                                    "restart_limit": 0,
                                    "restart_backoff_ms": 0,
                                },
                            },
                            "metadata": {},
                        }
                    ],
                },
            }
            self.manager.apply_model(controller_model)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                device = self.manager.devices["controller-spi"]
                if device.get("managed_worker", {}).get("status") == "failed":
                    break
                time.sleep(0.02)
            device = self.manager.devices["controller-spi"]
            self.assertEqual(device["managed_worker"]["status"], "failed")
            self.assertIn("worker exited", device["degraded_reason"])
            health = self.manager.health()
            self.assertEqual(health["health"]["status"], sensoriumd.HEALTH_WARN)
            self.assertGreaterEqual(health["bridge"]["worker_restarts"], 1)

    def test_managed_controller_worker_logs_remain_bounded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            worker_script = Path(tmpdir) / "noisy-worker.py"
            worker_script.write_text(
                "import sys\n"
                "sys.stdout.write('x' * (300 * 1024))\n"
                "sys.stdout.flush()\n",
                encoding="utf-8",
            )
            self.manager.state_root = Path(tmpdir)
            controller_model = {
                "name": "managed-controller-noisy",
                "adapter": "runtime",
                "runtime": {
                    "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                    "devices": [
                        {
                            "id": "controller-spi",
                            "bus": "spi-main",
                            "transport": "spi",
                            "chip_select": 1,
                            "device_name": "spidev0.1",
                            "backend": {
                                "kind": "controller",
                                "worker": {
                                    "command": [sys.executable, str(worker_script)],
                                    "restart_limit": 0,
                                    "restart_backoff_ms": 0,
                                },
                            },
                            "metadata": {},
                        }
                    ],
                },
            }
            self.manager.apply_model(controller_model)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                device = self.manager.devices["controller-spi"]
                if device.get("managed_worker", {}).get("status") == "failed":
                    break
                time.sleep(0.02)

            log_path = Path(self.manager.devices["controller-spi"]["managed_worker"]["log_path"])
            self.assertTrue(log_path.exists())
            self.assertLessEqual(
                log_path.stat().st_size,
                runtime_workers.MANAGED_WORKER_LOG_LIMIT_BYTES,
            )

    def test_managed_controller_send_failure_forces_restart(self):
        controller_model = {
            "name": "managed-controller-send-failure",
            "adapter": "runtime",
            "runtime": {
                "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                "devices": [
                    {
                        "id": "controller-spi",
                        "bus": "spi-main",
                        "transport": "spi",
                        "chip_select": 1,
                        "device_name": "spidev0.1",
                        "backend": {
                            "kind": "controller",
                            "worker": {
                                "command": [
                                    sys.executable,
                                    str(RUNTIME_SCRIPTS_DIR / "runtime-controller-spi-flash.py"),
                                ],
                                "env": {"PYTHONPATH": str(SRC_DIR)},
                                "restart_limit": 1,
                                "restart_backoff_ms": 10,
                            },
                        },
                        "metadata": {},
                    }
                ],
            },
        }
        self.manager.apply_model(controller_model)
        device = self.manager.devices["controller-spi"]
        state = self.manager.managed_workers["controller-spi"]
        original_pid = state["proc"].pid
        state["sock"].close()

        payload = build_spi_payload(
            device,
            [{"len": 2, "speed_hz": 1000000, "bits_per_word": 8, "tx": "9f00"}],
        )
        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_SPI_XFER, 89, payload)
        self.assertEqual((status, data), (-errno.EPIPE, b""))

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            current = self.manager.devices["controller-spi"].get("managed_worker", {})
            new_state = self.manager.managed_workers.get("controller-spi")
            if (
                current.get("status") == "running"
                and current.get("pid")
                and current.get("pid") != original_pid
                and new_state is not None
            ):
                break
            time.sleep(0.02)

        current = self.manager.devices["controller-spi"]["managed_worker"]
        self.assertEqual(current["status"], "running")
        self.assertNotEqual(current["pid"], original_pid)
        health = self.manager.health()
        self.assertGreaterEqual(health["bridge"]["worker_restarts"], 1)

    def test_external_attach_rejects_broker_managed_device(self):
        controller_model = {
            "name": "managed-controller-demo",
            "adapter": "runtime",
            "runtime": {
                "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                "devices": [
                    {
                        "id": "controller-spi",
                        "bus": "spi-main",
                        "transport": "spi",
                        "chip_select": 1,
                        "device_name": "spidev0.1",
                        "backend": {
                            "kind": "controller",
                            "worker": {
                                "command": [
                                    sys.executable,
                                    str(RUNTIME_SCRIPTS_DIR / "runtime-controller-spi-flash.py"),
                                ],
                                "env": {"PYTHONPATH": str(SRC_DIR)},
                            },
                        },
                        "metadata": {},
                    }
                ],
            },
        }
        self.manager.apply_model(controller_model)
        with self.assertRaisesRegex(RuntimeError, "broker-managed"):
            self.manager.attach_backend("backend-a", ["controller-spi"])

    def test_bridge_workers_allow_fast_template_request_to_bypass_slow_backend(self):
        controller_model = {
            "name": "concurrency-demo",
            "adapter": "runtime",
            "runtime": {
                "buses": [
                    {"id": "spi-main", "transport": "spi", "name": "spi0"},
                    {"id": "uart-main", "transport": "uart", "name": "uart0"},
                ],
                "devices": [
                    {
                        "id": "controller-spi",
                        "bus": "spi-main",
                        "transport": "spi",
                        "chip_select": 1,
                        "device_name": "spidev0.1",
                        "backend": {"kind": "controller"},
                        "metadata": {},
                    },
                    {
                        "id": "console-uart",
                        "bus": "uart-main",
                        "transport": "uart",
                        "port_name": "ttyAMA0",
                        "backend": {
                            "kind": "template",
                            "template": "uart-script",
                            "echo": True,
                            "line_responses": {"AT": "OK\r\n"},
                        },
                        "metadata": {},
                    },
                ],
            },
        }
        manager = sensoriumd.RuntimeManager(
            Path("/dev/fake-runtime-bridge"),
            worker_count=2,
            max_pending_requests=4,
            kernel_timeout_ms=1000,
            controller_timeout_ms=900,
            **TEST_MANAGER_KWARGS,
        )
        manager.bridge = QueuedBridge(manager.bridge.path)
        manager.start()
        self.addCleanup(manager.shutdown)
        self.assertTrue(manager.bridge.negotiated)
        manager.apply_model(controller_model)
        manager.attach_backend("backend-a", ["controller-spi"])
        manager.bridge.writes.clear()

        spi_device = manager.devices["controller-spi"]
        uart_device = manager.devices["console-uart"]
        spi_payload = build_spi_payload(
            spi_device,
            [{"len": 2, "speed_hz": 1000000, "bits_per_word": 8, "tx": "a55a"}],
        )
        uart_payload = ABI_UART_REQ_PREFIX_STRUCT.pack(
            uart_device["handle"], 0, 4, 0, 0
        ) + b"AT\r\n"

        manager.bridge.push_frame(sensoriumd.REQ_SPI_XFER, 201, spi_payload)
        manager.bridge.push_frame(sensoriumd.REQ_UART_TX, 202, uart_payload)

        event = manager.next_event("backend-a", timeout=1.0)
        self.assertIsNotNone(event)
        self.assertEqual(event["request_id"], 201)

        fast_reply = manager.bridge.wait_for_write(202, timeout=1.0)
        self.assertIsNotNone(fast_reply)
        self.assertEqual(fast_reply[0], sensoriumd.CMD_REPLY)
        fast_status, fast_len = sensoriumd.REPLY_PREFIX_STRUCT.unpack_from(fast_reply[3], 0)
        self.assertEqual(fast_status, 0)
        self.assertEqual(fast_reply[3][sensoriumd.REPLY_PREFIX_STRUCT.size :], b"AT\r\nOK\r\n")
        self.assertEqual(fast_len, 8)

        manager.reply_event("backend-a", event["request_id"], 0, "beef")
        slow_reply = manager.bridge.wait_for_write(201, timeout=1.0)
        self.assertIsNotNone(slow_reply)
        slow_status, _slow_len = sensoriumd.REPLY_PREFIX_STRUCT.unpack_from(slow_reply[3], 0)
        self.assertEqual(slow_status, 0)

        stats = manager.get_stats()
        self.assertGreaterEqual(stats["bridge"]["completed"], 2)
        self.assertGreaterEqual(stats["bridge"]["inflight_max"], 1)

    def test_late_controller_reply_is_counted(self):
        controller_model = {
            "name": "late-reply-demo",
            "adapter": "runtime",
            "runtime": {
                "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                "devices": [
                    {
                        "id": "controller-spi",
                        "bus": "spi-main",
                        "transport": "spi",
                        "chip_select": 1,
                        "device_name": "spidev0.1",
                        "backend": {"kind": "controller"},
                        "metadata": {},
                    }
                ],
            },
        }
        manager = sensoriumd.RuntimeManager(
            Path("/dev/fake-runtime-bridge"),
            kernel_timeout_ms=1000,
            controller_timeout_ms=50,
            **TEST_MANAGER_KWARGS,
        )
        manager.bridge = FakeBridge(manager.bridge.path)
        manager.apply_model(controller_model)
        manager.attach_backend("backend-a", ["controller-spi"])
        device = manager.devices["controller-spi"]
        payload = build_spi_payload(
            device,
            [{"len": 2, "speed_hz": 1000000, "bits_per_word": 8, "tx": "a55a"}],
        )

        result = {}

        def issue_request():
            result["response"] = manager._handle_bridge_request(sensoriumd.REQ_SPI_XFER, 301, payload)

        worker = threading.Thread(target=issue_request)
        worker.start()
        event = manager.next_event("backend-a", timeout=1.0)
        self.assertIsNotNone(event)
        worker.join(timeout=1.0)
        self.assertEqual(result["response"], (-errno.ETIMEDOUT, b""))

        with self.assertRaisesRegex(RuntimeError, "unknown request id"):
            manager.reply_event("backend-a", event["request_id"], 0, "beef")
        self.assertEqual(manager.get_stats()["bridge"]["late_replies"], 1)

    def test_bridge_metrics_desync_marks_runtime_unhealthy(self):
        self.manager.apply_model(self.model)
        self.manager.bridge.metrics_payload = {"desynced": True}

        health = self.manager.health()

        self.assertEqual(health["health"]["status"], sensoriumd.HEALTH_ERROR)
        self.assertEqual(health["health"]["state"], "desynced")
        self.assertIn("kernel bridge reported desynced runtime session", health["health"]["reasons"])

    def test_spi_zero_lane_widths_normalize_to_single_lane(self):
        controller_model = {
            "name": "controller-demo",
            "adapter": "runtime",
            "runtime": {
                "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                "devices": [
                    {
                        "id": "controller-spi",
                        "bus": "spi-main",
                        "transport": "spi",
                        "chip_select": 1,
                        "device_name": "spidev0.1",
                        "backend": {"kind": "controller"},
                        "metadata": {},
                    }
                ],
            },
        }
        self.manager.apply_model(controller_model)
        self.manager.attach_backend("backend-a", ["controller-spi"])
        device = self.manager.devices["controller-spi"]

        results = {}

        def issue_request():
            payload = build_spi_payload(
                device,
                [{"len": 2, "speed_hz": 1000000, "bits_per_word": 8, "tx": "a55a"}],
            )
            results["response"] = self.manager._handle_bridge_request(
                sensoriumd.REQ_SPI_XFER, 78, payload
            )

        worker = threading.Thread(target=issue_request)
        worker.start()
        event = self.manager.next_event("backend-a", timeout=1.0)
        self.assertIsNotNone(event)
        self.assertEqual(event["transfers"][0]["tx_nbits"], 1)
        self.assertEqual(event["transfers"][0]["rx_nbits"], 1)
        self.manager.reply_event("backend-a", event["request_id"], 0, "beef")
        worker.join(timeout=2.0)
        self.assertEqual(results["response"], (0, bytes.fromhex("beef")))

    def test_invalid_spi_lane_width_is_rejected_and_traced(self):
        self.manager.apply_model(self.model)
        spi_device = self.manager.devices["flash-spi"]

        payload = build_spi_payload(
            spi_device,
            [{"len": 2, "speed_hz": 500000, "bits_per_word": 8, "tx_nbits": 3, "tx": "9f00"}],
        )
        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_SPI_XFER, 92, payload)
        self.assertEqual((status, data), (-errno.EOPNOTSUPP, b""))
        trace = self.manager.get_trace(1)["events"][0]
        self.assertEqual(trace["transport"], "spi")
        self.assertEqual(trace["request"]["reason"], "invalid-lane-width")

    def test_controller_backed_spi_without_backend_returns_enodev(self):
        controller_model = {
            "name": "controller-demo",
            "adapter": "runtime",
            "runtime": {
                "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                "devices": [
                    {
                        "id": "controller-spi",
                        "bus": "spi-main",
                        "transport": "spi",
                        "chip_select": 1,
                        "device_name": "spidev0.1",
                        "backend": {"kind": "controller"},
                        "metadata": {},
                    }
                ],
            },
        }
        self.manager.apply_model(controller_model)
        device = self.manager.devices["controller-spi"]
        payload = build_spi_payload(
            device,
            [{"len": 2, "speed_hz": 1000000, "bits_per_word": 8, "tx": "a55a"}],
        )
        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_SPI_XFER, 93, payload)
        self.assertEqual((status, data), (-errno.ENODEV, b""))

    def test_fault_injection_trace_and_stats_are_recorded(self):
        self.manager.apply_model(self.model)
        self.manager.update_device(
            "flash-spi",
            {
                "faults": {
                    "mode": "short-reply",
                    "reply_data": "be",
                    "remaining": 1,
                }
            },
        )
        spi_device = self.manager.devices["flash-spi"]

        spi_payload = build_spi_payload(
            spi_device,
            [{"len": 3, "speed_hz": 500000, "bits_per_word": 8, "tx": "9f0000"}],
        )
        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_SPI_XFER, 91, spi_payload)
        self.assertEqual(status, 0)
        self.assertEqual(data, bytes.fromhex("be"))

        stats = self.manager.get_stats()
        self.assertEqual(stats["runtime"]["requests"], 1)
        self.assertEqual(stats["devices"]["flash-spi"]["ok"], 1)

        trace = self.manager.get_trace(1)["events"][0]
        self.assertEqual(trace["transport"], "spi")
        self.assertEqual(trace["device_id"], "flash-spi")
        self.assertEqual(trace["reply"], "be")

    def test_device_update_rebuilds_template_and_preserves_identity(self):
        self.manager.apply_model(self.model)

        updated = self.manager.update_device(
            "console-uart",
            {
                "settings": {"baud_rate": 57600, "parity": "even"},
                "backend": {
                    "echo": False,
                    "binary_responses": {"0102": "aabb"},
                    "default_response": "ff",
                },
            },
        )

        self.assertEqual(updated["settings"]["baud_rate"], 57600)
        self.assertEqual(updated["settings"]["parity"], "even")
        uart_template = self.manager.devices["console-uart"]["template"]
        self.assertEqual(uart_template.handle_uart(bytes.fromhex("0102")), bytes.fromhex("aabb"))

    def test_uart_config_request_updates_settings(self):
        self.manager.apply_model(self.model)
        device = self.manager.devices["console-uart"]
        payload = sensoriumd.UART_CFG_STRUCT.pack(
            device["handle"],
            57600,
            termios.CS7 | termios.PARENB | termios.CSTOPB,
            termios.IXON,
            0,
            0,
        )
        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_UART_CFG, 123, payload)
        self.assertEqual((status, data), (0, b""))
        self.assertEqual(device["settings"]["baud_rate"], 57600)
        self.assertEqual(device["settings"]["data_bits"], 7)
        self.assertEqual(device["settings"]["parity"], "even")
        self.assertEqual(device["settings"]["stop_bits"], 2)
        self.assertTrue(device["settings"]["xonxoff"])

    def test_runtime_snapshot_restores_devices_and_backend_attachments(self):
        controller_model = {
            "name": "restore-demo",
            "schema_version": runtime_common.RUNTIME_MODEL_SCHEMA_VERSION,
            "adapter": "runtime",
            "runtime": {
                "buses": [
                    {"id": "spi-main", "transport": "spi", "name": "spi0"},
                    {"id": "uart-main", "transport": "uart", "name": "uart0"},
                ],
                "devices": [
                    {
                        "id": "controller-spi",
                        "bus": "spi-main",
                        "transport": "spi",
                        "chip_select": 1,
                        "device_name": "spidev0.1",
                        "backend": {"kind": "controller"},
                        "metadata": {},
                    },
                    {
                        "id": "console-uart",
                        "bus": "uart-main",
                        "transport": "uart",
                        "port_name": "ttyAMA0",
                        "backend": {
                            "kind": "template",
                            "template": "uart-script",
                            "echo": True,
                            "line_responses": {"AT": "OK\r\n"},
                        },
                        "metadata": {},
                    },
                ],
            },
        }
        with tempfile.TemporaryDirectory(prefix="sensorium-runtime-state-") as tempdir:
            snapshot_path = Path(tempdir) / "runtime-snapshot.json"
            trace_path = Path(tempdir) / "runtime-trace.jsonl"
            manager = sensoriumd.RuntimeManager(
                Path("/dev/fake-runtime-bridge"),
                snapshot_path=snapshot_path,
                trace_path=trace_path,
                restore_snapshot=False,
            )
            manager.bridge = FakeBridge(manager.bridge.path)
            manager.apply_model(controller_model)
            manager.attach_backend("backend-a", ["controller-spi"])
            manager.update_device(
                "console-uart",
                {"settings": {"baud_rate": 57600, "parity": "even"}},
            )

            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(
                snapshot["schema_version"], runtime_common.RUNTIME_SNAPSHOT_SCHEMA_VERSION
            )
            self.assertEqual(snapshot["model"]["name"], "restore-demo")
            self.assertEqual(snapshot["backend_attachments"]["backend-a"], ["controller-spi"])

            restored = sensoriumd.RuntimeManager(
                Path("/dev/fake-runtime-bridge"),
                snapshot_path=snapshot_path,
                trace_path=trace_path,
                restore_snapshot=True,
            )
            restored.bridge = FakeBridge(restored.bridge.path)
            self.assertTrue(restored.restore_snapshot_if_available())
            self.assertEqual(restored.model_name, "restore-demo")
            self.assertEqual(restored.devices["controller-spi"]["attached_backend"], "backend-a")
            self.assertEqual(restored.devices["console-uart"]["settings"]["baud_rate"], 57600)
            self.assertEqual(restored.devices["console-uart"]["settings"]["parity"], "even")
            self.assertTrue(restored.status()["persistence"]["snapshot_loaded"])

    def test_runtime_snapshot_restore_rejects_invalid_model_before_reset(self):
        bad_model = json.loads(json.dumps(self.model))
        bad_model["runtime"]["devices"].append(
            {
                "id": "flash-spi-dup",
                "bus": "spi-main",
                "transport": "spi",
                "chip_select": 0,
                "device_name": "spidev0.0",
                "backend": {"kind": "template", "template": "spi-script"},
                "metadata": {},
                "faults": {"mode": "none", "remaining": 0},
                "settings": {"mode": 0, "bits_per_word": 8, "max_speed_hz": 500000},
            }
        )

        with tempfile.TemporaryDirectory(prefix="sensorium-runtime-state-") as tempdir:
            snapshot_path = Path(tempdir) / "runtime-snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "schema_version": runtime_common.RUNTIME_SNAPSHOT_SCHEMA_VERSION,
                        "model": bad_model,
                        "backend_attachments": {},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            restored = sensoriumd.RuntimeManager(
                Path("/dev/fake-runtime-bridge"),
                snapshot_path=snapshot_path,
                trace_path=None,
                restore_snapshot=True,
            )
            restored.bridge = FakeBridge(restored.bridge.path)

            self.assertFalse(restored.restore_snapshot_if_available())
            self.assertEqual(restored.bridge.writes, [])
            self.assertIsNone(restored.model_name)
            self.assertEqual(restored.status()["bus_count"], 0)
            self.assertEqual(restored.status()["device_count"], 0)
            self.assertIn(
                "duplicate spi chip_select",
                restored.status()["persistence"]["last_snapshot_error"],
            )

    def test_runtime_snapshot_restore_rejects_invalid_backend_attachments_before_apply(self):
        controller_model = {
            "name": "restore-demo",
            "schema_version": runtime_common.RUNTIME_MODEL_SCHEMA_VERSION,
            "adapter": "runtime",
            "runtime": {
                "buses": [
                    {"id": "spi-main", "transport": "spi", "name": "spi0"},
                    {"id": "uart-main", "transport": "uart", "name": "uart0"},
                ],
                "devices": [
                    {
                        "id": "controller-spi",
                        "bus": "spi-main",
                        "transport": "spi",
                        "chip_select": 1,
                        "device_name": "spidev0.1",
                        "backend": {"kind": "controller"},
                        "metadata": {},
                    },
                    {
                        "id": "console-uart",
                        "bus": "uart-main",
                        "transport": "uart",
                        "port_name": "ttyAMA0",
                        "backend": {
                            "kind": "template",
                            "template": "uart-script",
                            "echo": True,
                        },
                        "metadata": {},
                    },
                ],
            },
        }

        with tempfile.TemporaryDirectory(prefix="sensorium-runtime-state-") as tempdir:
            snapshot_path = Path(tempdir) / "runtime-snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "schema_version": runtime_common.RUNTIME_SNAPSHOT_SCHEMA_VERSION,
                        "model": controller_model,
                        "backend_attachments": {"backend-a": ["console-uart"]},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            restored = sensoriumd.RuntimeManager(
                Path("/dev/fake-runtime-bridge"),
                snapshot_path=snapshot_path,
                trace_path=None,
                restore_snapshot=True,
            )
            restored.bridge = FakeBridge(restored.bridge.path)

            self.assertFalse(restored.restore_snapshot_if_available())
            self.assertEqual(restored.bridge.writes, [])
            self.assertIsNone(restored.model_name)
            self.assertEqual(restored.status()["bus_count"], 0)
            self.assertEqual(restored.status()["device_count"], 0)
            self.assertIn(
                "not controller-backed",
                restored.status()["persistence"]["last_snapshot_error"],
            )

    def test_runtime_snapshot_restore_discards_old_snapshot_schema(self):
        with tempfile.TemporaryDirectory(prefix="sensorium-runtime-state-") as tempdir:
            snapshot_path = Path(tempdir) / "runtime-snapshot.json"
            snapshot_path.write_text(
                json.dumps({"schema_version": 1, "model": self.model, "backend_attachments": {}}, indent=2)
                + "\n",
                encoding="utf-8",
            )

            restored = sensoriumd.RuntimeManager(
                Path("/dev/fake-runtime-bridge"),
                snapshot_path=snapshot_path,
                trace_path=None,
                restore_snapshot=True,
            )
            restored.bridge = FakeBridge(restored.bridge.path)

            self.assertFalse(restored.restore_snapshot_if_available())
            self.assertFalse(snapshot_path.exists())
            self.assertIn(
                "snapshot schema_version",
                restored.status()["persistence"]["last_snapshot_error"],
            )

    def test_trace_history_is_loaded_from_jsonl(self):
        with tempfile.TemporaryDirectory(prefix="sensorium-runtime-trace-") as tempdir:
            trace_path = Path(tempdir) / "runtime-trace.jsonl"
            manager = sensoriumd.RuntimeManager(
                Path("/dev/fake-runtime-bridge"),
                trace_path=trace_path,
                snapshot_path=None,
                restore_snapshot=False,
                trace_limit=8,
            )
            manager.bridge = FakeBridge(manager.bridge.path)
            manager.apply_model(self.model)
            device = manager.devices["console-uart"]
            payload = ABI_UART_REQ_PREFIX_STRUCT.pack(
                device["handle"], 0, 4, 0, 0
            ) + b"AT\r\n"
            status, data = manager._handle_bridge_request(sensoriumd.REQ_UART_TX, 333, payload)
            self.assertEqual((status, data), (0, b"AT\r\nOK\r\n"))
            manager.flush_trace_writes()

            restored = sensoriumd.RuntimeManager(
                Path("/dev/fake-runtime-bridge"),
                trace_path=trace_path,
                snapshot_path=None,
                restore_snapshot=False,
                trace_limit=8,
            )
            restored.bridge = FakeBridge(restored.bridge.path)
            events = restored.get_trace(4)["events"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["transport"], "uart")
            self.assertEqual(events[0]["status"], 0)
            self.assertEqual(restored.status()["persistence"]["trace_loaded"], 1)

    def test_missing_trace_file_does_not_degrade_health(self):
        with tempfile.TemporaryDirectory(prefix="sensorium-runtime-trace-missing-") as tempdir:
            trace_path = Path(tempdir) / "runtime-trace.jsonl"
            manager = sensoriumd.RuntimeManager(
                Path("/dev/fake-runtime-bridge"),
                trace_path=trace_path,
                snapshot_path=None,
                restore_snapshot=False,
                trace_limit=8,
            )
            manager.bridge = FakeBridge(manager.bridge.path)

            status = manager.status()
            self.assertEqual(status["persistence"]["trace_loaded"], 0)
            self.assertIsNone(status["persistence"]["last_trace_error"])
            self.assertEqual(status["health"]["status"], sensoriumd.HEALTH_OK)

    def test_truncated_final_trace_line_is_ignored(self):
        with tempfile.TemporaryDirectory(prefix="sensorium-runtime-trace-truncated-") as tempdir:
            trace_path = Path(tempdir) / "runtime-trace.jsonl"
            trace_path.write_text(
                json.dumps({"transport": "uart", "status": 0}) + "\n"
                + '{"transport":"spi"',
                encoding="utf-8",
            )

            manager = sensoriumd.RuntimeManager(
                Path("/dev/fake-runtime-bridge"),
                trace_path=trace_path,
                snapshot_path=None,
                restore_snapshot=False,
                trace_limit=8,
            )
            manager.bridge = FakeBridge(manager.bridge.path)

            events = manager.get_trace(8)["events"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["transport"], "uart")
            status = manager.status()
            self.assertEqual(status["persistence"]["trace_loaded"], 1)
            self.assertIsNone(status["persistence"]["last_trace_error"])
            self.assertEqual(status["health"]["status"], sensoriumd.HEALTH_OK)

    def test_null_padded_trace_lines_are_ignored(self):
        with tempfile.TemporaryDirectory(prefix="sensorium-runtime-trace-null-") as tempdir:
            trace_path = Path(tempdir) / "runtime-trace.jsonl"
            trace_path.write_bytes(
                (
                    json.dumps({"transport": "i2c", "status": 0}) + "\n"
                ).encode("utf-8")
                + (b"\x00" * 32)
                + b"\n"
                + (
                    json.dumps({"transport": "spi", "status": 0}) + "\n"
                ).encode("utf-8")
            )

            manager = sensoriumd.RuntimeManager(
                Path("/dev/fake-runtime-bridge"),
                trace_path=trace_path,
                snapshot_path=None,
                restore_snapshot=False,
                trace_limit=8,
            )
            manager.bridge = FakeBridge(manager.bridge.path)

            events = manager.get_trace(8)["events"]
            self.assertEqual([event["transport"] for event in events], ["i2c", "spi"])
            status = manager.status()
            self.assertEqual(status["persistence"]["trace_loaded"], 2)
            self.assertIsNone(status["persistence"]["last_trace_error"])
            self.assertEqual(status["health"]["status"], sensoriumd.HEALTH_OK)

    def test_uart_inject_and_control_emit_bridge_commands(self):
        self.manager.apply_model(self.model)

        result = self.manager.inject_uart_rx("console-uart", "414243")
        self.assertEqual(result["bytes"], 3)
        self.assertEqual(self.manager.bridge.writes[-1][0], sensoriumd.CMD_UART_INJECT_RX)

        result = self.manager.set_uart_modem("console-uart", {"cts": True, "ri": False})
        self.assertEqual(result["mask"], sensoriumd.UART_MODEM_BITS["cts"] | sensoriumd.UART_MODEM_BITS["ri"])
        self.assertEqual(self.manager.bridge.writes[-1][0], sensoriumd.CMD_UART_SET_MODEM)

    def test_invalid_uart_flags_are_rejected_and_traced(self):
        self.manager.apply_model(self.model)
        device = self.manager.devices["console-uart"]
        payload = sensoriumd.UART_REQ_PREFIX_STRUCT.pack(
            device["handle"], 9, 0, 0, 0
        )
        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_UART_TX, 130, payload)
        self.assertEqual((status, data), (-errno.EPROTO, b""))
        trace = self.manager.get_trace(1)["events"][0]
        self.assertEqual(trace["transport"], "uart")
        self.assertEqual(trace["request"]["reason"], "invalid-flags")

    def test_validate_command_payload_rejects_bad_fixed_width_frames(self):
        with self.assertRaisesRegex(ValueError, "requires 0 bytes"):
            sensoriumd.validate_command_payload(sensoriumd.CMD_RESET, b"x")
        with self.assertRaisesRegex(ValueError, "requires 76 bytes"):
            sensoriumd.validate_command_payload(sensoriumd.CMD_BUS_ADD, b"\0" * 75)
        with self.assertRaisesRegex(ValueError, "requires 92 bytes"):
            sensoriumd.validate_command_payload(sensoriumd.CMD_DEVICE_ADD, b"\0" * 79)
        with self.assertRaisesRegex(ValueError, "requires 12 bytes"):
            sensoriumd.validate_command_payload(sensoriumd.CMD_UART_SET_MODEM, b"\0" * 8)

    def test_trace_drop_health_resets_with_runtime_state_clear(self):
        self.manager.apply_model(self.model)

        with self.manager.trace_writer.cond:
            self.manager.trace_writer.drop_count = 2
            self.manager.trace_writer.max_queue_depth = 5
            self.manager.trace_writer.max_queue_bytes = 64

        with self.manager.lock:
            self.manager._refresh_trace_metrics_locked()
            self.assertEqual(self.manager._health_summary_locked()["status"], sensoriumd.HEALTH_WARN)
            self.manager._clear_state()
            self.manager._refresh_trace_metrics_locked()
            self.assertEqual(self.manager.persistence["trace_drop_count"], 0)
            self.assertEqual(self.manager._health_summary_locked()["status"], sensoriumd.HEALTH_OK)

    def test_validate_command_payload_rejects_oversized_runtime_frame(self):
        with self.assertRaisesRegex(ValueError, "payload too large"):
            sensoriumd.validate_command_payload(
                sensoriumd.CMD_REPLY, b"\0" * (runtime_common.RUNTIME_MAX_PAYLOAD + 1)
            )

    def test_mixed_address_i2c_request_is_rejected_and_traced(self):
        self.manager.apply_model(self.model)
        i2c_device = self.manager.devices["env-bme280"]

        payload = (
            ABI_I2C_REQ_PREFIX_STRUCT.pack(i2c_device["handle"], i2c_device["bus_handle"], 2, 1)
            + ABI_I2C_REQ_MSG_STRUCT.pack(0x76, 0, 1, 0)
            + ABI_I2C_REQ_MSG_STRUCT.pack(0x77, 1, 1, 0)
            + bytes([0x10])
        )

        status, data = self.manager._handle_bridge_request(sensoriumd.REQ_I2C_XFER, 140, payload)

        self.assertEqual((status, data), (-errno.EOPNOTSUPP, b""))
        trace = self.manager.get_trace(1)["events"][0]
        self.assertEqual(trace["transport"], "i2c")
        self.assertEqual(trace["request"]["reason"], "mixed-address-transfer")


class RuntimeRpcDispatchTests(unittest.TestCase):
    def test_dispatch_runtime_apply_routes_to_manager(self):
        manager = FakeRpcManager()
        request = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "runtime.apply",
            "params": {"model": {"name": "demo"}},
        }

        response = runtime_rpc.dispatch_runtime_request(manager, request)

        self.assertEqual(manager.applied_models, [{"name": "demo"}])
        self.assertEqual(response["id"], 7)
        self.assertEqual(response["result"]["state"], "ready")
        self.assertEqual(response["result"]["applied_models"], 1)

    def test_dispatch_daemon_stop_sets_stop_event(self):
        manager = FakeRpcManager()

        response = runtime_rpc.dispatch_runtime_request(
            manager,
            {"jsonrpc": "2.0", "id": 9, "method": "daemon.stop"},
        )

        self.assertEqual(response["result"], {"ok": True})
        self.assertTrue(manager.stop_event.wait(timeout=1.0))


class RpcServerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="sensorium-rpc-test-"))
        self.socket_path = self.tempdir / "sensoriumd.sock"
        self.manager = sensoriumd.RuntimeManager(Path("/dev/fake-runtime-bridge"), **TEST_MANAGER_KWARGS)
        self.manager.bridge = FakeBridge(self.manager.bridge.path)
        self.manager.apply_model(
            runtime_common.normalize_runtime_model(
                REPO_ROOT / "models" / "runtime" / "rpi-multibus.yaml"
            )
        )
        self.server = runtime_rpc.RuntimeRpcServer(
            self.socket_path,
            lambda request: runtime_rpc.dispatch_runtime_request(self.manager, request),
        )
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        deadline = time.monotonic() + 2.0
        while not self.socket_path.exists():
            if time.monotonic() > deadline:
                raise RuntimeError("RPC socket did not appear")
            time.sleep(0.01)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2.0)
        shutil.rmtree(self.tempdir)

    def test_rpc_status_and_device_listing(self):
        status = runtime_common.rpc_call("status", socket_path=self.socket_path, timeout=2.0)
        self.assertEqual(status["model"], "rpi-multibus")
        self.assertEqual(status["device_count"], 6)
        self.assertIn("stats", status)

        devices = runtime_common.rpc_call(
            "device.list", socket_path=self.socket_path, timeout=2.0
        )
        self.assertEqual(
            {item["id"] for item in devices["devices"]},
            {"env-bme280", "env-bme680", "flash-spi", "aux-spi", "console-uart", "aux-uart"},
        )

        device = runtime_common.rpc_call(
            "device.get", {"device_id": "console-uart"}, socket_path=self.socket_path, timeout=2.0
        )
        self.assertEqual(device["device"]["id"], "console-uart")

        stats = runtime_common.rpc_call("stats.get", socket_path=self.socket_path, timeout=2.0)
        self.assertIn("runtime", stats)

        health = runtime_common.rpc_call("health.get", socket_path=self.socket_path, timeout=2.0)
        self.assertEqual(health["health"]["status"], sensoriumd.HEALTH_OK)

        trace = runtime_common.rpc_call("trace.list", {"limit": 4}, socket_path=self.socket_path, timeout=2.0)
        self.assertIn("events", trace)

    def test_rpc_reports_errors_for_unknown_method(self):
        with self.assertRaisesRegex(RuntimeError, "unknown method"):
            runtime_common.rpc_call("does.not.exist", socket_path=self.socket_path, timeout=2.0)

    def test_client_wrapper_uses_new_runtime_methods(self):
        client = runtime_client.SensoriumRuntimeClient(self.socket_path, timeout=2.0)
        status = client.status()
        self.assertEqual(status["model"], "rpi-multibus")
        self.assertEqual(status["state"], "ready")
        health = client.health()
        self.assertEqual(health["health"]["status"], sensoriumd.HEALTH_OK)
        device = client.get_device("flash-spi")
        self.assertEqual(device["device"]["id"], "flash-spi")
        trace = client.trace(2)
        self.assertIn("events", trace)

    def test_long_poll_does_not_consume_rpc_worker_capacity(self):
        socket_path = self.tempdir / "sensoriumd-longpoll.sock"
        server = runtime_rpc.RuntimeRpcServer(
            socket_path,
            lambda request: runtime_rpc.dispatch_runtime_request(self.manager, request),
            max_workers=1,
            next_event_fn=self.manager.poll_next_event,
            note_backend_poll_fn=self.manager.note_backend_poll,
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            deadline = time.monotonic() + 2.0
            while not socket_path.exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("RPC socket did not appear")
                time.sleep(0.01)

            results = {}

            def poll_event():
                results["event"] = runtime_common.rpc_call(
                    "backend.next_event",
                    {"backend_id": "backend-a", "timeout": 0.5},
                    socket_path=socket_path,
                    timeout=2.0,
                )

            waiter = threading.Thread(target=poll_event, daemon=True)
            waiter.start()
            time.sleep(0.05)
            started = time.monotonic()
            status = runtime_common.rpc_call("status", socket_path=socket_path, timeout=1.0)
            elapsed = time.monotonic() - started
            waiter.join(timeout=2.0)

            self.assertEqual(status["model"], "rpi-multibus")
            self.assertLess(elapsed, 0.4)
            self.assertEqual(results["event"]["event"], None)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2.0)

    def test_oversized_rpc_request_closes_client(self):
        socket_path = self.tempdir / "sensoriumd-limits.sock"
        server = runtime_rpc.RuntimeRpcServer(
            socket_path,
            lambda request: runtime_rpc.dispatch_runtime_request(self.manager, request),
            max_request_bytes=64,
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            deadline = time.monotonic() + 2.0
            while not socket_path.exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("RPC socket did not appear")
                time.sleep(0.01)

            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                client.connect(str(socket_path))
                oversized = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "status",
                        "params": {"padding": "x" * 256},
                    }
                ).encode("utf-8") + b"\n"
                client.sendall(oversized)
                reply = client.recv(4096)
            self.assertIn(b"exceeded maximum size", reply)
            self.assertEqual(
                runtime_common.rpc_call("status", socket_path=socket_path, timeout=2.0)["state"],
                "ready",
            )
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2.0)

    def test_too_many_pending_rpc_requests_close_client(self):
        socket_path = self.tempdir / "sensoriumd-queued-limits.sock"

        def slow_dispatch(request):
            time.sleep(0.2)
            return runtime_rpc.dispatch_runtime_request(self.manager, request)

        server = runtime_rpc.RuntimeRpcServer(
            socket_path,
            slow_dispatch,
            max_workers=1,
            max_pending_requests_per_client=1,
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            deadline = time.monotonic() + 2.0
            while not socket_path.exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("RPC socket did not appear")
                time.sleep(0.01)

            request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "status"}).encode("utf-8") + b"\n"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                client.connect(str(socket_path))
                client.sendall(request * 3)
                reply = client.recv(4096)
            self.assertIn(b"maximum queued request count", reply)
            self.assertEqual(
                runtime_common.rpc_call("status", socket_path=socket_path, timeout=2.0)["state"],
                "ready",
            )
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2.0)


class RuntimeRpcHelpersTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="sensorium-rpc-helper-test-"))

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_prepare_socket_path_unlinks_stale_socket(self):
        socket_path = self.tempdir / "stale.sock"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(socket_path))
        sock.close()

        self.assertTrue(socket_path.exists())
        runtime_rpc.prepare_socket_path(socket_path)
        self.assertFalse(socket_path.exists())

    def test_prepare_socket_path_rejects_active_socket(self):
        socket_path = self.tempdir / "active.sock"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(socket_path))
        sock.listen()
        try:
            with self.assertRaisesRegex(RuntimeError, "active socket"):
                runtime_rpc.prepare_socket_path(socket_path)
        finally:
            sock.close()
            if socket_path.exists():
                socket_path.unlink()

    def test_prepare_socket_path_rejects_non_socket_path(self):
        socket_path = self.tempdir / "not-a-socket"
        socket_path.write_text("x", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "non-socket path"):
            runtime_rpc.prepare_socket_path(socket_path)


class RuntimeBridgeNegotiationTests(unittest.TestCase):
    def test_runtime_manager_rejects_bridge_missing_required_features(self):
        manager = sensoriumd.RuntimeManager(Path("/dev/fake-runtime-bridge"), **TEST_MANAGER_KWARGS)
        manager.bridge = FakeBridge(manager.bridge.path)
        manager.bridge.negotiated_features = sensoriumd.REQUIRED_FEATURES & ~sensoriumd.FEATURE_EVENTFD_NOTIFY

        with self.assertRaisesRegex(RuntimeError, "missing required ABI v5 feature bits"):
            manager.start()


class SensoriumdDefaultsTests(unittest.TestCase):
    def test_apply_daemon_runtime_defaults_uses_system_state_paths(self):
        args = type(
            "Args",
            (),
            {
                "daemonize": True,
                "foreground": False,
                "trace_path": sensoriumd.RUNTIME_TRACE_PATH,
                "snapshot_path": sensoriumd.RUNTIME_SNAPSHOT_PATH,
                "daemon_log_path": None,
            },
        )()
        env = dict(os.environ)
        env.pop("SENSORIUM_STATE_DIR", None)

        with mock.patch.dict(os.environ, env, clear=True):
            sensoriumd.apply_daemon_runtime_defaults(args)
            self.assertEqual(
                os.environ["SENSORIUM_STATE_DIR"],
                str(sensoriumd.RUNTIME_SYSTEM_STATE_ROOT),
            )
            self.assertEqual(args.trace_path, sensoriumd.RUNTIME_SYSTEM_TRACE_PATH)
            self.assertEqual(args.snapshot_path, sensoriumd.RUNTIME_SYSTEM_SNAPSHOT_PATH)
            self.assertEqual(args.daemon_log_path, sensoriumd.RUNTIME_SYSTEM_DAEMON_LOG_PATH)

    def test_runtime_manager_uses_current_state_root_from_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = dict(os.environ)
            env["SENSORIUM_STATE_DIR"] = tmpdir
            with mock.patch.dict(os.environ, env, clear=True):
                manager = sensoriumd.RuntimeManager(
                    Path("/dev/fake-runtime-bridge"),
                    trace_path=None,
                    snapshot_path=None,
                    restore_snapshot=False,
                )
            self.assertEqual(manager.state_root, Path(tmpdir))


class SensoriumctlSystemdTests(unittest.TestCase):
    def test_privileged_cmd_skips_sudo_when_running_as_root(self):
        with mock.patch.object(sensoriumctl.os, "geteuid", return_value=0), mock.patch.object(
            sensoriumctl.shutil, "which", return_value=None
        ):
            self.assertEqual(
                sensoriumctl._privileged_cmd(["kill", "-0", "123"], action="testing"),
                ["kill", "-0", "123"],
            )

    def test_privileged_cmd_requires_sudo_for_non_root_when_unavailable(self):
        with mock.patch.object(sensoriumctl.os, "geteuid", return_value=1000), mock.patch.object(
            sensoriumctl.shutil, "which", return_value=None
        ):
            with self.assertRaisesRegex(sensoriumctl.ModelError, "requires root privileges"):
                sensoriumctl._privileged_cmd(["kill", "-0", "123"], action="testing")

    def test_daemon_start_ad_hoc_mode_does_not_use_sudo_when_root(self):
        with mock.patch.object(sensoriumctl, "sensoriumd_running", return_value=False), mock.patch.object(
            sensoriumctl, "_sensoriumd_pids", return_value=set()
        ), mock.patch.object(
            sensoriumctl, "read_module_state", return_value={"adapter": "runtime"}
        ), mock.patch.object(
            sensoriumctl, "_systemd_management_enabled", return_value=False
        ), mock.patch.object(
            sensoriumctl, "rpc_call", return_value={"state": "ready"}
        ), mock.patch.object(
            sensoriumctl, "_assert_no_foreign_systemd_service_conflict"
        ), mock.patch.object(
            sensoriumctl, "runtime_socket_path", return_value=Path("/tmp/test-sensoriumd.sock")
        ), mock.patch.object(
            sensoriumctl, "runtime_pidfile_path", return_value=Path("/tmp/test-sensoriumd.pid")
        ), mock.patch.object(
            sensoriumctl.os, "geteuid", return_value=0
        ), mock.patch.object(
            sensoriumctl, "subprocess"
        ) as subprocess_mod:
            subprocess_mod.run.return_value = mock.Mock(returncode=0)
            sensoriumctl.daemon_start()

        start_cmd = subprocess_mod.run.call_args_list[0].args[0]
        self.assertNotEqual(start_cmd[0], "sudo")
        self.assertEqual(start_cmd[0], "env")

    def test_systemd_management_auto_mode_requires_matching_execstart(self):
        with mock.patch.object(
            sensoriumctl, "_systemd_service_load_state", return_value="loaded"
        ), mock.patch.object(
            sensoriumctl, "_systemd_service_execstart_paths", return_value=set()
        ), mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(sensoriumctl._systemd_management_enabled())

        with mock.patch.object(
            sensoriumctl, "_systemd_service_load_state", return_value="loaded"
        ), mock.patch.object(
            sensoriumctl,
            "_systemd_service_execstart_paths",
            return_value={str(sensoriumctl.SENSORIUMD_SCRIPT.resolve())},
        ), mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(sensoriumctl._systemd_management_enabled())

    def test_foreign_systemd_service_conflict_detects_active_mismatched_unit(self):
        foreign_script = "/usr/share/sensorium/scripts/runtime/sensoriumd"
        with mock.patch.object(
            sensoriumctl, "_systemd_management_enabled", return_value=False
        ), mock.patch.object(
            sensoriumctl, "_systemd_service_load_state", return_value="loaded"
        ), mock.patch.object(
            sensoriumctl, "_systemd_service_matches_current_script", return_value=False
        ), mock.patch.object(
            sensoriumctl, "_systemd_service_active", return_value=True
        ), mock.patch.object(
            sensoriumctl, "runtime_socket_path", return_value=sensoriumctl.RUNTIME_SYSTEM_SOCKET_PATH
        ), mock.patch.object(
            sensoriumctl, "_systemd_service_execstart_paths", return_value={foreign_script}
        ):
            message = sensoriumctl._foreign_systemd_service_conflict()

        self.assertIsNotNone(message)
        self.assertIn(str(sensoriumctl.SENSORIUMD_SCRIPT), message)
        self.assertIn(foreign_script, message)

    def test_daemon_start_uses_systemctl_when_management_is_enabled(self):
        with mock.patch.object(sensoriumctl, "sensoriumd_running", return_value=False), mock.patch.object(
            sensoriumctl, "_sensoriumd_pids", return_value=set()
        ), mock.patch.object(
            sensoriumctl, "read_module_state", return_value={"adapter": "runtime"}
        ), mock.patch.object(
            sensoriumctl, "_systemd_management_enabled", return_value=True
        ), mock.patch.object(
            sensoriumctl, "_run_systemctl"
        ) as run_systemctl, mock.patch.object(
            sensoriumctl, "rpc_call", return_value={"state": "ready"}
        ):
            sensoriumctl.daemon_start()

        run_systemctl.assert_called_once_with("start")

    def test_daemon_stop_uses_systemctl_when_management_is_enabled(self):
        with mock.patch.object(sensoriumctl, "_sensoriumd_pids", return_value=set()), mock.patch.object(
            sensoriumctl, "_read_pidfile_pid", return_value=None
        ), mock.patch.object(
            sensoriumctl, "_systemd_management_enabled", return_value=True
        ), mock.patch.object(
            sensoriumctl, "_run_systemctl"
        ) as run_systemctl, mock.patch.object(
            sensoriumctl, "_wait_for_daemon_exit", return_value=True
        ):
            sensoriumctl.daemon_stop()

        run_systemctl.assert_called_once_with("stop", check=False)


class SystemdPackagingTests(unittest.TestCase):
    def test_packaged_unit_uses_env_backed_runtime_paths(self):
        service_text = (REPO_ROOT / "packaging" / "systemd" / "sensoriumd.service").read_text()
        self.assertIn("Environment=SENSORIUM_STATE_DIR=/var/lib/sensorium", service_text)
        self.assertIn("Environment=SENSORIUM_SOCKET_PATH=/run/sensorium/sensoriumd.sock", service_text)
        self.assertIn("Environment=SENSORIUM_PIDFILE_PATH=/run/sensorium/sensoriumd.pid", service_text)
        self.assertIn("--socket-path ${SENSORIUM_SOCKET_PATH}", service_text)
        self.assertIn("--pidfile ${SENSORIUM_PIDFILE_PATH}", service_text)

    def test_source_checkout_installer_preserves_env_expansion_in_unit(self):
        installer_text = (
            REPO_ROOT / "scripts" / "local" / "install-systemd-service.sh"
        ).read_text()
        self.assertIn("Environment=SENSORIUM_STATE_DIR=/var/lib/sensorium", installer_text)
        self.assertIn("Environment=SENSORIUM_SOCKET_PATH=/run/sensorium/sensoriumd.sock", installer_text)
        self.assertIn("Environment=SENSORIUM_PIDFILE_PATH=/run/sensorium/sensoriumd.pid", installer_text)
        self.assertIn("--socket-path \\${SENSORIUM_SOCKET_PATH}", installer_text)
        self.assertIn("--pidfile \\${SENSORIUM_PIDFILE_PATH}", installer_text)

    def test_source_checkout_installer_preserves_args_across_sudo(self):
        installer_text = (
            REPO_ROOT / "scripts" / "local" / "install-systemd-service.sh"
        ).read_text()
        self.assertIn('original_args=( "$@" )', installer_text)
        self.assertIn('exec sudo bash "$0" "${original_args[@]}"', installer_text)


class PackagingMetadataTests(unittest.TestCase):
    def test_scripts_root_contains_only_category_directories(self):
        root_entries = list(SCRIPTS_DIR.iterdir())
        self.assertTrue(root_entries)
        root_files = [path.name for path in root_entries if not path.is_dir()]
        self.assertEqual(root_files, [])

    def test_python_runtime_lives_in_src_package_with_stable_script_wrappers(self):
        self.assertTrue((REPO_ROOT / "src" / "sensorium" / "runtime" / "client.py").is_file())
        self.assertFalse((REPO_ROOT / "scripts" / "sensorium_runtime_client.py").exists())
        sensoriumd_wrapper = (
            REPO_ROOT / "scripts" / "runtime" / "sensoriumd"
        ).read_text()
        controller_wrapper = (
            REPO_ROOT / "scripts" / "runtime" / "runtime-controller-spi-flash.py"
        ).read_text()
        self.assertIn('Path(__file__).resolve().parents[2] / "src"', sensoriumd_wrapper)
        self.assertIn("from sensorium.cli.sensoriumd import main", sensoriumd_wrapper)
        self.assertIn(
            "from sensorium.controllers.spi_flash import main",
            controller_wrapper,
        )
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        self.assertIn('name = "sensorium-runtime"', pyproject)
        self.assertIn('sensoriumd = "sensorium.cli.sensoriumd:main"', pyproject)

    def test_arch_package_ships_models_and_runtime_python_deps(self):
        pkgbuild = (REPO_ROOT / "packaging" / "arch" / "PKGBUILD").read_text()
        for dep in ("bash", "dkms", "gcc", "make", "python", "python-pyserial", "python-yaml"):
            self.assertIn(dep, pkgbuild)
        self.assertIn("makedepends=('python' 'rsync')", pkgbuild)
        self.assertIn("config docs models src tools", pkgbuild)
        self.assertIn("rsync -a scripts/runtime", pkgbuild)
        self.assertIn("scripts/lib/sensorium-common.sh", pkgbuild)
        self.assertIn("python_sitelib", pkgbuild)
        self.assertIn("packaging/python/sensorium-runtime.pth", pkgbuild)
        self.assertIn("/usr/bin/sensoriumctl", pkgbuild)
        self.assertIn("/usr/bin/sensoriumd", pkgbuild)
        self.assertNotIn("scripts/qemu", pkgbuild)
        self.assertNotIn("scripts/remote", pkgbuild)
        self.assertNotIn("scripts/local", pkgbuild)
        self.assertNotIn("scripts/benchmarks", pkgbuild)
        self.assertNotIn("scripts/package", pkgbuild)
        self.assertNotIn("scripts/tools", pkgbuild)
        self.assertIn("pyproject.toml", pkgbuild)

    def test_alpine_package_ships_models_and_runtime_python_deps(self):
        apkbuild = (REPO_ROOT / "packaging" / "alpine" / "APKBUILD").read_text()
        for dep in ("akms", "bash", "python3", "py3-pyserial", "py3-yaml"):
            self.assertIn(dep, apkbuild)
        self.assertIn('makedepends="python3 rsync"', apkbuild)
        self.assertIn("AKMBUILD", apkbuild)
        self.assertIn("built_modules=\"sensorium.ko\"", apkbuild)
        self.assertIn("config docs models src tools", apkbuild)
        self.assertIn("rsync -a scripts/runtime", apkbuild)
        self.assertIn("scripts/lib/sensorium-common.sh", apkbuild)
        self.assertIn("python_sitelib", apkbuild)
        self.assertIn("packaging/python/sensorium-runtime.pth", apkbuild)
        self.assertIn("/usr/bin/sensoriumctl", apkbuild)
        self.assertIn("/usr/bin/sensoriumd", apkbuild)
        self.assertNotIn("scripts/qemu", apkbuild)
        self.assertNotIn("scripts/remote", apkbuild)
        self.assertNotIn("scripts/local", apkbuild)
        self.assertNotIn("scripts/benchmarks", apkbuild)
        self.assertNotIn("scripts/package", apkbuild)
        self.assertNotIn("scripts/tools", apkbuild)
        self.assertIn("pyproject.toml", apkbuild)

    def test_debian_package_ships_models_unit_and_runtime_python_deps(self):
        deb_build = (
            REPO_ROOT / "scripts" / "package" / "build-deb-package.sh"
        ).read_text()
        self.assertIn('"${sensorium_repo_root}/models"', deb_build)
        self.assertIn('"${sensorium_repo_root}/src"', deb_build)
        self.assertIn('"${sensorium_repo_root}/pyproject.toml"', deb_build)
        self.assertIn('"${sensorium_repo_root}/scripts/runtime"', deb_build)
        self.assertIn('"${sensorium_repo_root}/scripts/lib/sensorium-common.sh"', deb_build)
        self.assertIn("/usr/lib/python3/dist-packages", deb_build)
        self.assertIn("packaging/python/sensorium-runtime.pth", deb_build)
        self.assertIn("/usr/bin/sensoriumctl", deb_build)
        self.assertIn("/usr/bin/sensoriumd", deb_build)
        self.assertNotIn('"${sensorium_repo_root}/scripts"', deb_build)
        self.assertIn('install -m 0644 "${sensorium_repo_root}/packaging/systemd/sensoriumd.service"', deb_build)
        self.assertIn('install -m 0644 "${sensorium_repo_root}/packaging/systemd/sensoriumd.env.example"', deb_build)
        for dep in ("bash", "dkms", "gcc", "make", "python3", "python3-serial", "python3-yaml"):
            self.assertIn(dep, deb_build)

    def test_runtime_python_import_hook_is_packaged(self):
        pth = (REPO_ROOT / "packaging" / "python" / "sensorium-runtime.pth").read_text()
        self.assertEqual(pth.strip(), "/usr/share/sensorium/src")

    def test_package_common_excludes_generated_build_outputs(self):
        package_common = (
            REPO_ROOT / "scripts" / "lib" / "package-common.sh"
        ).read_text()
        for pattern in (
            "--exclude '*.o'",
            "--exclude '*.ko'",
            "--exclude '*.cmd'",
            "--exclude '.*.cmd'",
            "--exclude 'Module.symvers'",
            "--exclude 'modules.order'",
            "--exclude '*.egg-info'",
            "--exclude '*.egg-info/'",
            "--exclude 'tools/libcamera-capture'",
            "--exclude 'tools/libcamera-record'",
            "--exclude 'tools/rgb24-to-rggb10'",
        ):
            self.assertIn(pattern, package_common)
        self.assertIn('"${sensorium_package_rsync_excludes[@]}"', package_common)

    def test_make_test_disables_python_bytecode_writes(self):
        makefile = (REPO_ROOT / "Makefile").read_text()
        self.assertIn("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$(CURDIR)/src python3 -m unittest discover", makefile)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$(CURDIR)/src python3 ./scripts/local/verify-runtime-abi.py", makefile)


class RepoCheckTests(unittest.TestCase):
    def test_makefile_exposes_release_repo_check_target(self):
        makefile = (REPO_ROOT / "Makefile").read_text()
        self.assertIn("check-release:", makefile)
        self.assertIn("SENSORIUM_CHECK_PROFILE=release ./scripts/local/check-repo.sh", makefile)

    def test_makefile_exposes_linux7_qemu_targets(self):
        makefile = (REPO_ROOT / "Makefile").read_text()
        self.assertIn("qemu-linux7-e2e:", makefile)
        self.assertIn("qemu-linux7-ci-smoke:", makefile)
        self.assertIn("QEMU_EXPECT_KERNEL_MAJOR=7 ./scripts/qemu/qemu-e2e.sh", makefile)
        self.assertIn("QEMU_EXPECT_KERNEL_MAJOR=7 ./scripts/qemu/qemu-ci-smoke.sh", makefile)
        self.assertIn("QEMU_LIBCAMERA_APT_RELEASE=sid", makefile)
        self.assertIn("QEMU_CI_LIBCAMERA_APT_RELEASE=sid", makefile)

    def test_check_repo_allows_repo_cache_outside_release_profile(self):
        script = (REPO_ROOT / "scripts" / "local" / "check-repo.sh").read_text()
        self.assertIn("allow_repo_cache=1", script)
        self.assertIn('if [[ "${allow_repo_cache}" == "0" && -e "${repo_cache_path}" ]]; then', script)
        self.assertIn('Note: repo-root ${repo_cache_path} is present and allowed in the ${check_profile} profile.', script)

    def test_check_repo_rejects_kernel_artifacts_and_package_leaks(self):
        script = (REPO_ROOT / "scripts" / "local" / "check-repo.sh").read_text()
        self.assertIn("Checking for generated kernel build artifacts", script)
        self.assertIn("Checking package staging excludes", script)
        self.assertIn("Package staging would include generated artifacts", script)
        self.assertIn("Generated Python package metadata is present", script)
        self.assertIn("build", script)
        self.assertIn("src/*.egg-info/*", script)


class QemuRemoteHarnessTests(unittest.TestCase):
    def test_remote_common_exposes_no_stdin_ssh_helpers(self):
        script = (REPO_ROOT / "scripts" / "lib" / "remote-common.sh").read_text()
        self.assertIn("remote_ssh_no_stdin()", script)
        self.assertIn('ssh -n -p "${remote_port}"', script)
        self.assertIn("remote_ssh_retry_no_stdin()", script)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", script)
        self.assertIn("--exclude '*.egg-info'", script)
        self.assertIn("--exclude '*.egg-info/'", script)
        manifest = (
            REPO_ROOT / "scripts" / "tools" / "compute-sync-manifest.py"
        ).read_text()
        self.assertIn("sys.dont_write_bytecode = True", manifest)

    def test_qemu_wait_and_remote_kernel_wait_use_no_stdin_helpers(self):
        qemu_wait = (REPO_ROOT / "scripts" / "qemu" / "qemu-wait.sh").read_text()
        remote_kernel = (
            REPO_ROOT / "scripts" / "remote" / "remote-ensure-media-kernel.sh"
        ).read_text()
        qemu_common = (REPO_ROOT / "scripts" / "lib" / "qemu-common.sh").read_text()
        self.assertIn("qemu_ssh_no_stdin()", qemu_common)
        self.assertIn('qemu_ssh_no_stdin "sudo cloud-init status --wait >/dev/null 2>&1 || true"', qemu_wait)
        self.assertIn('remote_ssh_no_stdin "sudo cloud-init status --wait >/dev/null 2>&1 || true"', remote_kernel)

    def test_qemu_wrappers_can_assert_expected_kernel_major(self):
        qemu_common = (REPO_ROOT / "scripts" / "lib" / "qemu-common.sh").read_text()
        remote_assert = (
            REPO_ROOT / "scripts" / "remote" / "remote-assert-kernel-major.sh"
        ).read_text()
        provision = (REPO_ROOT / "scripts" / "remote" / "provision-droplet.sh").read_text()
        self.assertIn("qemu_assert_expected_kernel_major()", qemu_common)
        self.assertIn("remote-assert-kernel-major.sh", qemu_common)
        self.assertIn("REMOTE_EXPECT_KERNEL_MAJOR", remote_assert)
        self.assertIn("remote_ssh_retry_no_stdin \"uname -r\"", remote_assert)
        self.assertIn("remote_major=\"${remote_kernel%%.*}\"", remote_assert)
        self.assertIn("provision_profile=%s", provision)
        for script_name in (
            "qemu-e2e.sh",
            "qemu-ci-smoke.sh",
            "qemu-burnin.sh",
            "qemu-camera-matrix.sh",
            "qemu-benchmark.sh",
            "qemu-benchmark-matrix.sh",
        ):
            script = (REPO_ROOT / "scripts" / "qemu" / script_name).read_text()
            self.assertIn("qemu_assert_expected_kernel_major", script)

    def test_qemu_benchmark_uses_repo_cache_artifact_dir(self):
        for script_name in ("qemu-benchmark.sh", "qemu-benchmark-matrix.sh"):
            script = (REPO_ROOT / "scripts" / "qemu" / script_name).read_text()
            self.assertIn('benchmark_artifact_dir="${SENSORIUM_BENCHMARK_DIR:-${sensorium_repo_root}/.cache/benchmarks}"', script)
            self.assertIn('--artifact-dir "${benchmark_artifact_dir}"', script)


class KernelCompatibilityTests(unittest.TestCase):
    def test_iio_event_config_signature_switches_at_newer_kernel_abi(self):
        script = (REPO_ROOT / "kernel" / "sensorium-iio.c").read_text()
        self.assertIn("#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 16, 0)", script)
        self.assertIn("bool state)", script)
        self.assertIn("int state)", script)

    def test_spi_driver_override_handles_mainline_and_stable_backport_helpers(self):
        script = (REPO_ROOT / "kernel" / "sensorium-runtime-spi.c").read_text()
        self.assertIn("#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 19, 0)", script)
        self.assertIn("KERNEL_VERSION(6, 12, 80)", script)
        self.assertIn('ret = device_set_driver_override(&spi->dev, "spidev");', script)
        self.assertIn('ret = driver_set_override(&spi->dev, &spi->driver_override,', script)

    def test_vb2_wait_callbacks_are_omitted_on_linux_620_and_newer(self):
        header = (REPO_ROOT / "kernel" / "sensorium.h").read_text()
        self.assertIn("#include <linux/version.h>", header)
        for path in ("sensorium-capture.c", "sensorium-inject.c"):
            script = (REPO_ROOT / "kernel" / path).read_text()
            self.assertIn("#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 20, 0)", script)
            self.assertIn(".wait_prepare = vb2_ops_wait_prepare", script)
            self.assertIn(".wait_finish = vb2_ops_wait_finish", script)


if __name__ == "__main__":
    unittest.main()
