#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sensorium-common.sh"

sensorium_require_clean_env
target_name="${1:-${sensorium_sensor}}"
timeout_seconds="${TIMEOUT_SECONDS:-5}"
cmd_timeout_seconds="${CMD_TIMEOUT_SECONDS:-8}"

need_cmd() {
	local cmd="$1"
	if ! command -v "${cmd}" >/dev/null 2>&1; then
		echo "Missing required command: ${cmd}" >&2
		exit 1
	fi
}

need_cmd media-ctl

declare -a libcamera_cmd
if ! sensorium_resolve_camera_list_cmd libcamera_cmd; then
	echo "Could not find a libcamera camera-listing tool. Install libcamera-tools." >&2
	exit 1
fi

echo "Waiting up to ${timeout_seconds}s for media nodes to settle..."
end_epoch=$((SECONDS + timeout_seconds))
found_media_entity=0

while (( SECONDS <= end_epoch )); do
	for media_dev in /dev/media*; do
		[[ -e "${media_dev}" ]] || continue
		if timeout "${cmd_timeout_seconds}" media-ctl -d "${media_dev}" -p 2>/dev/null | grep -qi "${target_name}"; then
			found_media_entity=1
			break 2
		fi
	done
	sleep 1
done

if (( ! found_media_entity )); then
	echo "Did not find a media entity matching '${target_name}'." >&2
	echo
	echo "Current media topology:"
	for media_dev in /dev/media*; do
		[[ -e "${media_dev}" ]] || continue
		echo "--- ${media_dev} ---"
		timeout "${cmd_timeout_seconds}" media-ctl -d "${media_dev}" -p || true
	done
	exit 1
fi

echo
echo "Running: ${libcamera_cmd[*]}"
libcamera_output="$(timeout "${cmd_timeout_seconds}" "${libcamera_cmd[@]}" 2>&1 || true)"
printf '%s\n' "${libcamera_output}"

if grep -Eiq "^[[:space:]]*[0-9]+:.*${target_name}" <<<"${libcamera_output}"; then
	echo
	echo "libcamera detected a camera matching '${target_name}'."
	exit 0
fi

echo
echo "libcamera did not report a camera matching '${target_name}'." >&2
echo "Media topology for debugging:"
for media_dev in /dev/media*; do
	[[ -e "${media_dev}" ]] || continue
	echo "--- ${media_dev} ---"
	timeout "${cmd_timeout_seconds}" media-ctl -d "${media_dev}" -p || true
done

if command -v v4l2-ctl >/dev/null 2>&1; then
	echo
	echo "V4L2 devices:"
	v4l2-ctl --list-devices || true
fi

exit 1
