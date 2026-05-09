#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
iio_model="${QEMU_CI_IIO_MODEL:-models/iio/environment-plus-i2c.yaml}"
runtime_model="${QEMU_CI_RUNTIME_MODEL:-models/runtime/rpi-multibus.yaml}"
managed_runtime_model="${QEMU_CI_MANAGED_RUNTIME_MODEL:-models/runtime/rpi-managed-workers.yaml}"

# shellcheck disable=SC1091
source "${script_dir}/../lib/qemu-common.sh"

cleanup() {
	if [[ "${QEMU_KEEP_RUNNING:-0}" != "1" ]]; then
		"${script_dir}/qemu-stop.sh" >/dev/null 2>&1 || true
	fi
}

trap cleanup EXIT

QEMU_RESET_DISK="${QEMU_RESET_DISK:-1}" "${script_dir}/qemu-start.sh"
"${script_dir}/qemu-wait.sh"

qemu_export_remote_env

echo "Provisioning QEMU guest"
SENSORIUM_PROVISION_SKIP_IF_CURRENT="${QEMU_SKIP_PROVISION_IF_CURRENT:-1}" \
SENSORIUM_PROVISION_PROFILE="${QEMU_PROVISION_PROFILE:-lean}" \
SENSORIUM_LIBCAMERA_APT_RELEASE="${QEMU_CI_LIBCAMERA_APT_RELEASE:-}" \
	"${script_dir}/../remote/provision-droplet.sh"

echo "Ensuring media-capable remote kernel"
"${script_dir}/../remote/remote-ensure-media-kernel.sh"
qemu_assert_expected_kernel_major

echo "Checking remote libcamera version"
LIBCAMERA_MIN_VERSION="${QEMU_CI_LIBCAMERA_MIN_VERSION:-0.4.0}" \
	"${script_dir}/../remote/remote-check-libcamera-version.sh"

echo "Syncing repo to QEMU guest"
"${script_dir}/../remote/remote-sync.sh"

echo "Building helper tools in QEMU guest"
SKIP_SYNC=1 "${script_dir}/../remote/remote-build-libcamera-capture.sh"

echo "Running lean QEMU smoke gate"
SKIP_SYNC=1 "${script_dir}/../remote/remote-cycle.sh"
SKIP_SYNC=1 "${script_dir}/../remote/remote-assert-camera-contract.sh"
SKIP_SYNC=1 "${script_dir}/../remote/remote-smoke-iio.sh" "${iio_model}"
SKIP_SYNC=1 "${script_dir}/../remote/remote-smoke-runtime.sh" "${runtime_model}"
SKIP_SYNC=1 "${script_dir}/../remote/remote-smoke-runtime-managed.sh" "${managed_runtime_model}"

if [[ "${QEMU_CI_VERIFY_SYNC_REPAIR:-0}" == "1" ]]; then
	echo "Running sync-repair verification"
	SKIP_SYNC=1 "${script_dir}/../remote/remote-verify-sync-repair.sh" \
		"${QEMU_CI_SYNC_REPAIR_MODEL:-models/runtime/rpi-sparse-uart.yaml}"
fi

echo
echo "QEMU CI smoke complete."
