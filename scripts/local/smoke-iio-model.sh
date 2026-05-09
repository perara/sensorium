#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/sbin:/sbin:${PATH}"

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
model_path="${1:-${repo_root}/models/iio/environment-i2c.yaml}"
iio_smoke_skip_apply="${IIO_SMOKE_SKIP_APPLY:-0}"

run_root() {
	if [[ "${EUID}" -eq 0 ]]; then
		"$@"
	else
		sudo "$@"
	fi
}

write_root_value() {
	local value="$1"
	local path="$2"

	if [[ "${EUID}" -eq 0 ]]; then
		printf '%s\n' "${value}" >"${path}"
	else
		printf '%s\n' "${value}" | sudo tee "${path}" >/dev/null
	fi
}

find_sensorium_iio_device() {
	local dev

	for dev in /sys/bus/iio/devices/iio:device*; do
		[[ -e "${dev}/name" ]] || continue
		if grep -Eq '^(env-|environment-|sensorium)' "${dev}/name"; then
			printf '%s\n' "${dev}"
			return 0
		fi
	done

	return 1
}

if [[ "${iio_smoke_skip_apply}" != "1" ]]; then
	"${repo_root}/scripts/runtime/sensoriumctl" apply "${model_path}"
fi

readarray -t iio_model_info < <(python3 - "${model_path}" <<'PY'
import sys
import yaml
from pathlib import Path

path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text())
iio = (((data or {}).get("config") or {}).get("iio") or {})
print(iio.get("profile", "environment-basic"))
print(iio.get("temperature_thresh_rising_millic", 26000))
PY
)
iio_profile="${iio_model_info[0]:-environment-basic}"
iio_temp_thresh="${iio_model_info[1]:-26000}"

iio_dev="$(find_sensorium_iio_device)"
if [[ -z "${iio_dev}" ]]; then
	echo "No sensorium IIO device found under /sys/bus/iio/devices" >&2
	exit 1
fi

find_iio_attr() {
	local pattern
	local match

	for pattern in "$@"; do
		for match in ${pattern}; do
			if [[ -e "${match}" ]]; then
				printf '%s\n' "${match}"
				return 0
			fi
		done
	done

	return 1
}

echo "IIO device: ${iio_dev}"
echo "name: $(<"${iio_dev}/name")"

temp_input_path="$(find_iio_attr "${iio_dev}/in_temp*_input" "${iio_dev}/in_temp_input" || true)"
pressure_input_path="$(find_iio_attr "${iio_dev}/in_pressure*_input" "${iio_dev}/in_pressure_input" || true)"

for channel_path in "${temp_input_path}" "${pressure_input_path}"; do
	[[ -n "${channel_path}" ]] || continue
	echo "$(basename "${channel_path}"): $(<"${channel_path}")"
done

if [[ "${iio_profile}" == "environment-plus" ]]; then
	humidity_input_path="$(find_iio_attr "${iio_dev}/in_humidityrelative*_input" "${iio_dev}/in_humidityrelative_input" || true)"
	temp_calibbias_path="$(find_iio_attr "${iio_dev}/in_temp*_calibbias" "${iio_dev}/in_temp_calibbias" || true)"
	pressure_calibbias_path="$(find_iio_attr "${iio_dev}/in_pressure*_calibbias" "${iio_dev}/in_pressure_calibbias" || true)"
	humidity_calibbias_path="$(find_iio_attr "${iio_dev}/in_humidityrelative*_calibbias" "${iio_dev}/in_humidityrelative_calibbias" || true)"
	temp_thresh_enable_path="$(find_iio_attr "${iio_dev}/events/in_temp*_thresh_rising_en" "${iio_dev}/events/in_temp_thresh_rising_en" || true)"
	temp_thresh_value_path="$(find_iio_attr "${iio_dev}/events/in_temp*_thresh_rising_value" "${iio_dev}/events/in_temp_thresh_rising_value" || true)"

	if [[ -z "${humidity_input_path}" ]]; then
		echo "Expected humidity channel for profile ${iio_profile}, but none was found." >&2
		exit 1
	fi
	echo "$(basename "${humidity_input_path}"): $(<"${humidity_input_path}")"

	echo
	echo "Writable IIO calibration smoke:"
	if [[ -z "${temp_calibbias_path}" || -z "${pressure_calibbias_path}" || -z "${humidity_calibbias_path}" ]]; then
		echo "Expected calibration-bias controls for profile ${iio_profile}" >&2
		exit 1
	fi
	write_root_value 125 "${temp_calibbias_path}"
	temp_bias="$(<"${temp_calibbias_path}")"
	if [[ "${temp_bias}" != "125" ]]; then
		echo "Unexpected $(basename "${temp_calibbias_path}") after write: ${temp_bias}" >&2
		exit 1
	fi
	write_root_value -42 "${pressure_calibbias_path}"
	pressure_bias="$(<"${pressure_calibbias_path}")"
	if [[ "${pressure_bias}" != "-42" ]]; then
		echo "Unexpected $(basename "${pressure_calibbias_path}") after write: ${pressure_bias}" >&2
		exit 1
	fi
	write_root_value 77 "${humidity_calibbias_path}"
	humidity_bias="$(<"${humidity_calibbias_path}")"
	if [[ "${humidity_bias}" != "77" ]]; then
		echo "Unexpected $(basename "${humidity_calibbias_path}") after write: ${humidity_bias}" >&2
		exit 1
	fi

	echo
	echo "IIO threshold-event config smoke:"
	if [[ -z "${temp_thresh_enable_path}" || -z "${temp_thresh_value_path}" ]]; then
		echo "Expected temp threshold event controls for profile ${iio_profile}" >&2
		exit 1
	fi
	write_root_value "${iio_temp_thresh}" "${temp_thresh_value_path}"
	write_root_value 1 "${temp_thresh_enable_path}"
	event_value="$(<"${temp_thresh_value_path}")"
	event_enable="$(<"${temp_thresh_enable_path}")"
	if [[ "${event_value}" != "${iio_temp_thresh}" ]]; then
		echo "Unexpected $(basename "${temp_thresh_value_path}") after write: ${event_value}" >&2
		exit 1
	fi
	if [[ "${event_enable}" != "1" ]]; then
		echo "Unexpected $(basename "${temp_thresh_enable_path}") after write: ${event_enable}" >&2
		exit 1
	fi
