#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/sbin:/sbin:${PATH}"

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
model_path="${1:-${repo_root}/models/runtime/rpi-managed-workers.yaml}"

if [[ "${RUNTIME_SMOKE_SKIP_APPLY:-0}" != "1" ]]; then
	"${repo_root}/scripts/runtime/sensoriumctl" runtime apply "${model_path}"
fi

echo "Managed-worker runtime status:"
"${repo_root}/scripts/runtime/sensoriumctl" runtime status

echo
echo "Managed worker smoke:"
python3 - "${repo_root}" "${model_path}" <<'PY'
import ctypes
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

import serial

repo_root = Path(sys.argv[1])
model_path = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo_root / "src"))

from sensorium.runtime.client import SensoriumRuntimeClient
from sensorium.runtime.common import normalize_runtime_model

SPI_IOC_MAGIC = ord("k")
IOC_NRBITS = 8
IOC_TYPEBITS = 8
IOC_SIZEBITS = 14
IOC_DIRBITS = 2
IOC_NRSHIFT = 0
IOC_TYPESHIFT = IOC_NRSHIFT + IOC_NRBITS
IOC_SIZESHIFT = IOC_TYPESHIFT + IOC_TYPEBITS
IOC_DIRSHIFT = IOC_SIZESHIFT + IOC_SIZEBITS
IOC_WRITE = 1


def _IOC(direction, ioc_type, nr, size):
    return (
        (direction << IOC_DIRSHIFT)
        | (ioc_type << IOC_TYPESHIFT)
        | (nr << IOC_NRSHIFT)
        | (size << IOC_SIZESHIFT)
    )


def _IOW(ioc_type, nr, size):
    return _IOC(IOC_WRITE, ioc_type, nr, size)


class SpiIocTransfer(ctypes.Structure):
    _fields_ = [
        ("tx_buf", ctypes.c_uint64),
        ("rx_buf", ctypes.c_uint64),
        ("len", ctypes.c_uint32),
        ("speed_hz", ctypes.c_uint32),
        ("delay_usecs", ctypes.c_uint16),
        ("bits_per_word", ctypes.c_uint8),
        ("cs_change", ctypes.c_uint8),
        ("tx_nbits", ctypes.c_uint8),
        ("rx_nbits", ctypes.c_uint8),
        ("word_delay_usecs", ctypes.c_uint8),
        ("pad", ctypes.c_uint8),
    ]


def SPI_IOC_MESSAGE(count):
    return _IOW(SPI_IOC_MAGIC, 0, ctypes.sizeof(SpiIocTransfer) * count)


model = normalize_runtime_model(model_path)
client = SensoriumRuntimeClient()
status = client.status()
devices_by_id = {device["id"]: device for device in status["devices"]}
managed_devices = [
    device
    for device in model["runtime"]["devices"]
    if device["backend"]["kind"] == "controller" and device["backend"].get("worker")
]
assert managed_devices, "model does not define any managed controller workers"

for device in managed_devices:
    live = devices_by_id[device["id"]]
    worker = live.get("managed_worker")
    assert worker, (device["id"], live)
    assert worker["status"] == "running", (device["id"], worker)
    assert worker["pid"] and worker["pid"] > 0, (device["id"], worker)
print("  broker-managed worker processes are running")

buses = {bus["id"]: bus for bus in model["runtime"]["buses"]}

i2c_device = next(device for device in managed_devices if device["transport"] == "i2c")
i2c_bus_num = buses[i2c_device["bus"]]["name"].split("-", 1)[1]
scan = subprocess.check_output(["i2cdetect", "-y", i2c_bus_num], text=True)
addr_hex = f"{i2c_device['address']:02x}"
assert addr_hex in scan.lower(), scan
i2c_value = subprocess.check_output(
    ["i2cget", "-y", i2c_bus_num, f"0x{i2c_device['address']:02x}", "0x12"],
    text=True,
).strip().lower()
assert i2c_value == "0x12", i2c_value
print("  managed I2C worker path ok")

