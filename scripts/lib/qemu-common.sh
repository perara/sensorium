#!/usr/bin/env bash
set -euo pipefail

sensorium_script_path="$(readlink -f "${BASH_SOURCE[0]}")"
sensorium_repo_root="$(cd "$(dirname "${sensorium_script_path}")/../.." && pwd)"

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	echo "qemu-common.sh is an internal library; source it from QEMU workflow scripts." >&2
	exit 2
fi

qemu_cache_root="${QEMU_CACHE_ROOT:-${sensorium_repo_root}/.cache/qemu}"
qemu_distro="${QEMU_DISTRO:-debian-trixie}"
qemu_vm_name="${QEMU_VM_NAME:-sensorium-e2e}"
qemu_guest_host="${QEMU_GUEST_HOST:-127.0.0.1}"
qemu_memory_mb="${QEMU_MEMORY_MB:-4096}"
qemu_cpus="${QEMU_CPUS:-4}"
qemu_disk_gb="${QEMU_DISK_GB:-24}"
if [[ -n "${QEMU_BOOT_TIMEOUT_SECONDS:-}" ]]; then
	qemu_boot_timeout_seconds="${QEMU_BOOT_TIMEOUT_SECONDS}"
elif [[ -r /dev/kvm && -w /dev/kvm ]]; then
	qemu_boot_timeout_seconds="300"
else
	qemu_boot_timeout_seconds="1200"
fi

case "${qemu_distro}" in
ubuntu-noble)
	qemu_default_guest_user="ubuntu"
	qemu_default_image_url="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
	;;
debian-bookworm)
	qemu_default_guest_user="debian"
	qemu_default_image_url="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2"
	;;
debian-trixie)
	qemu_default_guest_user="debian"
	qemu_default_image_url="https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
	;;
debian-sid)
	qemu_default_guest_user="debian"
	qemu_default_image_url="https://cloud.debian.org/images/cloud/sid/daily/latest/debian-sid-genericcloud-amd64-daily.qcow2"
	;;
custom)
	qemu_default_guest_user="ubuntu"
	qemu_default_image_url=""
	;;
*)
	echo "Unsupported QEMU_DISTRO: ${qemu_distro}" >&2
	echo "Supported values: ubuntu-noble, debian-bookworm, debian-trixie, debian-sid, custom" >&2
	exit 2
	;;
esac

qemu_guest_user="${QEMU_GUEST_USER:-${qemu_default_guest_user}}"
qemu_remote_repo_dir="${QEMU_REMOTE_REPO_DIR:-/home/${qemu_guest_user}/sensorium}"
qemu_image_url="${QEMU_IMAGE_URL:-${qemu_default_image_url}}"

if [[ -z "${qemu_image_url}" ]]; then
	echo "QEMU_IMAGE_URL must be set when QEMU_DISTRO=custom." >&2
	exit 2
fi

qemu_image_basename="$(basename "${qemu_image_url%%\?*}")"
if [[ -z "${qemu_image_basename}" || "${qemu_image_basename}" == "/" || "${qemu_image_basename}" == "." ]]; then
	qemu_image_basename="base.img"
fi

qemu_base_image="${qemu_cache_root}/${qemu_distro}-${qemu_image_basename}"
qemu_overlay_image="${qemu_cache_root}/${qemu_vm_name}.qcow2"
qemu_seed_iso="${qemu_cache_root}/${qemu_vm_name}-seed.iso"
qemu_user_data="${qemu_cache_root}/${qemu_vm_name}-user-data"
qemu_meta_data="${qemu_cache_root}/${qemu_vm_name}-meta-data"
qemu_ssh_key="${qemu_cache_root}/${qemu_vm_name}-id_ed25519"
qemu_ssh_pubkey="${qemu_ssh_key}.pub"
qemu_pid_file="${qemu_cache_root}/${qemu_vm_name}.pid"
qemu_port_file="${qemu_cache_root}/${qemu_vm_name}.port"
qemu_serial_log="${qemu_cache_root}/${qemu_vm_name}-serial.log"
qemu_qemu_log="${qemu_cache_root}/${qemu_vm_name}-qemu.log"
qemu_reuse_probe_seconds="${QEMU_REUSE_PROBE_SECONDS:-15}"

