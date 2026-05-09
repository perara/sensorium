#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/sbin:/sbin:${PATH}"

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
model_path="${1:-${repo_root}/models/runtime/rpi-multibus.yaml}"

if [[ "${RUNTIME_SMOKE_SKIP_APPLY:-0}" != "1" ]]; then
	"${repo_root}/scripts/runtime/sensoriumctl" runtime apply "${model_path}"
fi

echo "Runtime status:"
"${repo_root}/scripts/runtime/sensoriumctl" runtime status

echo
echo "I2C smoke:"
python3 - "${repo_root}" "${model_path}" <<'PY'
import ctypes
import fcntl
import errno
import os
import re
import time
import subprocess
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
model_path = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo_root / "src"))

from sensorium.runtime.common import (
    RUNTIME_BRIDGE_SEGMENT_PAYLOAD_LIMIT,
    RUNTIME_MAX_I2C_MSGS,
    normalize_runtime_model,
)

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001
I2C_RDWR_IOCTL_MAX_MSGS = 42


class I2cMsg(ctypes.Structure):
    _fields_ = [
        ("addr", ctypes.c_ushort),
        ("flags", ctypes.c_ushort),
        ("len", ctypes.c_ushort),
        ("buf", ctypes.c_void_p),
    ]


class I2cRdWrIoctlData(ctypes.Structure):
    _fields_ = [
        ("msgs", ctypes.POINTER(I2cMsg)),
        ("nmsgs", ctypes.c_uint),
    ]

model = normalize_runtime_model(model_path)
buses = {bus["id"]: bus for bus in model["runtime"]["buses"]}
i2c_devices = sorted(
    (device for device in model["runtime"]["devices"] if device["transport"] == "i2c"),
    key=lambda item: (buses[item["bus"]]["name"], item["address"], item["id"]),
)

print(subprocess.check_output(["i2cdetect", "-l"], text=True), end="")
groups = {}
for device in i2c_devices:
    groups.setdefault(device["bus"], []).append(device)

if not groups:
    print("  no I2C devices in model")

for bus_id, devices in groups.items():
    bus_name = buses[bus_id]["name"]
    match = re.fullmatch(r"i2c-(\d+)", bus_name)
    if not match:
        raise AssertionError(f"runtime smoke expects i2c-N bus names, got {bus_name!r}")
    bus_num = match.group(1)
    scan = subprocess.check_output(["i2cdetect", "-y", bus_num], text=True)
    print(scan, end="" if scan.endswith("\n") else "\n")
    for device in devices:
        addr_hex = f"{device['address']:02x}"
        if not re.search(rf"(^|\s){addr_hex}($|\s)", scan, re.MULTILINE):
            raise AssertionError(
                f"missing expected I2C address 0x{addr_hex} on bus {bus_num}"
            )
        backend = device["backend"]
        registers = backend.get("registers", {})
        if not registers:
            continue
        reg_key, expected = sorted(registers.items(), key=lambda item: int(item[0], 16))[0]
        reg_addr = int(reg_key, 16)
        expected_str = f"0x{expected:02x}"
        subprocess.run(
            ["i2cset", "-y", bus_num, f"0x{device['address']:02x}", f"0x{reg_addr:02x}", expected_str],
            check=True,
        )
        value = subprocess.check_output(
            ["i2cget", "-y", bus_num, f"0x{device['address']:02x}", f"0x{reg_addr:02x}"],
            text=True,
        ).strip().lower()
        assert value == expected_str, (device["id"], value, expected_str)

        clear_on_read = backend.get("clear_on_read", [])
        if clear_on_read:
            clear_reg = int(clear_on_read[0], 16)
            before = subprocess.check_output(
                ["i2cget", "-y", bus_num, f"0x{device['address']:02x}", f"0x{clear_reg:02x}"],
                text=True,
            ).strip().lower()
            after = subprocess.check_output(
                ["i2cget", "-y", bus_num, f"0x{device['address']:02x}", f"0x{clear_reg:02x}"],
                text=True,
            ).strip().lower()
            assert before != "0x00", (device["id"], before)
            assert after == "0x00", (device["id"], after)

        write_effects = backend.get("write_effects", {})
        if write_effects:
            source_reg, targets = next(iter(sorted(write_effects.items())))
            subprocess.run(
                [
                    "i2cset",
                    "-y",
                    bus_num,
                    f"0x{device['address']:02x}",
                    f"0x{int(source_reg, 16):02x}",
                    "0xaa",
                ],
                check=True,
            )
            for target_reg, target_value in sorted(targets.items()):
                actual = subprocess.check_output(
                    [
                        "i2cget",
                        "-y",
                        bus_num,
                        f"0x{device['address']:02x}",
                        f"0x{int(target_reg, 16):02x}",
                    ],
                    text=True,
                ).strip().lower()
                expected_value = f"0x{target_value:02x}"
                assert actual == expected_value, (device["id"], target_reg, actual, expected_value)