spi_device = next(device for device in managed_devices if device["transport"] == "spi")
spi_fd = os.open(f"/dev/{spi_device['device_name']}", os.O_RDWR | os.O_CLOEXEC)
try:
    tx = bytes.fromhex("9f0000")
    tx_buf = (ctypes.c_ubyte * len(tx)).from_buffer_copy(tx)
    rx_buf = (ctypes.c_ubyte * len(tx))()
    xfer = SpiIocTransfer(
        tx_buf=ctypes.addressof(tx_buf),
        rx_buf=ctypes.addressof(rx_buf),
        len=len(tx),
        speed_hz=spi_device["settings"]["max_speed_hz"],
        bits_per_word=spi_device["settings"]["bits_per_word"],
        tx_nbits=1,
        rx_nbits=1,
    )
    ret = fcntl.ioctl(spi_fd, SPI_IOC_MESSAGE(1), xfer)
    assert ret == len(tx), ret
    assert bytes(rx_buf)[:3] == bytes.fromhex("ef4018"), bytes(rx_buf).hex()
finally:
    os.close(spi_fd)
print("  managed SPI worker path ok")

uart_device = next(device for device in managed_devices if device["transport"] == "uart")
uart_port = serial.Serial(
    f"/dev/{uart_device['port_name']}",
    uart_device["settings"]["baud_rate"],
    timeout=0.3,
)
try:
    uart_port.write(b"AT\r\n")
    uart_port.flush()
    time.sleep(0.1)
    reply = uart_port.read(128)
    assert b"OK\r\n" in reply, reply
finally:
    uart_port.close()
print("  managed UART worker path ok")

pre_restart_state = devices_by_id[spi_device["id"]]["managed_worker"]
pre_restart_count = pre_restart_state["restart_count"]
pre_restart_pid = pre_restart_state["pid"]
subprocess.run(["sudo", "kill", "-KILL", str(pre_restart_pid)], check=True)

uart_port = serial.Serial(
    f"/dev/{uart_device['port_name']}",
    uart_device["settings"]["baud_rate"],
    timeout=0.3,
)
try:
    uart_port.write(b"AT\r\n")
    uart_port.flush()
    time.sleep(0.1)
    reply = uart_port.read(128)
    assert b"OK\r\n" in reply, reply
finally:
    uart_port.close()
print("  unrelated managed worker stayed responsive during SPI worker restart")

deadline = time.monotonic() + 5.0
restarted = None
while time.monotonic() < deadline:
    devices_by_id = {device["id"]: device for device in client.status()["devices"]}
    candidate = devices_by_id[spi_device["id"]]["managed_worker"]
    if (
        candidate["status"] == "running"
        and candidate["restart_count"] >= pre_restart_count + 1
        and candidate["pid"] != pre_restart_pid
    ):
        restarted = candidate
        break
    time.sleep(0.1)

assert restarted is not None, client.status()["devices"]
print(
    f"  managed SPI worker restarted after crash "
    f"(pid {pre_restart_pid} -> {restarted['pid']})"
)

spi_fd = os.open(f"/dev/{spi_device['device_name']}", os.O_RDWR | os.O_CLOEXEC)
try:
    tx = bytes.fromhex("9f0000")
    tx_buf = (ctypes.c_ubyte * len(tx)).from_buffer_copy(tx)
    rx_buf = (ctypes.c_ubyte * len(tx))()
    xfer = SpiIocTransfer(
        tx_buf=ctypes.addressof(tx_buf),
        rx_buf=ctypes.addressof(rx_buf),
        len=len(tx),
        speed_hz=spi_device["settings"]["max_speed_hz"],
        bits_per_word=spi_device["settings"]["bits_per_word"],
        tx_nbits=1,
        rx_nbits=1,
    )
    ret = fcntl.ioctl(spi_fd, SPI_IOC_MESSAGE(1), xfer)
    assert ret == len(tx), ret
    assert bytes(rx_buf)[:3] == bytes.fromhex("ef4018"), bytes(rx_buf).hex()
finally:
    os.close(spi_fd)
print("  restarted managed SPI worker resumed serving requests")

health = client.health()
assert health["bridge"]["worker_restarts"] >= 1, health
assert health["health"]["status"] in {"warn", "ok"}, health
print("  worker restart surfaced in runtime health/metrics")
PY
