import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sensorium.runtime import model as runtime_model


class RuntimeModelNormalizationTests(unittest.TestCase):
    def test_normalize_runtime_model_data_accepts_valid_minimal_model(self):
        model = runtime_model.normalize_runtime_model_data(
            {
                "name": "runtime-test",
                "adapter": "runtime",
                "runtime": {
                    "buses": [{"id": "i2c-main", "transport": "i2c", "name": "i2c-1"}],
                    "devices": [
                        {
                            "id": "env",
                            "bus": "i2c-main",
                            "transport": "i2c",
                            "address": "0x76",
                            "backend": {"kind": "template", "template": "i2c-register-bank"},
                        }
                    ],
                },
            }
        )
        self.assertEqual(model["schema_version"], runtime_model.RUNTIME_MODEL_SCHEMA_VERSION)
        self.assertEqual(model["runtime"]["buses"][0]["name"], "i2c-1")
        self.assertEqual(model["runtime"]["devices"][0]["address"], 0x76)

    def test_rejects_transport_mismatch_between_bus_and_device(self):
        with self.assertRaisesRegex(runtime_model.RuntimeModelError, "does not match bus"):
            runtime_model.normalize_runtime_model_data(
                {
                    "name": "runtime-test",
                    "adapter": "runtime",
                    "runtime": {
                        "buses": [{"id": "i2c-main", "transport": "i2c", "name": "i2c-1"}],
                        "devices": [
                            {
                                "id": "flash",
                                "bus": "i2c-main",
                                "transport": "spi",
                                "chip_select": 0,
                                "device_name": "spidev1.0",
                                "backend": {"kind": "template", "template": "spi-script"},
                            }
                        ],
                    },
                }
            )

    def test_rejects_duplicate_spi_location(self):
        with self.assertRaisesRegex(runtime_model.RuntimeModelError, "duplicate spi chip_select"):
            runtime_model.normalize_runtime_model_data(
                {
                    "name": "runtime-test",
                    "adapter": "runtime",
                    "runtime": {
                        "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                        "devices": [
                            {
                                "id": "flash-a",
                                "bus": "spi-main",
                                "transport": "spi",
                                "chip_select": 0,
                                "backend": {"kind": "template", "template": "spi-script"},
                            },
                            {
                                "id": "flash-b",
                                "bus": "spi-main",
                                "transport": "spi",
                                "chip_select": 0,
                                "backend": {"kind": "template", "template": "spi-script"},
                            },
                        ],
                    },
                }
            )

    def test_rejects_noncanonical_uart_name(self):
        buses = {"uart-main": {"id": "uart-main", "transport": "uart", "name": "ttyAMA"}}
        with self.assertRaisesRegex(runtime_model.RuntimeModelError, "canonical numeric suffix"):
            runtime_model.normalize_runtime_device_item(
                {
                    "id": "console",
                    "bus": "uart-main",
                    "transport": "uart",
                    "port_name": "ttyAMA01",
                    "backend": {"kind": "template", "template": "uart-script"},
                },
                buses,
                "device",
            )

    def test_accepts_controller_worker_definition(self):
        model = runtime_model.normalize_runtime_model_data(
            {
                "name": "runtime-workers",
                "adapter": "runtime",
                "runtime": {
                    "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                    "devices": [
                        {
                            "id": "flash",
                            "bus": "spi-main",
                            "transport": "spi",
                            "chip_select": 0,
                            "backend": {
                                "kind": "controller",
                                "worker": {
                                    "command": ["python3", "scripts/runtime/runtime-controller-spi-flash.py"],
                                    "restart_limit": 2,
                                    "restart_backoff_ms": 100,
                                    "cwd": ".",
                                    "env": {"PYTHONPATH": "src"},
                                },
                            },
                        }
                    ],
                },
            }
        )
        backend = model["runtime"]["devices"][0]["backend"]["worker"]
        self.assertEqual(backend["command"][1], "scripts/runtime/runtime-controller-spi-flash.py")
        self.assertEqual(backend["restart_limit"], 2)
        self.assertEqual(backend["restart_backoff_ms"], 100)
        self.assertEqual(backend["cwd"], ".")
        self.assertEqual(backend["env"]["PYTHONPATH"], "src")

    def test_rejects_empty_controller_worker_command(self):
        with self.assertRaisesRegex(runtime_model.RuntimeModelError, "must not be empty"):
            runtime_model.normalize_runtime_model_data(
                {
                    "name": "runtime-workers",
                    "adapter": "runtime",
                    "runtime": {
                        "buses": [{"id": "spi-main", "transport": "spi", "name": "spi0"}],
                        "devices": [
                            {
                                "id": "flash",
                                "bus": "spi-main",
                                "transport": "spi",
                                "chip_select": 0,
                                "backend": {
                                    "kind": "controller",
                                    "worker": {"command": []},
                                },
                            }
                        ],
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