fi

echo
echo "Module parameters:"
for param in adapter transport instance transport_device_name i2c_address fault_mode iio_profile iio_temperature_millic iio_pressure_pascal iio_humidity_millipercent iio_temperature_thresh_rising_millic; do
	path="/sys/module/sensorium/parameters/${param}"
	[[ -f "${path}" ]] || continue
	echo "  ${param}: $(run_root cat "${path}")"
done

transport_name="$(run_root cat /sys/module/sensorium/parameters/transport 2>/dev/null || true)"
transport_device_name="$(run_root cat /sys/module/sensorium/parameters/transport_device_name 2>/dev/null || true)"
i2c_address_raw="$(run_root cat /sys/module/sensorium/parameters/i2c_address 2>/dev/null || true)"
if [[ -n "${transport_device_name}" ]]; then
	transport_device_path="/dev/${transport_device_name}"
	if [[ ! -e "${transport_device_path}" ]]; then
		echo "Expected transport alias node is missing: ${transport_device_path}" >&2
		exit 1
	fi
	echo
	echo "Transport alias: ${transport_device_path}"
	ls -l "${transport_device_path}"

	case "${transport_name}" in
	i2c)
		if [[ ! "${transport_device_name}" =~ ^i2c-([0-9]+)$ ]]; then
			echo "I2C transport alias must use an i2c-N name for i2c-tools compatibility." >&2
			exit 1
		fi
		if ! command -v i2cdetect >/dev/null 2>&1 || \
		   ! command -v i2cset >/dev/null 2>&1 || \
		   ! command -v i2cget >/dev/null 2>&1 || \
		   ! command -v i2cdump >/dev/null 2>&1; then
			echo "Missing i2c-tools commands (i2cdetect/i2cset/i2cget/i2cdump)." >&2
			exit 1
		fi

		i2c_bus="${BASH_REMATCH[1]}"
		i2c_address_raw="${i2c_address_raw:-118}"
		printf -v i2c_address_hex '0x%02x' "${i2c_address_raw}"
		printf -v i2c_address_token '%02x' "${i2c_address_raw}"
		echo "I2C consumer smoke:"
		i2cdetect_list="$(i2cdetect -l)"
		printf '%s\n' "${i2cdetect_list}"
		if ! grep -q "^i2c-${i2c_bus}[[:space:]]" <<<"${i2cdetect_list}"; then
			echo "i2cdetect -l did not list i2c-${i2c_bus}" >&2
			exit 1
		fi

		i2cdetect_scan="$(i2cdetect -y "${i2c_bus}")"
		printf '%s\n' "${i2cdetect_scan}"
		if ! grep -Eq "(^|[[:space:]])${i2c_address_token}($|[[:space:]])" <<<"${i2cdetect_scan}"; then
			echo "i2cdetect did not find ${i2c_address_hex} on bus ${i2c_bus}" >&2
			exit 1
		fi

		i2cset -y "${i2c_bus}" "${i2c_address_hex}" 0x10 0x23
		byte_value="$(i2cget -y "${i2c_bus}" "${i2c_address_hex}" 0x10)"
		if [[ "${byte_value,,}" != "0x23" ]]; then
			echo "Unexpected byte value from i2cget: ${byte_value}" >&2
			exit 1
		fi

		i2cset -y "${i2c_bus}" "${i2c_address_hex}" 0x20 0x1234 w
		word_value="$(i2cget -y "${i2c_bus}" "${i2c_address_hex}" 0x20 w)"
		if [[ "${word_value,,}" != "0x1234" ]]; then
			echo "Unexpected word value from i2cget: ${word_value}" >&2
			exit 1
		fi

		i2c_dump="$(i2cdump -y "${i2c_bus}" "${i2c_address_hex}" b)"
		printf '%s\n' "${i2c_dump}"
		if ! grep -Eq '^10: .*23' <<<"${i2c_dump}"; then
			echo "i2cdump did not show the expected 0x23 value at register 0x10" >&2
			exit 1
		fi

		python3 - "${transport_device_path}" "${i2c_address_raw}" <<'PY'
