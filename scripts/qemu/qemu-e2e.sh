#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

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
	"${script_dir}/../remote/provision-droplet.sh"

echo "Ensuring media-capable remote kernel"
"${script_dir}/../remote/remote-ensure-media-kernel.sh"
qemu_assert_expected_kernel_major

echo "Checking remote libcamera version"
"${script_dir}/../remote/remote-check-libcamera-version.sh"

echo "Syncing repo to QEMU guest"
"${script_dir}/../remote/remote-sync.sh"

echo "Building helper tools in QEMU guest"
SKIP_SYNC=1 "${script_dir}/../remote/remote-build-libcamera-capture.sh"

echo "Running QEMU regression"
REMOTE_VERIFY_SYNC_REPAIR="${QEMU_VERIFY_SYNC_REPAIR:-1}" \
SKIP_SYNC=1 "${script_dir}/../remote/remote-regression.sh"

echo
echo "QEMU end-to-end flow complete."
