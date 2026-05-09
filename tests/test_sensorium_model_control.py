import shutil
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
import sys

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sensorium import model_control


def write_yaml(path: Path, content: str):
    path.write_text(content, encoding="utf-8")


class ModuleStateMatchTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="sensorium-model-control-"))

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_camera_model_matches_repeat_last_frame_false(self):
        path = self.tempdir / "camera.yaml"
        write_yaml(
            path,
            """
name: demo-camera
adapter: camera
transport: virtual
timing:
  repeat_last_frame: false
config:
  camera:
    family: imx
    sensor: imx219
""",
        )
        model = model_control.normalize_model(path)
        state = {
            "adapter": "camera",
            "transport": "virtual",
            "instance": "demo-camera",
            "transport_device_name": "",
            "fault_mode": "none",
            "family": "imx",
            "sensor": "imx219",
            "repeat_last_frame": "N",
        }

        self.assertTrue(model_control.model_matches_module_state(model, state))

    def test_iio_model_matches_full_module_param_set(self):
        path = self.tempdir / "iio.yaml"
        write_yaml(
            path,
            """
name: env-plus
adapter: iio
transport: i2c
config:
  transport:
    device_name: i2c-7
    address: 0x44
  iio:
    profile: environment-plus
    temperature_millic: 22000
    pressure_pascal: 100000
    temperature_step_millic: 50
    pressure_step_pascal: 42
    humidity_millipercent: 41000
    humidity_step_millipercent: 100
    temperature_thresh_rising_millic: 25500
timing:
  update_interval_ms: 250
""",
        )
        model = model_control.normalize_model(path)
        state = {
            "adapter": "iio",
            "transport": "i2c",
            "instance": "env-plus",
            "transport_device_name": "i2c-7",
            "i2c_address": "68",
            "fault_mode": "none",
            "iio_profile": "environment-plus",
            "iio_temperature_millic": "22000",
            "iio_pressure_pascal": "100000",
            "iio_temperature_step_millic": "50",
            "iio_pressure_step_pascal": "42",
            "iio_humidity_millipercent": "41000",
            "iio_humidity_step_millipercent": "100",
            "iio_temperature_thresh_rising_millic": "25500",
            "update_interval_ms": "250",
        }

        self.assertTrue(model_control.model_matches_module_state(model, state))

    def test_iio_model_detects_param_mismatch(self):
        path = self.tempdir / "iio.yaml"
        write_yaml(
            path,
            """
name: env-basic
adapter: iio
transport: spi
config:
  transport:
    device_name: spidev0.0
  iio:
    profile: environment-basic
    temperature_millic: 21500
    pressure_pascal: 101325
timing:
  update_interval_ms: 1000
""",
        )
        model = model_control.normalize_model(path)
        state = {
            "adapter": "iio",
            "transport": "spi",
            "instance": "env-basic",
            "transport_device_name": "spidev0.0",
            "fault_mode": "none",
            "iio_profile": "environment-basic",
            "iio_temperature_millic": "21500",
            "iio_pressure_pascal": "101325",
            "iio_temperature_step_millic": "250",
            "iio_pressure_step_pascal": "120",
            "iio_humidity_millipercent": "45500",
            "iio_humidity_step_millipercent": "350",
            "iio_temperature_thresh_rising_millic": "26000",
            "update_interval_ms": "750",
        }

        self.assertFalse(model_control.model_matches_module_state(model, state))


if __name__ == "__main__":
    unittest.main()
