#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/remote-common.sh"

if [[ "${SKIP_SYNC:-0}" != "1" ]]; then
	"${script_dir}/remote-sync.sh"
fi

echo "Reloading module on ${remote_target}"
if [[ -n "${SENSORIUM_INSMOD_ARGS:-}" || -n "${SENSORIUM_FAMILY:-}" || -n "${SENSORIUM_SENSOR:-}" ]]; then
	printf -v remote_module_args_q "%q" "${SENSORIUM_INSMOD_ARGS:-}"
	printf -v remote_family_q "%q" "${sensorium_family}"
	printf -v remote_sensor_q "%q" "${sensorium_sensor}"
	remote_ssh_retry "cd '${remote_repo_dir}' && SENSORIUM_FAMILY=${remote_family_q} SENSORIUM_SENSOR=${remote_sensor_q} SENSORIUM_INSMOD_ARGS=${remote_module_args_q} ./scripts/reload-sensorium.sh"
else
	remote_ssh_retry "cd '${remote_repo_dir}' && ./scripts/reload-sensorium.sh"
fi
