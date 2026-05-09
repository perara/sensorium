#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

if [[ "${SKIP_SYNC:-0}" != "1" ]]; then
	"${script_dir}/remote-sync.sh"
fi

echo "Reloading module on ${remote_target}"
if [[ -n "${SENSORIUM_INSMOD_ARGS:-}" || -n "${SENSORIUM_ADAPTER:-}" || \
      -n "${SENSORIUM_TRANSPORT:-}" || -n "${SENSORIUM_INSTANCE:-}" || \
      -n "${SENSORIUM_TRANSPORT_DEVICE_NAME:-}" || \
      -n "${SENSORIUM_I2C_ADDRESS:-}" || \
      -n "${SENSORIUM_FAULT_MODE:-}" || -n "${SENSORIUM_FAMILY:-}" || \
      -n "${SENSORIUM_SENSOR:-}" ]]; then
	printf -v remote_module_args_q "%q" "${SENSORIUM_INSMOD_ARGS:-}"
	printf -v remote_adapter_q "%q" "${sensorium_adapter}"
	printf -v remote_transport_q "%q" "${sensorium_transport}"
	printf -v remote_instance_q "%q" "${sensorium_instance}"
	printf -v remote_transport_device_name_q "%q" "${sensorium_transport_device_name}"
	printf -v remote_i2c_address_q "%q" "${sensorium_i2c_address}"
	printf -v remote_fault_mode_q "%q" "${sensorium_fault_mode}"
	printf -v remote_family_q "%q" "${sensorium_family}"
	printf -v remote_sensor_q "%q" "${sensorium_sensor}"
	remote_ssh_retry "cd '${remote_repo_dir}' && SENSORIUM_ADAPTER=${remote_adapter_q} SENSORIUM_TRANSPORT=${remote_transport_q} SENSORIUM_INSTANCE=${remote_instance_q} SENSORIUM_TRANSPORT_DEVICE_NAME=${remote_transport_device_name_q} SENSORIUM_I2C_ADDRESS=${remote_i2c_address_q} SENSORIUM_FAULT_MODE=${remote_fault_mode_q} SENSORIUM_FAMILY=${remote_family_q} SENSORIUM_SENSOR=${remote_sensor_q} SENSORIUM_INSMOD_ARGS=${remote_module_args_q} ./scripts/runtime/reload-sensorium.sh"
else
	remote_ssh_retry "cd '${remote_repo_dir}' && ./scripts/runtime/reload-sensorium.sh"
fi
