#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

sensor_list="${CAMERA_MATRIX_SENSORS:-imx708 imx219 imx477}"

for sensor in ${sensor_list}; do
	echo
	echo "==> Camera matrix sensor ${sensor}"
	env SENSORIUM_SENSOR="${sensor}" \
		"${script_dir}/remote-cycle.sh" "${sensor}"
	env SENSORIUM_SENSOR="${sensor}" \
		"${script_dir}/remote-assert-camera-contract.sh" "${sensor}"
	env SENSORIUM_SENSOR="${sensor}" CAPTURE_ROLE=raw \
		"${script_dir}/remote-smoke-url-stream.sh"
done

echo
echo "Remote camera matrix complete."
