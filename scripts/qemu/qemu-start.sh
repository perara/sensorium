#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/qemu-common.sh"

qemu_prepare_assets

if qemu_is_running; then
	echo "QEMU guest appears to be running; probing SSH reachability"
	if qemu_wait_for_ssh $((SECONDS + qemu_reuse_probe_seconds)); then
		echo "QEMU guest is already running and reachable."
		exit 0
	fi
	echo "Existing QEMU guest is not reachable; restarting it cleanly"
	qemu_stop_running_vm
fi

if [[ -z "${QEMU_SSH_PORT:-}" ]] && ! qemu_port_is_available "${qemu_ssh_port}"; then
	qemu_ssh_port="$(qemu_find_free_port)"
fi

qemu_prepare_dir
printf '%s\n' "${qemu_ssh_port}" >"${qemu_port_file}"

if [[ "${QEMU_RESET_DISK:-0}" == "1" ]]; then
	echo "Resetting QEMU overlay disk"
	qemu_reset_overlay
	qemu_write_cloud_init
fi

if [[ -r /dev/kvm && -w /dev/kvm ]]; then
	qemu_accel="kvm"
	qemu_cpu="host"
else
	qemu_accel="tcg"
	qemu_cpu="max"
fi

rm -f "${qemu_pid_file}" "${qemu_serial_log}" "${qemu_qemu_log}"

echo "Starting QEMU guest ${qemu_vm_name}"
qemu-system-x86_64 \
	-name "${qemu_vm_name}" \
	-machine type=q35,accel="${qemu_accel}" \
	-cpu "${qemu_cpu}" \
	-smp "${qemu_cpus}" \
	-m "${qemu_memory_mb}" \
	-display none \
	-daemonize \
	-pidfile "${qemu_pid_file}" \
	-D "${qemu_qemu_log}" \
	-serial "file:${qemu_serial_log}" \
	-device virtio-rng-pci \
	-drive if=virtio,format=qcow2,file="${qemu_overlay_image}" \
	-drive if=virtio,format=raw,readonly=on,file="${qemu_seed_iso}" \
	-netdev "user,id=net0,hostfwd=tcp:${qemu_guest_host}:${qemu_ssh_port}-:22" \
	-device virtio-net-pci,netdev=net0

if ! qemu_wait_for_hostfwd $((SECONDS + 15)); then
	echo "QEMU guest started but SSH forward ${qemu_guest_host}:${qemu_ssh_port} did not become reachable." >&2
	echo "QEMU log:"
	tail -n 80 "${qemu_qemu_log}" 2>/dev/null || true
	qemu_stop_running_vm
	exit 1
fi

echo "QEMU guest started."
echo "  host: ${qemu_guest_host}"
echo "  port: ${qemu_ssh_port}"
echo "  user: ${qemu_guest_user}"
echo "  pid:  $(<"${qemu_pid_file}")"
