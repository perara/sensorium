#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib" && pwd)/sensorium-common.sh"

sensorium_require_clean_env
export PATH="/usr/sbin:/sbin:${PATH}"

module_path="${sensorium_repo_root}/kernel/sensorium.ko"
module_name="sensorium"
legacy_modules=(sony_imx_sim imx708_sim)
fallback_module_dependencies=(
	"i2c-dev"
	"videodev"
	"videobuf2-common"
	"videobuf2-v4l2"
	"videobuf2-dma-sg"
	"mc"
	"industrialio"
)
kernel_release="$(uname -r)"
kdir_default="/lib/modules/${kernel_release}/build"
env_file="${sensorium_repo_root}/.env.kernel"
kdir="${KDIR:-}"
module_args="${SENSORIUM_INSMOD_ARGS:-}"
build_state_dir="${sensorium_repo_root}/.cache/kbuild"
build_kdir_stamp="${build_state_dir}/last-kdir"
build_fingerprint_stamp="${build_state_dir}/last-source-fingerprint"
build_layout_stamp="${build_state_dir}/last-layout-fingerprint"
build_in_progress_stamp="${build_state_dir}/build-in-progress"
clean_build="${SENSORIUM_CLEAN_BUILD:-0}"
broad_kill="${SENSORIUM_RELOAD_KILL_BROAD:-0}"
force_evict="${SENSORIUM_RELOAD_FORCE_EVICT:-0}"
skip_build_if_unchanged="${SENSORIUM_SKIP_BUILD_IF_UNCHANGED:-1}"
sensoriumctl_path="${sensorium_repo_root}/scripts/runtime/sensoriumctl"

run_root() {
	if [[ "${EUID}" -eq 0 ]]; then
		"$@"
	else
		sudo "$@"
	fi
}

compute_build_fingerprint() {
	local files=(
		"${sensorium_repo_root}/Makefile"
		"${sensorium_repo_root}/kernel/Makefile"
	)
	local file

	while IFS= read -r file; do
		files+=( "${file}" )
	done < <(
		find "${sensorium_repo_root}/kernel" -maxdepth 1 \
			\( -name '*.c' -o -name '*.h' -o -name '*.inc' \) \
			-type f | sort
	)

	sha256sum "${files[@]}" | sha256sum | awk '{print $1}'
}

compute_build_layout_fingerprint() {
	local makefiles=(
		"${sensorium_repo_root}/Makefile"
		"${sensorium_repo_root}/kernel/Makefile"
	)
	local names_hash

	names_hash="$(
		find "${sensorium_repo_root}/kernel" -maxdepth 1 \
			\( -name '*.c' -o -name '*.h' -o -name '*.inc' \) \
			-type f -printf '%f\n' | sort | sha256sum | awk '{print $1}'
	)"

	{
		sha256sum "${makefiles[@]}"
		printf '%s  %s\n' "${names_hash}" "kernel-file-layout"
	} | sha256sum | awk '{print $1}'
}

stop_camera_users() {
	local scoped_patterns=(
		"./scripts/runtime/stream-url-to-sensorium.sh"
		"./scripts/runtime/sensoriumd"
		"sensoriumd --daemonize"
		"python3 ./scripts/runtime/sensoriumd"
	)
	local broad_patterns=(
		"cam"
		"i2cdetect"
		"i2cdump"
		"i2cget"
		"i2cset"
		"i2ctransfer"
		"libcamera-hello"
		"ffmpeg"
	)
	local pattern

	for pattern in "${scoped_patterns[@]}"; do
		run_root pkill -f "${pattern}" 2>/dev/null || true
	done

	if [[ "${broad_kill}" == "1" ]]; then
		for pattern in "${broad_patterns[@]}"; do
			run_root pkill -f "${pattern}" 2>/dev/null || true
		done
	fi

	stop_scoped_url_streams
}

