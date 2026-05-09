#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
record_seconds="${RECORD_SECONDS:-2}"
runtime_model="${RUNTIME_MODEL:-models/runtime/rpi-multibus.yaml}"
runtime_stress_model="${RUNTIME_STRESS_MODEL:-models/runtime/rpi-multibus-scale.yaml}"
runtime_stress_iterations="${RUNTIME_STRESS_ITERATIONS:-0}"
remote_verify_sync_repair="${REMOTE_VERIFY_SYNC_REPAIR:-0}"
remote_verify_sync_repair_model="${REMOTE_VERIFY_SYNC_REPAIR_MODEL:-models/runtime/rpi-sparse-uart.yaml}"
iio_models_default=(
	"models/iio/environment-i2c.yaml"
	"models/iio/environment-plus-i2c.yaml"
	"models/iio/environment-plus-spi.yaml"
	"models/iio/environment-plus-uart.yaml"
	"models/iio/environment-spi.yaml"
	"models/iio/environment-uart.yaml"
)

if [[ -n "${IIO_MODELS:-}" ]]; then
	# shellcheck disable=SC2206
	iio_models=( ${IIO_MODELS} )
elif [[ -n "${IIO_MODEL:-}" ]]; then
	iio_models=( "${IIO_MODEL}" )
else
	iio_models=( "${iio_models_default[@]}" )
fi

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

target_name="${1:-${sensorium_sensor}}"

run_step() {
	local label="$1"
	shift

	echo
	echo "==> ${label}"
	if "$@"; then
		return 0
	fi

	echo
	echo "Step failed: ${label}" >&2
	echo "Recent remote kernel log:" >&2
	"${script_dir}/remote-klogs.sh" 160 || true
	exit 1
}

run_step "Reload and verify detection" \
	"${script_dir}/remote-cycle.sh" "${target_name}"

run_step "Camera graph and control contract" \
	"${script_dir}/remote-assert-camera-contract.sh" "${target_name}"

run_step "Raw single-frame smoke capture" \
	env REMOTE_AUTO_INSTALL_STREAM_DEPS="${REMOTE_AUTO_INSTALL_STREAM_DEPS:-1}" \
		CAPTURE_ROLE=raw "${script_dir}/remote-smoke-url-stream.sh"

run_step "Processed single-frame smoke capture" \
	env REMOTE_AUTO_INSTALL_STREAM_DEPS="${REMOTE_AUTO_INSTALL_STREAM_DEPS:-1}" \
		CAPTURE_ROLE=viewfinder "${script_dir}/remote-smoke-url-stream.sh"

run_step "Short raw MP4 record" \
	env REMOTE_AUTO_INSTALL_STREAM_DEPS="${REMOTE_AUTO_INSTALL_STREAM_DEPS:-1}" \
		CAPTURE_ROLE=raw RECORD_SECONDS="${record_seconds}" \
		"${script_dir}/remote-record-url-video.sh"

for iio_model in "${iio_models[@]}"; do
	iio_label="${iio_model##*/}"
	iio_label="${iio_label%.yaml}"
	run_step "IIO adapter smoke test (${iio_label})" \
		"${script_dir}/remote-smoke-iio.sh" "${iio_model}"
done

run_step "Runtime multi-device smoke test" \
	"${script_dir}/remote-smoke-runtime.sh" "${runtime_model}"

if [[ "${remote_verify_sync_repair}" == "1" ]]; then
	run_step "Remote sync repair verification (${remote_verify_sync_repair_model##*/})" \
		"${script_dir}/remote-verify-sync-repair.sh" "${remote_verify_sync_repair_model}"
fi

if [[ "${runtime_stress_iterations}" != "0" ]]; then
	run_step "Runtime scale stress test (${runtime_stress_iterations} iteration(s))" \
		"${script_dir}/remote-stress-runtime.sh" "${runtime_stress_model}" "${runtime_stress_iterations}"
fi

echo
echo "Remote regression complete."