if RUNTIME_MAX_I2C_MSGS > 8 and i2c_devices:
    expanded_device = next(
        (
            device
            for device in i2c_devices
            if len(device["backend"].get("registers", {})) >= 2
            and not device["backend"].get("write_effects")
            and not device["backend"].get("clear_on_read")
        ),
        None,
    )
    if expanded_device is not None:
        registers = sorted(
            expanded_device["backend"]["registers"].items(),
            key=lambda item: int(item[0], 16),
        )[:2]
        bus_name = buses[expanded_device["bus"]]["name"]
        pairs = min(5, RUNTIME_MAX_I2C_MSGS // 2)
        messages = []
        keepalive = []
        expected = bytearray()
        for index in range(pairs):
            reg_key, value = registers[index % len(registers)]
            reg_addr = int(reg_key, 16)
            tx = (ctypes.c_ubyte * 1)(reg_addr)
            rx = (ctypes.c_ubyte * 1)()
            keepalive.extend([tx, rx])
            messages.append(
                I2cMsg(
                    addr=expanded_device["address"],
                    flags=0,
                    len=1,
                    buf=ctypes.cast(tx, ctypes.c_void_p).value,
                )
            )
            messages.append(
                I2cMsg(
                    addr=expanded_device["address"],
                    flags=I2C_M_RD,
                    len=1,
                    buf=ctypes.cast(rx, ctypes.c_void_p).value,
                )
            )
            expected.append(value)

        msg_array = (I2cMsg * len(messages))(*messages)
        ioctl_data = I2cRdWrIoctlData(msgs=msg_array, nmsgs=len(messages))
        fd = os.open(f"/dev/{bus_name}", os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.ioctl(fd, I2C_RDWR, ioctl_data)
        finally:
            os.close(fd)

        actual = bytes(keepalive[index * 2 + 1][0] for index in range(pairs))
        assert actual == bytes(expected), (expanded_device["id"], actual, bytes(expected))
        print(f"  expanded I2C_RDWR path ok with {len(messages)} messages")

        large_pairs = min(RUNTIME_MAX_I2C_MSGS - 1, I2C_RDWR_IOCTL_MAX_MSGS - 1)
        large_chunk_len = 6656
        if large_pairs > 0:
            for attempt in range(3):
                pointer_tx = (ctypes.c_ubyte * 1)(0x00)
                keepalive.append(pointer_tx)
                messages = [
                    I2cMsg(
                        addr=expanded_device["address"],
                        flags=0,
                        len=1,
                        buf=ctypes.cast(pointer_tx, ctypes.c_void_p).value,
                    )
                ]
                rx_buffers = []
                for _ in range(large_pairs):
                    rx = (ctypes.c_ubyte * large_chunk_len)()
                    keepalive.append(rx)
                    rx_buffers.append(rx)
                    messages.append(
                        I2cMsg(
                            addr=expanded_device["address"],
                            flags=I2C_M_RD,
                            len=large_chunk_len,
                            buf=ctypes.cast(rx, ctypes.c_void_p).value,
                        )
                    )

                msg_array = (I2cMsg * len(messages))(*messages)
                ioctl_data = I2cRdWrIoctlData(msgs=msg_array, nmsgs=len(messages))
                fd = os.open(f"/dev/{bus_name}", os.O_RDWR | os.O_CLOEXEC)
                try:
                    fcntl.ioctl(fd, I2C_RDWR, ioctl_data)
                    break
                except TimeoutError as exc:
                    if exc.errno != errno.ETIMEDOUT or attempt == 2:
                        raise
                    time.sleep(0.2)
                finally:
                    os.close(fd)

            total_rx = sum(len(bytes(buffer)) for buffer in rx_buffers)
            assert total_rx > RUNTIME_BRIDGE_SEGMENT_PAYLOAD_LIMIT, total_rx
            assert any(buffer[0] == 0x00 for buffer in rx_buffers), expanded_device["id"]
            print(
                f"  segmented I2C_RDWR payload path ok at {total_rx} bytes "
                f"across {len(messages)} messages"
            )

print(f"  verified {len(i2c_devices)} I2C target(s) across {len(groups)} bus(es)")
PY

echo
echo "SPI smoke:"
python3 - "${repo_root}" "${model_path}" <<'PY'
import ctypes
import fcntl
import os
from pathlib import Path
import sys

repo_root = Path(sys.argv[1])
model_path = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo_root / "src"))

