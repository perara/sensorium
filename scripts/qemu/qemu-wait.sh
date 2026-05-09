#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/qemu-common.sh"

deadline=$((SECONDS + qemu_boot_timeout_seconds))

if qemu_wait_for_ssh "${deadline}"; then
	echo "SSH is up; waiting for cloud-init"
	qemu_ssh_no_stdin "sudo cloud-init status --wait >/dev/null 2>&1 || true"
	echo "Verifying stable SSH after cloud-init"
	if ! qemu_wait_for_stable_ssh "${deadline}"; then
		echo "QEMU guest lost SSH stability after cloud-init on ${qemu_guest_host}:${qemu_ssh_port}" >&2
		echo "Serial log:"
		tail -n 80 "${qemu_serial_log}" 2>/dev/null || true
		exit 1
	fi
	echo "QEMU guest is ready."
	exit 0
fi

echo "Timed out waiting for QEMU guest SSH on ${qemu_guest_host}:${qemu_ssh_port}" >&2
echo "Serial log:"
tail -n 80 "${qemu_serial_log}" 2>/dev/null || true
exit 1
