#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v ansible-playbook >/dev/null 2>&1; then
	echo "Missing required command: ansible-playbook" >&2
	echo "Install Ansible locally before provisioning the droplet." >&2
	exit 1
fi

# shellcheck disable=SC1091
source "${repo_root}/scripts/remote-common.sh"

inventory="${remote_host},"

ANSIBLE_CONFIG="${repo_root}/ansible/ansible.cfg" \
REMOTE_REPO_DIR="${remote_repo_dir}" \
ansible-playbook \
	-i "${inventory}" \
	-u "${remote_user}" \
	-e "ansible_port=${remote_port}" \
	"${repo_root}/ansible/provision.yml"