if [[ -n "${QEMU_SSH_PORT:-}" ]]; then
	qemu_ssh_port="${QEMU_SSH_PORT}"
elif [[ -f "${qemu_port_file}" ]]; then
	qemu_ssh_port="$(<"${qemu_port_file}")"
else
	qemu_ssh_port="2222"
fi

qemu_refresh_port() {
	if [[ -n "${QEMU_SSH_PORT:-}" ]]; then
		qemu_ssh_port="${QEMU_SSH_PORT}"
	elif [[ -f "${qemu_port_file}" ]]; then
		qemu_ssh_port="$(<"${qemu_port_file}")"
	fi
}

qemu_require_cmd() {
	local cmd="$1"
	if ! command -v "${cmd}" >/dev/null 2>&1; then
		echo "Missing required command: ${cmd}" >&2
		exit 1
	fi
}

qemu_prepare_dir() {
	mkdir -p "${qemu_cache_root}"
}

qemu_port_is_available() {
	local port="$1"

	python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
s = socket.socket()
try:
    s.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

qemu_find_free_port() {
	python3 <<'PY'
import socket

for port in range(2222, 2300):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        continue
    else:
        print(port)
        break
    finally:
        s.close()
PY
}

qemu_host_port_ready() {
	local port="${1:-${qemu_ssh_port}}"

	python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
s = socket.socket()
s.settimeout(1.0)
try:
    s.connect(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

qemu_is_running() {
	local pid

	if [[ -f "${qemu_pid_file}" ]]; then
		pid="$(<"${qemu_pid_file}")"
		if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
			if qemu_pid_matches_vm "${pid}"; then
				qemu_sync_runtime_files_from_pid "${pid}"
				return 0
			fi
			rm -f "${qemu_pid_file}"
		fi
	fi

	pid="$(qemu_find_vm_pid || true)"
	if [[ -n "${pid}" ]]; then
		printf '%s\n' "${pid}" >"${qemu_pid_file}"
		qemu_sync_runtime_files_from_pid "${pid}"
		return 0
	fi

	return 1
}

qemu_find_vm_pid() {
	local pid

	for pid in $(pgrep -f qemu-system-x86_64 || true); do
		if qemu_pid_matches_vm "${pid}"; then
			printf '%s\n' "${pid}"
			return 0
		fi
	done

	return 1
}

qemu_pid_matches_vm() {
	local pid="$1"
	local cmdline_file="/proc/${pid}/cmdline"
	local cmdline

	[[ -r "${cmdline_file}" ]] || return 1
	cmdline="$(tr '\0' ' ' <"${cmdline_file}" 2>/dev/null || true)"
	[[ -n "${cmdline}" ]] || return 1
	[[ "${cmdline}" == *"qemu-system-x86_64"* ]] || return 1
	[[ "${cmdline}" == *"-name ${qemu_vm_name}"* ]] || [[ "${cmdline}" == *"-name ${qemu_vm_name},"* ]] || return 1
	return 0
}

qemu_sync_runtime_files_from_pid() {
	local pid="$1"
	local cmdline_file="/proc/${pid}/cmdline"
	local cmdline

	[[ -r "${cmdline_file}" ]] || return 0
	cmdline="$(tr '\0' ' ' <"${cmdline_file}" 2>/dev/null || true)"
	[[ -n "${cmdline}" ]] || return 0

	printf '%s\n' "${pid}" >"${qemu_pid_file}"
	if [[ "${cmdline}" =~ hostfwd=tcp:[^:]+:([0-9]+)-:22 ]]; then
		qemu_ssh_port="${BASH_REMATCH[1]}"
		printf '%s\n' "${qemu_ssh_port}" >"${qemu_port_file}"
	fi
}

qemu_ssh_ready() {
	qemu_refresh_port
	ssh \
		-p "${qemu_ssh_port}" \
		-o StrictHostKeyChecking=accept-new \
		-o UserKnownHostsFile=/dev/null \
		-o LogLevel=ERROR \
		-o ConnectTimeout=5 \
		-i "${qemu_ssh_key}" \
		"${qemu_guest_user}@${qemu_guest_host}" true >/dev/null 2>&1
}

qemu_wait_for_ssh() {
	local deadline="${1:-$((SECONDS + qemu_boot_timeout_seconds))}"

	while (( SECONDS < deadline )); do
		if ! qemu_is_running; then
			return 1
		fi
		if qemu_ssh_ready; then
			return 0
		fi
		sleep 2
	done

	return 1
}

qemu_wait_for_stable_ssh() {
	local deadline="${1:-$((SECONDS + qemu_boot_timeout_seconds))}"
	local consecutive="${2:-3}"
	local successes=0

	while (( SECONDS < deadline )); do
		if ! qemu_is_running; then
			return 1
		fi
		if qemu_ssh_ready; then
			((successes += 1))
			if (( successes >= consecutive )); then
				return 0
			fi
		else
			successes=0
		fi
		sleep 2
	done

	return 1
}

qemu_wait_for_hostfwd() {
	local deadline="${1:-$((SECONDS + 15))}"

	while (( SECONDS < deadline )); do
		if ! qemu_is_running; then
			return 1
		fi
		if qemu_host_port_ready "${qemu_ssh_port}"; then
			return 0
		fi
		sleep 1
	done

	return 1
}

qemu_stop_running_vm() {
	local pid
	local wait_seconds="${1:-30}"
	local second

	if ! qemu_is_running; then
		rm -f "${qemu_pid_file}"
		return 0
	fi

	pid="$(<"${qemu_pid_file}")"
	if ! qemu_pid_matches_vm "${pid}"; then
		rm -f "${qemu_pid_file}"
		return 0
	fi
	kill "${pid}"

	for (( second = 0; second < wait_seconds; second++ )); do
		if ! kill -0 "${pid}" 2>/dev/null; then
			rm -f "${qemu_pid_file}"
			return 0
		fi
		sleep 1
	done

	kill -9 "${pid}" 2>/dev/null || true
	rm -f "${qemu_pid_file}"
	return 0
}

qemu_require_tools() {
	qemu_require_cmd curl
	qemu_require_cmd cloud-localds
	qemu_require_cmd qemu-img
	qemu_require_cmd qemu-system-x86_64
	qemu_require_cmd ssh
	qemu_require_cmd ssh-keygen
}

qemu_download_base_image() {
	if [[ -f "${qemu_base_image}" ]]; then
		return 0
	fi

	echo "Downloading base cloud image"
	curl -fL "${qemu_image_url}" -o "${qemu_base_image}"
}

qemu_generate_ssh_key() {
	if [[ -f "${qemu_ssh_key}" && -f "${qemu_ssh_pubkey}" ]]; then
		return 0
	fi

	ssh-keygen -q -t ed25519 -N "" -f "${qemu_ssh_key}"
}

qemu_write_cloud_init() {
	local pubkey

	pubkey="$(<"${qemu_ssh_pubkey}")"

	cat >"${qemu_user_data}" <<EOF
#cloud-config
users:
  - default
ssh_authorized_keys:
  - ${pubkey}
write_files:
  - path: /etc/sudoers.d/90-sensorium-nopasswd
    owner: root:root
    permissions: '0440'
    content: |
      ${qemu_guest_user} ALL=(ALL) NOPASSWD:ALL
runcmd:
  - mkdir -p '${qemu_remote_repo_dir}'
  - usermod -aG video,plugdev ${qemu_guest_user}
  - chown -R ${qemu_guest_user}:${qemu_guest_user} '/home/${qemu_guest_user}'
EOF

	cat >"${qemu_meta_data}" <<EOF
instance-id: ${qemu_vm_name}
local-hostname: ${qemu_vm_name}
EOF

	cloud-localds "${qemu_seed_iso}" "${qemu_user_data}" "${qemu_meta_data}"
}

qemu_create_overlay() {
	rm -f "${qemu_overlay_image}"
	qemu-img create -f qcow2 -F qcow2 -b "${qemu_base_image}" \
		"${qemu_overlay_image}" "${qemu_disk_gb}G" >/dev/null
}

qemu_prepare_assets() {
	qemu_prepare_dir
	qemu_require_tools
	qemu_download_base_image
	qemu_generate_ssh_key
	qemu_write_cloud_init

	if [[ ! -f "${qemu_overlay_image}" ]]; then
		qemu_create_overlay
	fi
}

qemu_reset_overlay() {
	qemu_prepare_dir
	qemu_download_base_image
	qemu_create_overlay
}

qemu_export_remote_env() {
	qemu_refresh_port
	export REMOTE_HOST="${qemu_guest_host}"
	export REMOTE_PORT="${qemu_ssh_port}"
	export REMOTE_USER="${qemu_guest_user}"
	export REMOTE_REPO_DIR="${qemu_remote_repo_dir}"
	export REMOTE_SSH_OPTS="-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i ${qemu_ssh_key}"
	export ANSIBLE_PRIVATE_KEY_FILE="${qemu_ssh_key}"
	export ANSIBLE_HOST_KEY_CHECKING=False
	export SENSORIUM_REMOTE_ENV_FILE="/dev/null"
	if [[ -r /dev/kvm && -w /dev/kvm ]]; then
		export REMOTE_RETRY_COUNT="${QEMU_REMOTE_RETRY_COUNT:-8}"
		export REMOTE_RETRY_DELAY_SECONDS="${QEMU_REMOTE_RETRY_DELAY_SECONDS:-5}"
		export REMOTE_REBOOT_TIMEOUT_SECONDS="${QEMU_REMOTE_REBOOT_TIMEOUT_SECONDS:-300}"
	else
		export REMOTE_RETRY_COUNT="${QEMU_REMOTE_RETRY_COUNT:-40}"
		export REMOTE_RETRY_DELAY_SECONDS="${QEMU_REMOTE_RETRY_DELAY_SECONDS:-5}"
		export REMOTE_REBOOT_TIMEOUT_SECONDS="${QEMU_REMOTE_REBOOT_TIMEOUT_SECONDS:-1200}"
	fi

	if [[ "${qemu_distro}" == debian-* ]]; then
		export SENSORIUM_LIBCAMERA_APT_RELEASE="${QEMU_LIBCAMERA_APT_RELEASE:-sid}"
	else
		unset SENSORIUM_LIBCAMERA_APT_RELEASE || true
	fi
}

qemu_assert_expected_kernel_major() {
	if [[ -z "${QEMU_EXPECT_KERNEL_MAJOR:-}" ]]; then
		return 0
	fi

	echo "Checking remote kernel major"
	"${sensorium_repo_root}/scripts/remote/remote-assert-kernel-major.sh" \
		"${QEMU_EXPECT_KERNEL_MAJOR}"
}

qemu_ssh() {
	qemu_refresh_port
	ssh \
		-p "${qemu_ssh_port}" \
		-o StrictHostKeyChecking=accept-new \
		-o UserKnownHostsFile=/dev/null \
		-o LogLevel=ERROR \
		-o ConnectTimeout=5 \
		-i "${qemu_ssh_key}" \
		"${qemu_guest_user}@${qemu_guest_host}" "$@"
}

qemu_ssh_no_stdin() {
	qemu_refresh_port
	ssh -n \
		-p "${qemu_ssh_port}" \
		-o StrictHostKeyChecking=accept-new \
		-o UserKnownHostsFile=/dev/null \
		-o LogLevel=ERROR \
		-o ConnectTimeout=5 \
		-i "${qemu_ssh_key}" \
		"${qemu_guest_user}@${qemu_guest_host}" "$@"
}
