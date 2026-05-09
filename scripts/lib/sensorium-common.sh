#!/usr/bin/env bash
set -euo pipefail

sensorium_script_path="$(readlink -f "${BASH_SOURCE[0]}")"
sensorium_repo_root="$(cd "$(dirname "${sensorium_script_path}")/../.." && pwd)"
sensorium_adapter="${SENSORIUM_ADAPTER:-camera}"
sensorium_transport="${SENSORIUM_TRANSPORT:-virtual}"
sensorium_instance="${SENSORIUM_INSTANCE:-default}"
sensorium_transport_device_name="${SENSORIUM_TRANSPORT_DEVICE_NAME:-}"
sensorium_i2c_address="${SENSORIUM_I2C_ADDRESS:-}"
sensorium_fault_mode="${SENSORIUM_FAULT_MODE:-none}"
sensorium_family="${SENSORIUM_FAMILY:-imx}"
sensorium_sensor="${SENSORIUM_SENSOR:-imx708}"
sensorium_default_libcamera_bindir="/opt/libcamera-custom/bin"

if [[ -n "${LIBCAMERA_BINDIR:-}" ]]; then
	sensorium_default_libcamera_bindir="${LIBCAMERA_BINDIR}"
elif [[ -x "/opt/libcamera-custom/bin/cam" ]]; then
	sensorium_default_libcamera_bindir="/opt/libcamera-custom/bin"
elif cam_path="$(command -v cam 2>/dev/null)"; then
	if resolved_cam_path="$(readlink -f "${cam_path}" 2>/dev/null)"; then
		cam_path="${resolved_cam_path}"
	fi
	sensorium_default_libcamera_bindir="$(dirname "${cam_path}")"
fi

sensorium_default_libcamera_prefix="$(dirname "${sensorium_default_libcamera_bindir}")"
sensorium_libcamera_prefix="${LIBCAMERA_PREFIX:-${sensorium_default_libcamera_prefix}}"
sensorium_libcamera_libdir="${LIBCAMERA_LIBDIR:-${sensorium_libcamera_prefix}/lib/x86_64-linux-gnu}"
sensorium_libcamera_bindir="${LIBCAMERA_BINDIR:-${sensorium_default_libcamera_bindir}}"
sensorium_libcamera_pkgconfig_dir="${LIBCAMERA_PKG_CONFIG_PATH:-${sensorium_libcamera_libdir}/pkgconfig}"
sensorium_libcamera_ipa_dir="${LIBCAMERA_IPA_DIR:-${sensorium_libcamera_prefix}/share/libcamera/ipa/simple}"

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	echo "sensorium-common.sh is an internal library; source it from Sensorium scripts." >&2
	exit 2
fi

sensorium_require_clean_env() {
	local legacy_vars=(
		IMX_SIM_SENSOR
		SONY_IMX_SIM_INSMOD_ARGS
		IMX708_SIM_INSMOD_ARGS
	)
	local legacy_var

	for legacy_var in "${legacy_vars[@]}"; do
		if [[ -n "${!legacy_var:-}" ]]; then
			echo "Legacy environment variable ${legacy_var} is no longer supported." >&2
			echo "Use SENSORIUM_FAMILY, SENSORIUM_SENSOR, and SENSORIUM_INSMOD_ARGS instead." >&2
			exit 2
		fi
	done
}

sensorium_export_libcamera_runtime() {
	if [[ -d "${sensorium_libcamera_libdir}" ]]; then
		export LD_LIBRARY_PATH="${sensorium_libcamera_libdir}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
	fi

	if [[ -d "${sensorium_libcamera_ipa_dir}" ]]; then
		export LIBCAMERA_IPA_CONFIG_PATH="${sensorium_libcamera_ipa_dir}${LIBCAMERA_IPA_CONFIG_PATH:+:${LIBCAMERA_IPA_CONFIG_PATH}}"
	fi
}

sensorium_setup_libcamera_build_env() {
	if [[ -d "${sensorium_libcamera_pkgconfig_dir}" ]]; then
		export PKG_CONFIG_PATH="${sensorium_libcamera_pkgconfig_dir}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
	fi
}

sensorium_resolve_camera_list_cmd() {
	local -n cmd_ref="$1"

	if [[ -x "${sensorium_libcamera_bindir}/cam" ]]; then
		sensorium_export_libcamera_runtime
		cmd_ref=("${sensorium_libcamera_bindir}/cam" -l)
		return 0
	fi

	if command -v cam >/dev/null 2>&1; then
		cmd_ref=("$(command -v cam)" -l)
		return 0
	fi

	if command -v libcamera-hello >/dev/null 2>&1; then
		cmd_ref=("$(command -v libcamera-hello)" --list-cameras)
		return 0
	fi

	return 1
}

sensorium_resolve_cam_binary() {
	local -n cmd_ref="$1"

	if [[ -x "${sensorium_libcamera_bindir}/cam" ]]; then
		sensorium_export_libcamera_runtime
		cmd_ref=("${sensorium_libcamera_bindir}/cam")
		return 0
	fi

	if command -v cam >/dev/null 2>&1; then
		cmd_ref=("$(command -v cam)")
		return 0
	fi

	return 1
}