stop_scoped_url_streams() {
	local pid_file
	local stream_pid
	local stream_pgid
	local pid
	local pgid
	local cmdline

	shopt -s nullglob
	for pid_file in "${sensorium_repo_root}"/.cache/url-stream-*.pid; do
		[[ -f "${pid_file}" ]] || continue
		stream_pid="$(<"${pid_file}" 2>/dev/null || true)"
		if [[ ! "${stream_pid}" =~ ^[0-9]+$ ]]; then
			rm -f "${pid_file}"
			continue
		fi
		stream_pgid="$(run_root ps -o pgid= -p "${stream_pid}" 2>/dev/null | tr -d ' ' || true)"
		if [[ -n "${stream_pgid}" ]]; then
			run_root kill -- "-${stream_pgid}" 2>/dev/null || true
		fi
		run_root kill "${stream_pid}" 2>/dev/null || true
		for _ in $(seq 1 30); do
			if ! run_root kill -0 "${stream_pid}" 2>/dev/null; then
				break
			fi
			sleep 0.1
		done
		if run_root kill -0 "${stream_pid}" 2>/dev/null; then
			if [[ -n "${stream_pgid}" ]]; then
				run_root kill -KILL -- "-${stream_pgid}" 2>/dev/null || true
			fi
			run_root kill -KILL "${stream_pid}" 2>/dev/null || true
		fi
		rm -f "${pid_file}"
	done
	shopt -u nullglob

	while IFS=$'\t' read -r pid pgid cmdline; do
		[[ "${pid}" =~ ^[0-9]+$ ]] || continue
		case "${cmdline}" in
		*"ffmpeg "*"-i ${sensorium_repo_root}/.cache/media/"* )
			;;
		*"v4l2-ctl -d /dev/video0 "*"--stream-from=-"* )
			;;
		*)
			continue
			;;
		esac
		if [[ -n "${pgid}" ]] && [[ "${pgid}" =~ ^[0-9]+$ ]]; then
			run_root kill -- "-${pgid}" 2>/dev/null || true
		fi
		run_root kill "${pid}" 2>/dev/null || true
		sleep 0.1
		if run_root kill -0 "${pid}" 2>/dev/null; then
			if [[ -n "${pgid}" ]] && [[ "${pgid}" =~ ^[0-9]+$ ]]; then
				run_root kill -KILL -- "-${pgid}" 2>/dev/null || true
			fi
			run_root kill -KILL "${pid}" 2>/dev/null || true
		fi
	done < <(
		run_root ps -eo pid=,pgid=,args= --no-headers | \
			awk '{pid=$1; pgid=$2; $1=""; $2=""; sub(/^  */, "", $0); printf "%s\t%s\t%s\n", pid, pgid, $0}'
	)
}