import ctypes
import fcntl
import os
import sys

path = sys.argv[1]
addr = int(sys.argv[2])

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001


class I2cMsg(ctypes.Structure):
    _fields_ = [
        ("addr", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("len", ctypes.c_uint16),
        ("buf", ctypes.c_uint64),
    ]


class I2cRdwrIoctlData(ctypes.Structure):
    _fields_ = [
        ("msgs", ctypes.c_uint64),
        ("nmsgs", ctypes.c_uint32),
    ]


fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
reg = (ctypes.c_ubyte * 1)(0x10)
data = (ctypes.c_ubyte * 1)()
msgs = (I2cMsg * 2)()
msgs[0] = I2cMsg(addr=addr, flags=0, len=1, buf=ctypes.addressof(reg))
msgs[1] = I2cMsg(addr=addr, flags=I2C_M_RD, len=1, buf=ctypes.addressof(data))
req = I2cRdwrIoctlData(msgs=ctypes.addressof(msgs), nmsgs=2)
fcntl.ioctl(fd, I2C_RDWR, req)
assert data[0] == 0x23, hex(data[0])
os.close(fd)
print("  i2c-tools and I2C_RDWR path ok")
PY
		;;
	spi)
		echo "SPI consumer smoke:"
		python3 - "${transport_device_path}" <<'PY'
import ctypes
import fcntl
import os
import sys

path = sys.argv[1]

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


SPI_IOC_WR_MODE = _IOW(SPI_IOC_MAGIC, 1, 1)
SPI_IOC_RD_MODE = _IOR(SPI_IOC_MAGIC, 1, 1)
SPI_IOC_WR_BITS_PER_WORD = _IOW(SPI_IOC_MAGIC, 3, 1)
SPI_IOC_RD_BITS_PER_WORD = _IOR(SPI_IOC_MAGIC, 3, 1)
SPI_IOC_WR_MAX_SPEED_HZ = _IOW(SPI_IOC_MAGIC, 4, 4)
SPI_IOC_RD_MAX_SPEED_HZ = _IOR(SPI_IOC_MAGIC, 4, 4)
SPI_IOC_MESSAGE_1 = _IOW(SPI_IOC_MAGIC, 0, ctypes.sizeof(SpiIocTransfer))

fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
mode = bytearray([0])
fcntl.ioctl(fd, SPI_IOC_WR_MODE, mode, True)
mode_read = bytearray([0])
fcntl.ioctl(fd, SPI_IOC_RD_MODE, mode_read, True)
assert mode_read[0] == 0

bpw = bytearray([8])
fcntl.ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, bpw, True)
bpw_read = bytearray([0])
fcntl.ioctl(fd, SPI_IOC_RD_BITS_PER_WORD, bpw_read, True)
assert bpw_read[0] == 8

speed = ctypes.c_uint32(500000)
fcntl.ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, speed, True)
speed_read = ctypes.c_uint32(0)
fcntl.ioctl(fd, SPI_IOC_RD_MAX_SPEED_HZ, speed_read, True)
assert speed_read.value == 500000

tx = (ctypes.c_ubyte * 4)(1, 2, 3, 4)
rx = (ctypes.c_ubyte * 4)()
xfer = SpiIocTransfer(
    tx_buf=ctypes.addressof(tx),
    rx_buf=ctypes.addressof(rx),
    len=4,
    speed_hz=500000,
    bits_per_word=8,
)
ret = fcntl.ioctl(fd, SPI_IOC_MESSAGE_1, xfer)
assert ret == 4
assert bytes(rx) == bytes(tx)
os.close(fd)
print("  ioctl loopback ok")
PY
		;;
	uart)
		echo "UART consumer smoke:"
		python3 - "${transport_device_path}" <<'PY'
import serial
import sys

path = sys.argv[1]
payload = b"sensorium-uart-loopback\n"

with serial.Serial(path, baudrate=115200, timeout=1) as ser:
    ser.reset_input_buffer()
    ser.write(payload)
    ser.flush()
    data = ser.read(len(payload))
    assert data == payload, (data, payload)

print("  pyserial loopback ok")
PY
		;;
	esac
elif [[ "${transport_name}" == "i2c" || "${transport_name}" == "spi" || "${transport_name}" == "uart" ]]; then
	echo "Expected a transport alias name for ${transport_name}, but none is configured." >&2
	exit 1
fi
