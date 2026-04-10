#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sensorium-common.sh"

sensorium_require_clean_env

module_path="${sensorium_repo_root}/kernel/sensorium.ko"
module_name="sensorium"
legacy_modules=(sony_imx_sim imx708_sim)
kernel_release="$(uname -r)"
kdir_default="/lib/modules/${kernel_release}/build"
env_file="${sensorium_repo_root}/.env.kernel"
kdir="${KDIR:-}"
module_args="${SENSORIUM_INSMOD_ARGS:-}"

run_root() {
	if [[ "${EUID}" -eq 0 ]]; then
		"$@"
	else
		sudo "$@"
	fi
}

stop_camera_users() {
	local patterns=(
		"./scripts/stream-url-to-sensorium.sh"
		"${sensorium_libcamera_bindir}/cam"
		"cam"
		"libcamera-hello"
		"v4l2-ctl -d /dev/video0"
		"v4l2-ctl -d /dev/video1"
		"ffmpeg"
	)
	local device
	local pattern

	for pattern in "${patterns[@]}"; do
		run_root pkill -f "${pattern}" 2>/dev/null || true
	done

	for device in /dev/video* /dev/v4l-subdev* /dev/media*; do
		[[ -e "${device}" ]] || continue
		run_root fuser -k "${device}" 2>/dev/null || true
	done
}

if [[ -z "${kdir}" && -f "${env_file}" ]]; then
	# shellcheck disable=SC1090
	source "${env_file}"
	kdir="${KDIR:-}"
fi

if [[ -z "${kdir}" && -d "${kdir_default}" ]]; then
	kdir="${kdir_default}"
fi

if [[ ! -d "${kdir}" ]]; then
	echo "Kernel build tree not found: ${kdir:-${kdir_default}}" >&2
	echo "Set KDIR to a prepared kernel tree before building." >&2
	echo "On WSL you can prepare one with:" >&2
	echo "  ./scripts/prepare-wsl-kernel-tree.sh" >&2
	exit 1
fi

if [[ ! -f "${kdir}/Module.symvers" ]]; then
	echo "Missing ${kdir}/Module.symvers" >&2
	echo "The WSL kernel tree has headers, but not the modversion map yet." >&2
	echo "Run ./scripts/prepare-wsl-kernel-tree.sh again and let it finish the module pass." >&2
	exit 1
fi

echo "Building ${module_name} against KDIR=${kdir}"
make -C "${sensorium_repo_root}" module KDIR="${kdir}"

if [[ ! -f "${module_path}" ]]; then
	echo "Expected module was not produced: ${module_path}" >&2
	exit 1
fi

echo "Stopping userspace processes that may still hold the module"
stop_camera_users

depends="$(modinfo -F depends "${module_path}" || true)"
if [[ -n "${depends}" ]]; then
	IFS=',' read -r -a dep_array <<<"${depends}"
	echo "Loading dependencies: ${depends}"
	for dep in "${dep_array[@]}"; do
		[[ -n "${dep}" ]] || continue
		run_root modprobe "${dep}"
	done
fi

if lsmod | awk '{print $1}' | grep -Fxq "${module_name}"; then
	echo "Unloading previous ${module_name} module"
	run_root rmmod "${module_name}"
fi

for legacy_module in "${legacy_modules[@]}"; do
	if lsmod | awk '{print $1}' | grep -Fxq "${legacy_module}"; then
		echo "Unloading legacy ${legacy_module} module"
		run_root rmmod "${legacy_module}"
	fi
done

echo "Loading ${module_path}"
if [[ -n "${sensorium_family}" ]]; then
	module_args="${module_args:+${module_args} }family=${sensorium_family}"
fi
if [[ -n "${sensorium_sensor}" ]]; then
	module_args="${module_args:+${module_args} }sensor=${sensorium_sensor}"
fi

if [[ -n "${module_args}" ]]; then
	# shellcheck disable=SC2206
	module_args_array=( ${module_args} )
	run_root insmod "${module_path}" "${module_args_array[@]}"
else
	run_root insmod "${module_path}"
fi

if command -v udevadm >/dev/null 2>&1; then
	udevadm settle || true
fi

echo
echo "Module reload complete."
echo "Loaded modules:"
lsmod | awk 'NR == 1 || $1 == "sensorium"'

echo
echo "Recent kernel log lines:"
dmesg | tail -n 20
