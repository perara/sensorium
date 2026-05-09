#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/qemu-common.sh"

if ! qemu_is_running; then
	echo "QEMU guest is not running."
	rm -f "${qemu_pid_file}"
	exit 0
fi

pid="$(<"${qemu_pid_file}")"
echo "Stopping QEMU guest ${qemu_vm_name} (pid ${pid})"
qemu_stop_running_vm
echo "QEMU guest stopped."