collect_targeted_devices() {
	local targeted_devices=()

	add_targeted_device() {
		local path="$1"
		[[ -n "${path}" ]] || return 0
		targeted_devices+=( "${path}" )
	}

	if [[ "${sensorium_adapter:-}" == "camera" ]]; then
		add_targeted_device /dev/video0
		add_targeted_device /dev/video1
		add_targeted_device /dev/v4l-subdev0
		add_targeted_device /dev/media0
	elif [[ "${sensorium_adapter:-}" == "runtime" ]]; then
		add_targeted_device /dev/sensorium-runtime-bridge
	elif [[ "${sensorium_adapter:-}" == "iio" ]]; then
		case "${sensorium_transport:-}" in
		i2c)
			if [[ -n "${sensorium_transport_device_name:-}" ]]; then
				add_targeted_device "/dev/${sensorium_transport_device_name}"
			fi
			;;
		spi)
			if [[ -n "${sensorium_transport_device_name:-}" ]]; then
				add_targeted_device "/dev/${sensorium_transport_device_name}"
			fi
			;;
		uart)
			if [[ -n "${sensorium_transport_device_name:-}" ]]; then
				add_targeted_device "/dev/${sensorium_transport_device_name}"
			fi
			;;
		esac
	fi

	if (( ${#targeted_devices[@]} == 0 )); then
		targeted_devices=(/dev/video* /dev/v4l-subdev* /dev/media* /dev/i2c-* /dev/spidev* /dev/ttyAMA* /dev/ttyS* /dev/sensorium-runtime-bridge)
	fi

	printf '%s\n' "${targeted_devices[@]}"
}

evict_device_holders() {
	local force="${1:-${force_evict}}"
	local device
	local pid
	local cmdline
	local unknown_holders=0

	sensorium_owned_pid() {
		local candidate="$1"
		local line="$2"

		[[ "${candidate}" =~ ^[0-9]+$ ]] || return 1
		[[ -n "${line}" ]] || return 1
		[[ "${line}" == *"${sensorium_repo_root}"* ]] && return 0
		[[ "${line}" == *"/tmp/sensorium-stream-"* ]] && return 0
		[[ "${line}" == *"sensoriumd"* ]] && return 0
		[[ "${line}" == *"stream-url-to-sensorium.sh"* ]] && return 0
		[[ "${line}" == *"libcamera-record"* ]] && return 0
		[[ "${line}" == *"libcamera-capture"* ]] && return 0
		[[ "${line}" == *"v4l2-ctl -d /dev/video"* ]] && return 0
		[[ "${line}" == *"python3 ./scripts/"* ]] && return 0
		return 1
	}

	while IFS= read -r device; do
		[[ -n "${device}" ]] || continue
		[[ -e "${device}" ]] || continue
		while IFS= read -r pid; do
			[[ -n "${pid}" ]] || continue
			cmdline="$(run_root ps -p "${pid}" -o args= 2>/dev/null | head -n 1 || true)"
			if sensorium_owned_pid "${pid}" "${cmdline}"; then
				run_root kill "${pid}" 2>/dev/null || true
				continue
			fi
			if [[ "${force}" == "1" ]]; then
				run_root kill "${pid}" 2>/dev/null || true
				continue
			fi
			echo "Refusing to evict non-Sensorium holder PID ${pid} on ${device}: ${cmdline}" >&2
			unknown_holders=1
		done < <(
			run_root bash -lc 'fuser -a -- "$1" 2>/dev/null || true' _ "${device}" | \
				tr ' ' '\n' | sed 's/://g' | awk '/^[0-9]+$/'
		)
	done < <(collect_targeted_devices)

	if [[ "${unknown_holders}" == "1" ]]; then
		echo "Set SENSORIUM_RELOAD_FORCE_EVICT=1 to override non-Sensorium holder protection." >&2
		return 1
	fi
}

teardown_runtime_state() {
	local current_adapter=""

	if [[ -r /sys/module/${module_name}/parameters/adapter ]]; then
		current_adapter="$(<"/sys/module/${module_name}/parameters/adapter")"
	fi

	if [[ "${current_adapter}" != "runtime" ]]; then
		return 0
	fi

	if [[ ! -x "${sensoriumctl_path}" ]]; then
		return 0
	fi

	echo "Resetting active runtime state before unloading ${module_name}"
	"${sensoriumctl_path}" runtime reset >/dev/null 2>&1 || true
	"${sensoriumctl_path}" daemon stop >/dev/null 2>&1 || true
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
	echo "  ./scripts/local/prepare-wsl-kernel-tree.sh" >&2
	exit 1
fi

if [[ ! -f "${kdir}/Module.symvers" ]]; then
	if [[ "${SENSORIUM_ALLOW_MISSING_SYMVERS:-0}" != "1" ]]; then
		echo "Missing ${kdir}/Module.symvers" >&2
		echo "The WSL kernel tree has headers, but not the modversion map yet." >&2
		echo "Run ./scripts/local/prepare-wsl-kernel-tree.sh again and let it finish the module pass." >&2
		echo "Set SENSORIUM_ALLOW_MISSING_SYMVERS=1 only if you intentionally want a warning-only local build." >&2
		exit 1
	fi
	echo "WARNING: proceeding without ${kdir}/Module.symvers because SENSORIUM_ALLOW_MISSING_SYMVERS=1" >&2
fi

echo "Building ${module_name} against KDIR=${kdir}"
mkdir -p "${build_state_dir}"
if [[ -f "${build_kdir_stamp}" ]] && [[ "$(<"${build_kdir_stamp}")" != "${kdir}" ]]; then
	clean_build="1"
fi
current_build_fingerprint="$(compute_build_fingerprint)"
last_build_fingerprint=""
current_build_layout_fingerprint="$(compute_build_layout_fingerprint)"
last_build_layout_fingerprint=""
if [[ -f "${build_fingerprint_stamp}" ]]; then
	last_build_fingerprint="$(<"${build_fingerprint_stamp}")"
fi
if [[ -f "${build_layout_stamp}" ]]; then
	last_build_layout_fingerprint="$(<"${build_layout_stamp}")"
fi
if [[ -f "${build_in_progress_stamp}" ]]; then
	echo "Previous ${module_name} build did not finish cleanly; forcing a clean rebuild" >&2
	clean_build="1"
fi
if [[ "${last_build_layout_fingerprint}" != "${current_build_layout_fingerprint}" ]]; then
	clean_build="1"
fi
build_required="1"
if [[ "${clean_build}" != "1" ]] && [[ "${skip_build_if_unchanged}" == "1" ]] && \
   [[ -f "${module_path}" ]] && [[ -f "${build_kdir_stamp}" ]] && \
   [[ "$(<"${build_kdir_stamp}")" == "${kdir}" ]] && \
   [[ "${last_build_fingerprint}" == "${current_build_fingerprint}" ]] && \
   [[ ! -f "${build_in_progress_stamp}" ]]; then
	build_required="0"
fi
if [[ "${build_required}" == "0" ]]; then
	echo "Kernel sources unchanged; reusing existing ${module_name} module"
else
	: >"${build_in_progress_stamp}"
	if [[ "${clean_build}" == "1" ]]; then
		make -C "${sensorium_repo_root}" clean KDIR="${kdir}" >/dev/null
	fi
	if ! make -C "${sensorium_repo_root}" module KDIR="${kdir}"; then
		if [[ "${clean_build}" == "1" ]]; then
			exit 1
		fi
		echo "Incremental build failed; retrying with a clean module rebuild" >&2
		make -C "${sensorium_repo_root}" clean KDIR="${kdir}" >/dev/null
		make -C "${sensorium_repo_root}" module KDIR="${kdir}"
	fi
	rm -f "${build_in_progress_stamp}"
fi
printf '%s\n' "${kdir}" >"${build_kdir_stamp}"
printf '%s\n' "${current_build_fingerprint}" >"${build_fingerprint_stamp}"
printf '%s\n' "${current_build_layout_fingerprint}" >"${build_layout_stamp}"

if [[ ! -f "${module_path}" ]]; then
	echo "Expected module was not produced: ${module_path}" >&2
	exit 1
fi

echo "Stopping userspace processes that may still hold the module"
teardown_runtime_state
stop_camera_users

depends="$(run_root modinfo -F depends "${module_path}" 2>/dev/null || true)"
if [[ -n "${depends}" ]]; then
	IFS=',' read -r -a dep_array <<<"${depends}"
else
	dep_array=( "${fallback_module_dependencies[@]}" )
fi

if (( ${#dep_array[@]} )); then
	echo "Loading dependencies: ${dep_array[*]}"
	for dep in "${dep_array[@]}"; do
		[[ -n "${dep}" ]] || continue
		run_root modprobe "${dep}"
	done
fi

echo "Ensuring i2c-dev is loaded for /dev/i2c-* nodes"
run_root modprobe i2c-dev

if lsmod | awk '{print $1}' | grep -Fxq "${module_name}"; then
	echo "Unloading previous ${module_name} module"
	if ! run_root rmmod "${module_name}"; then
		echo "Initial module unload failed; evicting remaining Sensorium-owned device holders and retrying" >&2
		evict_device_holders
		run_root rmmod "${module_name}"
	fi
fi

for legacy_module in "${legacy_modules[@]}"; do
	if lsmod | awk '{print $1}' | grep -Fxq "${legacy_module}"; then
		echo "Unloading legacy ${legacy_module} module"
		run_root rmmod "${legacy_module}"
	fi
done

echo "Loading ${module_path}"
if [[ -n "${sensorium_adapter}" ]]; then
	module_args="${module_args:+${module_args} }adapter=${sensorium_adapter}"
fi
if [[ -n "${sensorium_transport}" ]]; then
	module_args="${module_args:+${module_args} }transport=${sensorium_transport}"
fi
if [[ -n "${sensorium_instance}" ]]; then
	module_args="${module_args:+${module_args} }instance=${sensorium_instance}"
fi
if [[ -n "${sensorium_transport_device_name}" ]]; then
	module_args="${module_args:+${module_args} }transport_device_name=${sensorium_transport_device_name}"
fi
if [[ "${sensorium_transport}" == "i2c" && -n "${sensorium_i2c_address}" ]]; then
	module_args="${module_args:+${module_args} }i2c_address=${sensorium_i2c_address}"
fi
if [[ -n "${sensorium_fault_mode}" ]]; then
	module_args="${module_args:+${module_args} }fault_mode=${sensorium_fault_mode}"
fi
if [[ "${sensorium_adapter}" == "camera" && -n "${sensorium_family}" ]]; then
	module_args="${module_args:+${module_args} }family=${sensorium_family}"
fi
if [[ "${sensorium_adapter}" == "camera" && -n "${sensorium_sensor}" ]]; then
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
if ! run_root dmesg | tail -n 20; then
	echo "Could not read kernel log lines." >&2
fi
