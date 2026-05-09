#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
	echo "Re-running with sudo..."
	exec sudo --preserve-env=DEBIAN_FRONTEND bash "$0" "$@"
fi

if [[ ! -r /etc/os-release ]]; then
	echo "Could not detect the host OS." >&2
	exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release

if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *"debian"* ]]; then
	echo "This installer currently targets Ubuntu/Debian hosts." >&2
	exit 1
fi

kernel_release="$(uname -r)"
kernel_headers_pkg="linux-headers-${kernel_release}"
host_profile="${SENSORIUM_HOST_PROFILE:-driver}"
install_ops="${SENSORIUM_INSTALL_OPS:-0}"

while [[ $# -gt 0 ]]; do
	case "$1" in
	--profile)
		if [[ $# -lt 2 ]]; then
			echo "--profile requires driver, runtime, or full" >&2
			exit 1
		fi
		host_profile="$2"
		shift 2
		;;
	--ops)
		install_ops=1
		shift
		;;
	-h|--help)
		cat <<'EOF'
Usage: install-deps-ubuntu.sh [--profile driver|runtime|full] [--ops]

Profiles:
  driver   Kernel build/load dependencies only
  runtime  driver + sensoriumd/sensoriumctl runtime dependencies
  full     runtime + local camera/IIO/runtime validation tooling

Options:
  --ops    Include remote/QEMU/provisioning tools
EOF
		exit 0
		;;
	*)
		echo "Unknown argument: $1" >&2
		exit 1
		;;
	esac
done

driver_packages=(
	bc
	gcc
	g++
	kmod
	libc6-dev
	libelf-dev
	libssl-dev
	make
	pkg-config
)

runtime_packages=(
	python3
	python3-serial
	python3-yaml
)

camera_packages=(
	curl
	ffmpeg
	i2c-tools
	libcamera-dev
	libcamera-ipa
	libcamera-tools
	libcamera-v4l2
	rsync
	v4l-utils
)

dev_packages=(
	bison
	build-essential
	ccache
	cpio
	dwarves
	flex
	git
	meson
	ninja-build
	python3-pip
)

ops_packages=(
	ansible
	cloud-image-utils
	openssh-client
	qemu-system-x86
	qemu-utils
)

case "${host_profile}" in
driver|runtime|full)
	;;
*)
	echo "Unsupported SENSORIUM_HOST_PROFILE: ${host_profile}" >&2
	echo "Supported values: driver, runtime, full" >&2
	exit 1
	;;
esac

selected_packages=( "${driver_packages[@]}" )
case "${host_profile}" in
runtime)
	selected_packages+=( "${runtime_packages[@]}" )
	;;
full)
	selected_packages+=( "${runtime_packages[@]}" )
	selected_packages+=( "${camera_packages[@]}" )
	selected_packages+=( "${dev_packages[@]}" )
	;;
esac

if [[ "${install_ops}" == "1" ]]; then
	selected_packages+=( "${ops_packages[@]}" )
fi

declare -A package_seen=()
base_packages=()
for pkg in "${selected_packages[@]}"; do
	if [[ -z "${package_seen[${pkg}]:-}" ]]; then
		base_packages+=( "${pkg}" )
		package_seen["${pkg}"]=1
	fi
done

echo "Selected host profile: ${host_profile}"
if [[ "${install_ops}" == "1" ]]; then
	echo "Including remote/QEMU ops tools"
fi

echo "Updating apt metadata..."
apt-get update

echo "Installing base dependencies..."
apt-get install -y "${base_packages[@]}"

if apt-cache show "${kernel_headers_pkg}" >/dev/null 2>&1; then
	echo "Installing matching kernel headers: ${kernel_headers_pkg}"
	apt-get install -y "${kernel_headers_pkg}"
else
	echo
	echo "WARNING: ${kernel_headers_pkg} is not available from apt on this host."
	echo "This is common on WSL kernels."
	echo "You can still use this repo, but module builds will require either:"
	echo "  1. a manually prepared matching kernel build tree, or"
	echo "  2. KDIR pointing at that external kernel tree."
fi

echo
echo "Dependency install complete."
echo "Useful tool paths:"
for tool in ansible-playbook cam cloud-localds media-ctl qemu-system-x86_64 rsync ssh v4l2-ctl meson ninja pkg-config; do
	if command -v "${tool}" >/dev/null 2>&1; then
		printf '  %-10s %s\n' "${tool}" "$(command -v "${tool}")"
	fi
done

if [[ -e "/lib/modules/${kernel_release}/build" ]]; then
	echo
	echo "Kernel build tree detected at /lib/modules/${kernel_release}/build"
else
	echo
	echo "No kernel build tree found at /lib/modules/${kernel_release}/build"
	echo "Set KDIR to a prepared kernel tree before running the reload script."
fi
