#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
model_path="${1:-${repo_root}/models/runtime/rpi-multibus-scale.yaml}"
iterations="${2:-${RUNTIME_STRESS_ITERATIONS:-3}}"
restart_daemon="${RUNTIME_STRESS_RESTART_DAEMON:-1}"
auto_resync="${RUNTIME_STRESS_AUTO_RESYNC:-1}"

if ! [[ "${iterations}" =~ ^[0-9]+$ ]] || [[ "${iterations}" -lt 1 ]]; then
	echo "iterations must be a positive integer" >&2
	exit 1
fi

for ((iteration = 1; iteration <= iterations; iteration++)); do
	echo
	echo "Runtime stress iteration ${iteration}/${iterations}"
	if (( iteration == 1 )); then
		"${repo_root}/scripts/local/smoke-runtime-model.sh" "${model_path}"
		continue
	fi

	if [[ "${restart_daemon}" == "1" ]]; then
		echo "Restarting sensoriumd before iteration ${iteration}"
		"${repo_root}/scripts/runtime/sensoriumctl" daemon stop
		"${repo_root}/scripts/runtime/sensoriumctl" daemon start
	fi

	RUNTIME_SMOKE_SKIP_APPLY=1 "${repo_root}/scripts/local/smoke-runtime-model.sh" "${model_path}"

	health_status="$("${repo_root}/scripts/runtime/sensoriumctl" runtime health | awk '/^status:/{print $2; exit}')"
	if [[ "${health_status}" != "ok" ]]; then
		echo "Runtime health after iteration ${iteration}: ${health_status}" >&2
		if [[ "${auto_resync}" == "1" ]]; then
			echo "Attempting runtime resync..."
			"${repo_root}/scripts/runtime/sensoriumctl" runtime resync
			health_status="$("${repo_root}/scripts/runtime/sensoriumctl" runtime health | awk '/^status:/{print $2; exit}')"
		fi
	fi
	if [[ "${health_status}" != "ok" ]]; then
		echo "Runtime stress failed: runtime health remained ${health_status}" >&2
		exit 1
	fi
done

echo
echo "Runtime stress complete."
