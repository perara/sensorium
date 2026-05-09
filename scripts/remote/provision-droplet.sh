#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

if ! command -v ansible-playbook >/dev/null 2>&1; then
	echo "Missing required command: ansible-playbook" >&2
	echo "Install Ansible locally before provisioning the droplet." >&2
	exit 1
fi

# shellcheck disable=SC1091
source "${repo_root}/scripts/lib/remote-common.sh"

inventory="${remote_host},"
ansible_args=()
provision_stamp_path="${SENSORIUM_PROVISION_STAMP_PATH:-/var/tmp/sensorium-provision-stamp}"
provision_skip_if_current="${SENSORIUM_PROVISION_SKIP_IF_CURRENT:-0}"
provision_attempts="${SENSORIUM_PROVISION_ATTEMPTS:-2}"

compute_provision_fingerprint() {
	local files=(
		"${repo_root}/ansible/ansible.cfg"
		"${repo_root}/ansible/inventory.ini.example"
		"${repo_root}/ansible/provision.yml"
		"${repo_root}/scripts/remote/provision-droplet.sh"
	)
	local ansible_version

	ansible_version="$(ansible-playbook --version | head -n 1)"
	{
		printf 'ansible_version=%s\n' "${ansible_version}"
		printf 'remote_repo_dir=%s\n' "${remote_repo_dir}"
		printf 'remote_user=%s\n' "${remote_user}"
		printf 'provision_profile=%s\n' "${SENSORIUM_PROVISION_PROFILE:-full}"
		printf 'libcamera_apt_release=%s\n' "${SENSORIUM_LIBCAMERA_APT_RELEASE:-}"
		printf 'provision_stamp_path=%s\n' "${provision_stamp_path}"
		sha256sum "${files[@]}"
	} | sha256sum | awk '{print $1}'
}

if [[ -n "${ANSIBLE_PRIVATE_KEY_FILE:-}" ]]; then
	ansible_args+=( --private-key "${ANSIBLE_PRIVATE_KEY_FILE}" )
fi

provision_fingerprint="$(compute_provision_fingerprint)"

if [[ "${provision_skip_if_current}" == "1" ]]; then
	remote_fingerprint="$(
		remote_ssh_retry "cat '${provision_stamp_path}' 2>/dev/null || true" | tr -d '\r'
	)"
	if [[ -n "${remote_fingerprint}" && "${remote_fingerprint}" == "${provision_fingerprint}" ]]; then
		echo "Provisioning fingerprint unchanged; skipping remote provisioning."
		exit 0
	fi
fi

provision_with_retry() {
	local attempt=1
	local rc

	while true; do
		if ANSIBLE_CONFIG="${repo_root}/ansible/ansible.cfg" \
			REMOTE_REPO_DIR="${remote_repo_dir}" \
			ansible-playbook \
				-i "${inventory}" \
				-u "${remote_user}" \
				-b \
				"${ansible_args[@]}" \
				-e "ansible_port=${remote_port}" \
				"${repo_root}/ansible/provision.yml"; then
			return 0
		fi
		rc=$?

		if (( attempt >= provision_attempts )); then
			return "${rc}"
		fi

		if remote_ssh "true" >/dev/null 2>&1; then
			echo "Provisioning failed while the guest remained reachable; not retrying blindly." >&2
			return "${rc}"
		fi

		echo "Provisioning run interrupted; waiting for guest SSH and retrying (${attempt}/${provision_attempts})..." >&2
		sleep "${remote_retry_delay_seconds}"
		if ! remote_ssh_retry "true" >/dev/null; then
			return "${rc}"
		fi
		attempt=$((attempt + 1))
	done
}

provision_with_retry

remote_ssh_retry "mkdir -p '$(dirname "${provision_stamp_path}")' && printf '%s\n' '${provision_fingerprint}' > '${provision_stamp_path}'"
