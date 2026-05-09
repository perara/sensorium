#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
reboot_timeout_seconds="${REMOTE_REBOOT_TIMEOUT_SECONDS:-300}"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

current_or_target_kernel="$(
remote_ssh_retry bash -s <<'EOF'
set -euo pipefail

current_kernel="$(uname -r)"
current_config="/boot/config-${current_kernel}"

if [[ -f "${current_config}" ]] &&
   grep -q '^CONFIG_MEDIA_SUPPORT=' "${current_config}" &&
   ! grep -q '^# CONFIG_MEDIA_SUPPORT is not set' "${current_config}" &&
   grep -q '^CONFIG_MEDIA_CONTROLLER=y' "${current_config}"; then
	echo "__current__:${current_kernel}"
	exit 0
fi

candidate_kernel=""
for config_path in /boot/config-*-amd64; do
	[[ -e "${config_path}" ]] || continue
	[[ "${config_path}" == *-cloud-amd64 ]] && continue
	if ! grep -q '^CONFIG_MEDIA_SUPPORT=' "${config_path}"; then
		continue
	fi
	if grep -q '^# CONFIG_MEDIA_SUPPORT is not set' "${config_path}"; then
		continue
	fi
	if ! grep -q '^CONFIG_MEDIA_CONTROLLER=y' "${config_path}"; then
		continue
	fi
	candidate_kernel="${config_path#/boot/config-}"
done

if [[ -z "${candidate_kernel}" ]]; then
	echo "__install__"
	exit 0
fi

echo "${candidate_kernel}"
EOF
)"

if [[ "${current_or_target_kernel}" == __current__:* ]]; then
	current_kernel="${current_or_target_kernel#__current__:}"
	echo "Remote host already uses a media-capable kernel: ${current_kernel}"
	exit 0
fi

if [[ "${current_or_target_kernel}" == "__install__" ]]; then
	echo "Installing media-capable Debian kernel packages..."
	remote_ssh_retry bash -s <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export APT_LISTCHANGES_FRONTEND=none
sudo apt-get update
sudo apt-get install -y -o APT::Install-Recommends=no linux-headers-amd64 linux-image-amd64
EOF

	current_or_target_kernel="$(
	remote_ssh_retry bash -s <<'EOF'
set -euo pipefail
candidate_kernel=""
for config_path in /boot/config-*-amd64; do
	[[ -e "${config_path}" ]] || continue
	[[ "${config_path}" == *-cloud-amd64 ]] && continue
	if ! grep -q '^CONFIG_MEDIA_SUPPORT=' "${config_path}"; then
		continue
	fi
	if grep -q '^# CONFIG_MEDIA_SUPPORT is not set' "${config_path}"; then
		continue
	fi
	if ! grep -q '^CONFIG_MEDIA_CONTROLLER=y' "${config_path}"; then
		continue
	fi
	candidate_kernel="${config_path#/boot/config-}"
done

if [[ -z "${candidate_kernel}" ]]; then
	echo "Could not find an installed media-capable kernel after installation." >&2
	exit 1
fi

echo "${candidate_kernel}"
EOF
	)"
fi

target_kernel="${current_or_target_kernel}"
echo "Switching remote host to media-capable kernel: ${target_kernel}"
printf -v target_kernel_q "%q" "${target_kernel}"

set +e
remote_ssh "TARGET_KERNEL=${target_kernel_q} bash -s" <<'EOF'
set -euo pipefail

target_kernel="${TARGET_KERNEL:?}"
grub_default="GRUB_DEFAULT='Advanced options for Debian GNU/Linux>Debian GNU/Linux, with Linux ${target_kernel}'"

if grep -q '^GRUB_DEFAULT=' /etc/default/grub; then
	sudo sed -i "s|^GRUB_DEFAULT=.*|${grub_default}|" /etc/default/grub
else
	printf '%s\n' "${grub_default}" | sudo tee -a /etc/default/grub >/dev/null
fi

sudo update-grub >/dev/null
sudo reboot
EOF
reboot_rc=$?
set -e

if [[ "${reboot_rc}" -ne 0 ]]; then
	echo "Continuing after reboot command returned ${reboot_rc}; SSH disconnect is expected." >&2
fi

echo "Waiting for remote host to reboot..."
deadline=$((SECONDS + reboot_timeout_seconds))

while (( SECONDS < deadline )); do
	if remote_ssh "true" >/dev/null 2>&1; then
		remote_ssh_no_stdin "sudo cloud-init status --wait >/dev/null 2>&1 || true" >/dev/null 2>&1 || true
		break
	fi
	sleep 3
done

if (( SECONDS >= deadline )); then
	echo "Timed out waiting for the remote host to come back after reboot." >&2
	exit 1
fi

remote_ssh_retry "TARGET_KERNEL=${target_kernel_q} bash -s" <<'EOF'
set -euo pipefail

target_kernel="${TARGET_KERNEL:?}"
current_kernel="$(uname -r)"
current_config="/boot/config-${current_kernel}"

if [[ "${current_kernel}" != "${target_kernel}" ]]; then
	echo "Remote host rebooted into ${current_kernel}, expected ${target_kernel}." >&2
	exit 1
fi

if [[ ! -f "${current_config}" ]] ||
   ! grep -q '^CONFIG_MEDIA_SUPPORT=' "${current_config}" ||
   grep -q '^# CONFIG_MEDIA_SUPPORT is not set' "${current_config}" ||
   ! grep -q '^CONFIG_MEDIA_CONTROLLER=y' "${current_config}"; then
	echo "Remote kernel ${current_kernel} still lacks media-controller support." >&2
	exit 1
fi
EOF

echo "Remote media-capable kernel ready: ${target_kernel}"
