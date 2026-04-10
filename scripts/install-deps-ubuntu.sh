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

base_packages=(
	build-essential
	bc
	bison
	ccache
	cpio
	dwarves
	ffmpeg
	flex
	git
	kmod
	libcamera-dev
	libcamera-ipa
	libcamera-tools
	libcamera-v4l2
	libelf-dev
	libssl-dev
	make
	meson
	ninja-build
	pkg-config
	python3
	python3-pip
	v4l-utils
)

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
for tool in cam media-ctl v4l2-ctl meson ninja pkg-config; do
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