from sensorium.runtime.common import (
    RUNTIME_BRIDGE_SEGMENT_PAYLOAD_LIMIT,
    RUNTIME_MAX_SPI_XFERS,
    normalize_runtime_model,
    rpc_call,
)

model = normalize_runtime_model(model_path)
buses = {bus["id"]: bus for bus in model["runtime"]["buses"]}
spi_devices = sorted(
    (device for device in model["runtime"]["devices"] if device["transport"] == "spi"),
    key=lambda item: (buses[item["bus"]]["name"], item["chip_select"], item["id"]),
)

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
IOC_READ = 2

def _IOC(direction, ioc_type, nr, size):
    return (
        (direction << IOC_DIRSHIFT)
        | (ioc_type << IOC_TYPESHIFT)
        | (nr << IOC_NRSHIFT)
        | (size << IOC_SIZESHIFT)
    )

def _IOW(ioc_type, nr, size):
    return _IOC(IOC_WRITE, ioc_type, nr, size)

def _IOR(ioc_type, nr, size):
    return _IOC(IOC_READ, ioc_type, nr, size)

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

SPI_IOC_RD_MODE = _IOR(SPI_IOC_MAGIC, 1, 1)
SPI_IOC_RD_BITS_PER_WORD = _IOR(SPI_IOC_MAGIC, 3, 1)
SPI_IOC_RD_MAX_SPEED_HZ = _IOR(SPI_IOC_MAGIC, 4, 4)

trace_expectations = {}
if not spi_devices:
    print("  no SPI devices in model")
