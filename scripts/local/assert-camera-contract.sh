#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib" && pwd)/sensorium-common.sh"

sensorium_require_clean_env
target_name="${1:-${sensorium_sensor}}"
expected_model="${target_name} simulator"
expected_driver="imx7-csi"
cmd_timeout_seconds="${CMD_TIMEOUT_SECONDS:-8}"

need_cmd() {
	local cmd="$1"
	if ! command -v "${cmd}" >/dev/null 2>&1; then
		echo "Missing required command: ${cmd}" >&2
		exit 1
	fi
}

need_cmd media-ctl
need_cmd v4l2-ctl

find_media_device() {
	local media_dev

	for media_dev in /dev/media*; do
		[[ -e "${media_dev}" ]] || continue
		if timeout "${cmd_timeout_seconds}" media-ctl -d "${media_dev}" -p 2>/dev/null | grep -Fq "${target_name}"; then
			printf '%s\n' "${media_dev}"
			return 0
		fi
	done

	return 1
}

media_dev="$(find_media_device)"
if [[ -z "${media_dev}" ]]; then
	echo "Could not find a media graph containing ${target_name}" >&2
	exit 1
fi

topology="$(timeout "${cmd_timeout_seconds}" media-ctl -d "${media_dev}" -p)"
printf '%s\n' "${topology}"

for required in "${target_name}" "${expected_model}" "sensorium-inject" "sensorium-capture" "${expected_driver}"; do
	if ! grep -Fq "${required}" <<<"${topology}"; then
		echo "Camera contract check failed: missing '${required}' in media graph ${media_dev}" >&2
		exit 1
	fi
done

subdev_path="$(awk '/device node name/ && /v4l-subdev/ {gsub(/.*device node name /, "", $0); print $0; exit}' <<<"${topology}")"
if [[ -z "${subdev_path}" || ! -e "${subdev_path}" ]]; then
	echo "Could not resolve the sensor subdevice path from ${media_dev}" >&2
	exit 1
fi

ctrls="$(timeout "${cmd_timeout_seconds}" v4l2-ctl -d "${subdev_path}" --list-ctrls 2>/dev/null || true)"
printf '%s\n' "${ctrls}"
for ctrl_name in exposure analogue_gain pixel_rate horizontal_blanking vertical_blanking; do
	if ! grep -Fq "${ctrl_name}" <<<"${ctrls}"; then
		echo "Camera contract check failed: missing control '${ctrl_name}' on ${subdev_path}" >&2
		exit 1
	fi
done

echo
echo "Camera graph/control contract ok on ${media_dev} (${subdev_path})"
