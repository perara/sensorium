#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

camera_cycles="${BURNIN_CAMERA_CYCLES:-2}"
runtime_stress_iterations="${BURNIN_RUNTIME_STRESS_ITERATIONS:-3}"
runtime_stress_model="${BURNIN_RUNTIME_STRESS_MODEL:-models/runtime/rpi-multibus-burnin.yaml}"
remote_verify_sync_repair="${BURNIN_VERIFY_SYNC_REPAIR:-1}"
camera_matrix_sensors="${BURNIN_CAMERA_MATRIX_SENSORS:-imx708 imx219 imx477}"
target_name="${1:-${sensorium_sensor}}"

if ! [[ "${camera_cycles}" =~ ^[0-9]+$ ]]; then
	echo "BURNIN_CAMERA_CYCLES must be a non-negative integer" >&2
	exit 2
fi

if ! [[ "${runtime_stress_iterations}" =~ ^[0-9]+$ ]] || [[ "${runtime_stress_iterations}" -lt 1 ]]; then
	echo "BURNIN_RUNTIME_STRESS_ITERATIONS must be a positive integer" >&2
	exit 2
fi

run_step() {
	local label="$1"
	shift

	echo
	echo "==> ${label}"
	if "$@"; then
		return 0
	fi

	echo
	echo "Burn-in step failed: ${label}" >&2
	echo "Recent remote kernel log:" >&2
	"${script_dir}/remote-klogs.sh" 200 || true
	exit 1
}

run_step "Baseline remote regression" \
	env REMOTE_VERIFY_SYNC_REPAIR="${remote_verify_sync_repair}" \
		RUNTIME_STRESS_ITERATIONS="${runtime_stress_iterations}" \
		RUNTIME_STRESS_MODEL="${runtime_stress_model}" \
		"${script_dir}/remote-regression.sh" "${target_name}"

for ((iteration = 1; iteration <= camera_cycles; iteration++)); do
	run_step "Camera restart cycle ${iteration}/${camera_cycles}" \
		"${script_dir}/remote-cycle.sh" "${target_name}"
	run_step "Camera contract cycle ${iteration}/${camera_cycles}" \
		"${script_dir}/remote-assert-camera-contract.sh" "${target_name}"
done

if [[ -n "${camera_matrix_sensors// }" ]]; then
	run_step "Representative camera profile matrix" \
		env CAMERA_MATRIX_SENSORS="${camera_matrix_sensors}" \
		"${script_dir}/remote-camera-matrix.sh"
fi

echo
echo "Remote burn-in complete."