else:
    for index, device in enumerate(spi_devices, start=1):
        backend = device["backend"]
        if backend.get("flash_jedec_id"):
            tx_hex = "9f0000"
            rx_hex = backend["flash_jedec_id"]
        elif backend.get("responses"):
            tx_hex, rx_hex = sorted(backend["responses"].items())[0]
        elif backend.get("prefix_responses"):
            tx_hex, rx_hex = sorted(backend["prefix_responses"].items())[0]
        elif backend.get("echo"):
            tx_hex = "9f0000"
            rx_hex = tx_hex
        else:
            tx_hex = "000000"
            rx_hex = backend.get("default_response", "")

        tx_bytes = bytes.fromhex(tx_hex)
        expected = bytes.fromhex(rx_hex)
        if len(expected) < len(tx_bytes):
            expected = expected + (b"\0" * (len(tx_bytes) - len(expected)))
        expected = expected[: len(tx_bytes)]
        tx = (ctypes.c_ubyte * len(tx_bytes)).from_buffer_copy(tx_bytes)
        rx = (ctypes.c_ubyte * len(tx_bytes))()
        transfer = SpiIocTransfer(
            tx_buf=ctypes.addressof(tx),
            rx_buf=ctypes.addressof(rx),
            len=len(tx_bytes),
            speed_hz=device["settings"].get("max_speed_hz", 500000),
            delay_usecs=index * 3,
            bits_per_word=device["settings"].get("bits_per_word", 8),
            cs_change=index % 2,
            tx_nbits=1,
            rx_nbits=1,
            word_delay_usecs=index,
        )
        fd = os.open(f"/dev/{device['device_name']}", os.O_RDWR | os.O_CLOEXEC)
        try:
            mode_buf = bytearray(1)
            bits_buf = bytearray(1)
            speed_buf = bytearray(4)
            fcntl.ioctl(fd, SPI_IOC_RD_MODE, mode_buf, True)
            fcntl.ioctl(fd, SPI_IOC_RD_BITS_PER_WORD, bits_buf, True)
            fcntl.ioctl(fd, SPI_IOC_RD_MAX_SPEED_HZ, speed_buf, True)
            assert mode_buf[0] == device["settings"].get("mode", 0), (
                device["id"],
                mode_buf[0],
                device["settings"].get("mode", 0),
            )
            assert bits_buf[0] == device["settings"].get("bits_per_word", 8), (
                device["id"],
                bits_buf[0],
                device["settings"].get("bits_per_word", 8),
            )
            assert int.from_bytes(speed_buf, "little") == device["settings"].get("max_speed_hz", 500000), (
                device["id"],
                int.from_bytes(speed_buf, "little"),
                device["settings"].get("max_speed_hz", 500000),
            )
            ret = fcntl.ioctl(fd, SPI_IOC_MESSAGE(1), transfer)
        finally:
            os.close(fd)
        assert ret == len(tx_bytes)
        assert bytes(rx) == expected, (device["id"], bytes(rx).hex(), expected.hex())
        trace_expectations[device["id"]] = {
            "delay_usecs": index * 3,
            "word_delay_usecs": index,
            "cs_change": index % 2,
        }

    print(
        "  scripted SPI responses ok on "
        + ", ".join(device["device_name"] for device in spi_devices)
    )
    print("  SPI device defaults reflect model mode/bits/speed settings")

    events = rpc_call("trace.list", {"limit": max(32, len(spi_devices) * 8)}, timeout=5.0)["events"]
    for device in spi_devices:
        event = next(
            event
            for event in reversed(events)
            if event["transport"] == "spi" and event["device_id"] == device["id"]
        )
        transfer = event["request"]["transfers"][0]
        expected = trace_expectations[device["id"]]
        assert transfer["delay_usecs"] == expected["delay_usecs"]
        assert transfer["word_delay_usecs"] == expected["word_delay_usecs"]
        assert transfer["cs_change"] == expected["cs_change"]

    print("  SPI timing metadata reached the runtime trace")

    flash_device = next(
        (device for device in spi_devices if device["backend"].get("flash_jedec_id")),
        None,
    )
    if flash_device is not None:
        fd = os.open(f"/dev/{flash_device['device_name']}", os.O_RDWR | os.O_CLOEXEC)
        try:
            def run_simple_transfer(tx_bytes):
                tx = (ctypes.c_ubyte * len(tx_bytes)).from_buffer_copy(tx_bytes)
                rx = (ctypes.c_ubyte * len(tx_bytes))()
                transfer = SpiIocTransfer(
                    tx_buf=ctypes.addressof(tx),
                    rx_buf=ctypes.addressof(rx),
                    len=len(tx_bytes),
                    speed_hz=flash_device["settings"].get("max_speed_hz", 500000),
                    bits_per_word=flash_device["settings"].get("bits_per_word", 8),
                    tx_nbits=1,
                    rx_nbits=1,
                )
                ret = fcntl.ioctl(fd, SPI_IOC_MESSAGE(1), transfer)
                assert ret == len(tx_bytes)
                return bytes(rx)

            jedec = run_simple_transfer(bytes.fromhex("9f0000"))
            assert jedec.startswith(bytes.fromhex(flash_device["backend"]["flash_jedec_id"]))

            run_simple_transfer(bytes.fromhex("06"))
            status = run_simple_transfer(bytes.fromhex("05"))
            assert status[0] & 0x02, status

            run_simple_transfer(bytes.fromhex("011c"))
            status_busy = run_simple_transfer(bytes.fromhex("05"))
            status_final = run_simple_transfer(bytes.fromhex("05"))
            assert status_busy[0] & 0x01, status_busy
            assert not (status_final[0] & 0x02), status_final
        finally:
            os.close(fd)
        print(f"  stateful SPI flash path ok on {flash_device['device_name']}")

if RUNTIME_MAX_SPI_XFERS > 16 and spi_devices:
    expanded_device = next(
        (
            device
            for device in spi_devices
            if device["backend"].get("echo") and not device["backend"].get("flash_jedec_id")
        ),
        None,
    )
    if expanded_device is not None:
        transfer_count = min(20, RUNTIME_MAX_SPI_XFERS)
        xfers = (SpiIocTransfer * transfer_count)()
        tx_buffers = []
        rx_buffers = []
        expected = bytearray()
        for index in range(transfer_count):
            tx = (ctypes.c_ubyte * 1)(index & 0xFF)
            rx = (ctypes.c_ubyte * 1)()
            tx_buffers.append(tx)
            rx_buffers.append(rx)
            expected.append(index & 0xFF)
            xfers[index] = SpiIocTransfer(
                tx_buf=ctypes.addressof(tx),
                rx_buf=ctypes.addressof(rx),
                len=1,
                speed_hz=expanded_device["settings"].get("max_speed_hz", 500000),
                delay_usecs=0,
                bits_per_word=expanded_device["settings"].get("bits_per_word", 8),
                cs_change=0,
                tx_nbits=1,
                rx_nbits=1,
                word_delay_usecs=0,
            )

        fd = os.open(f"/dev/{expanded_device['device_name']}", os.O_RDWR | os.O_CLOEXEC)
        try:
            ret = fcntl.ioctl(fd, SPI_IOC_MESSAGE(transfer_count), xfers)
        finally:
            os.close(fd)
        assert ret == transfer_count
        actual = bytes(buffer[0] for buffer in rx_buffers)
        assert actual == bytes(expected), (expanded_device["id"], actual, bytes(expected))

        events = rpc_call("trace.list", {"limit": max(64, transfer_count * 4)}, timeout=5.0)["events"]
        event = next(
            event
            for event in reversed(events)
            if event["transport"] == "spi" and event["device_id"] == expanded_device["id"]
        )
        assert len(event["request"]["transfers"]) == transfer_count
        print(f"  expanded SPI_IOC_MESSAGE path ok with {transfer_count} transfers")

PY

echo
echo "UART smoke:"
python3 - "${repo_root}" "${model_path}" <<'PY'
import time
from pathlib import Path
import sys

import serial

repo_root = Path(sys.argv[1])
model_path = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo_root / "src"))

from sensorium.runtime.common import normalize_runtime_model

model = normalize_runtime_model(model_path)
buses = {bus["id"]: bus for bus in model["runtime"]["buses"]}
uart_devices = sorted(
    (device for device in model["runtime"]["devices"] if device["transport"] == "uart"),
    key=lambda item: (buses[item["bus"]]["name"], item["port_name"], item["id"]),
)
low_baud_device_ids = {
    device["id"] for device in uart_devices if device["settings"]["baud_rate"] <= 9600
}
if not low_baud_device_ids and uart_devices:
    low_baud_device_ids = {uart_devices[-1]["id"]}

def read_for(port, seconds):
    deadline = time.monotonic() + seconds
    chunks = bytearray()
    while time.monotonic() < deadline:
        chunk = port.read(512)
        if chunk:
            chunks.extend(chunk)
            continue
        time.sleep(0.02)
    return bytes(chunks)

low_baud_results = []
verified = []
for device in uart_devices:
    backend = device["backend"]
    port_baud = 9600 if device["id"] in low_baud_device_ids else device["settings"]["baud_rate"]
    port = serial.Serial(f"/dev/{device['port_name']}", port_baud, timeout=0.2)
    try:
        payload_echo = b""
        if device["id"] in low_baud_device_ids:
            payload_echo = (b"Z" * 192) + b"\r\n"
            started = time.monotonic()
            port.write(payload_echo)
            port.flush()
            elapsed = time.monotonic() - started
            data = read_for(port, 0.2)
            assert elapsed > 0.05, (device["id"], elapsed)
            if backend.get("echo", True):
                assert payload_echo in data, (device["id"], payload_echo, data)
            low_baud_results.append((device["port_name"], elapsed))

        if backend.get("line_responses"):
            command, reply = next(iter(backend["line_responses"].items()))
            tx = command.encode("utf-8") + b"\r\n"
            expected_reply = reply.encode("utf-8")
        elif backend.get("binary_responses"):
            tx_hex, reply_hex = next(iter(backend["binary_responses"].items()))
            tx = bytes.fromhex(tx_hex)
            expected_reply = bytes.fromhex(reply_hex)
        else:
            tx = b"AT\r\n"
            expected_reply = b""

        port.write(tx)
        port.flush()
        data = read_for(port, 0.25)
        if backend.get("echo", True):
            assert tx in data, (device["id"], tx, data)
        if expected_reply:
            assert expected_reply in data, (device["id"], expected_reply, data)
        if backend.get("cts_follows_rts"):
            port.rts = False
            time.sleep(0.05)
            assert not port.cts, (device["id"], "cts-low")
            port.rts = True
            time.sleep(0.05)
            assert port.cts, (device["id"], "cts-high")
        if backend.get("carrier_follows_dtr"):
            port.dtr = False
            time.sleep(0.05)
            assert not port.dsr and not port.cd, (device["id"], "carrier-low")
            port.dtr = True
            time.sleep(0.05)
            assert port.dsr and port.cd, (device["id"], "carrier-high")
        verified.append(device["port_name"])
    finally:
        port.close()

summary = "  pyserial path ok on " + ", ".join(verified)
if low_baud_results:
    summary += " (" + ", ".join(f"{name} flush {elapsed:.3f}s" for name, elapsed in low_baud_results) + ")"
print(summary)
PY
