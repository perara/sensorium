#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
source_url="${1:-https://filesamples.com/samples/video/mp4/sample_640x360.mp4}"

# shellcheck disable=SC1091
source "${script_dir}/../lib/qemu-common.sh"

benchmark_artifact_dir="${SENSORIUM_BENCHMARK_DIR:-${sensorium_repo_root}/.cache/benchmarks}"

cleanup() {
	if [[ "${QEMU_KEEP_RUNNING:-0}" != "1" ]]; then
		"${script_dir}/qemu-stop.sh" >/dev/null 2>&1 || true
	fi
}

trap cleanup EXIT

QEMU_RESET_DISK="${QEMU_RESET_DISK:-1}" "${script_dir}/qemu-start.sh"
"${script_dir}/qemu-wait.sh"

qemu_export_remote_env

benchmark_host_output="$("${script_dir}/../benchmarks/check-benchmark-host.sh" --mode qemu --qemu-cpus "${qemu_cpus}")"
printf '%s\n' "${benchmark_host_output}"
benchmark_env_ok="$(printf '%s\n' "${benchmark_host_output}" | awk -F= '/^benchmark_env_ok=/{print $2}')"
benchmark_qemu_accel="$(printf '%s\n' "${benchmark_host_output}" | awk -F= '/^benchmark_qemu_accel=/{print $2}')"
benchmark_host_nproc="$(printf '%s\n' "${benchmark_host_output}" | awk -F= '/^benchmark_host_nproc=/{print $2}')"
benchmark_host_load1="$(printf '%s\n' "${benchmark_host_output}" | awk -F= '/^benchmark_host_load1=/{print $2}')"
benchmark_env_reasons="$(printf '%s\n' "${benchmark_host_output}" | awk -F= '/^benchmark_env_reasons=/{print $2}')"

echo "Provisioning QEMU guest"
SENSORIUM_PROVISION_SKIP_IF_CURRENT="${QEMU_SKIP_PROVISION_IF_CURRENT:-1}" \
	"${script_dir}/../remote/provision-droplet.sh"

echo "Ensuring media-capable remote kernel"
"${script_dir}/../remote/remote-ensure-media-kernel.sh"
qemu_assert_expected_kernel_major

echo "Checking remote libcamera version"
"${script_dir}/../remote/remote-check-libcamera-version.sh"

echo "Syncing repo to QEMU guest"
"${script_dir}/../remote/remote-sync.sh"

echo "Building helper tools in QEMU guest"
SKIP_SYNC=1 "${script_dir}/../remote/remote-build-libcamera-capture.sh"

echo "Reloading and verifying the default camera path"
SKIP_SYNC=1 "${script_dir}/../remote/remote-cycle.sh"

echo "Running QEMU benchmark matrix"
benchmark_output="$(SKIP_SYNC=1 "${script_dir}/../remote/remote-benchmark-matrix.sh" "${source_url}")"
printf '%s\n' "${benchmark_output}"

remote_kernel="$(qemu_ssh "uname -r" | tail -n 1 | tr -d '\r')"
remote_nproc="$(qemu_ssh "nproc" | tail -n 1 | tr -d '\r')"
artifact_path="$(
	"${script_dir}/../benchmarks/record-benchmark-artifact.py" \
		--scenario qemu-benchmark-matrix \
		--artifact-dir "${benchmark_artifact_dir}" \
		--source-url "${source_url}" \
		--remote-kernel "${remote_kernel}" \
		--remote-target "${qemu_guest_user}@${qemu_guest_host}:${qemu_ssh_port}" \
		--meta sensor="${SENSORIUM_SENSOR:-imx708}" \
		--meta fps_list="${FPS_LIST:-10 20 30}" \
		--meta benchmark_env_ok="${benchmark_env_ok}" \
		--meta benchmark_qemu_accel="${benchmark_qemu_accel}" \
		--meta benchmark_host_nproc="${benchmark_host_nproc}" \
		--meta benchmark_host_load1="${benchmark_host_load1}" \
		--meta benchmark_env_reasons="${benchmark_env_reasons}" \
		--meta remote_nproc="${remote_nproc}" \
		--meta qemu_cpus="${qemu_cpus}" \
		--meta qemu_memory_mb="${qemu_memory_mb}" \
		<<<"${benchmark_output}"
)"

echo
echo "Benchmark artifact: ${artifact_path}"

if [[ -n "${BENCHMARK_BASELINE:-}" ]]; then
	echo
	echo "Benchmark comparison:"
	compare_args=()
	if [[ "${BENCHMARK_FAIL_ON_REGRESSION:-0}" == "1" ]]; then
		compare_args+=(--fail-on-regression)
	fi
	compare_args+=(--prefer-sustained-rate)
	if [[ -n "${BENCHMARK_MAX_FIRST_FRAME_DELTA_MS:-}" ]]; then
		compare_args+=(--max-first-frame-delta-ms "${BENCHMARK_MAX_FIRST_FRAME_DELTA_MS}")
	fi
	default_timestamp_ratio="${BENCHMARK_MIN_TIMESTAMP_FPS_RATIO:-}"
	if [[ -z "${default_timestamp_ratio}" && -n "${BENCHMARK_MIN_RECORD_FPS_RATIO:-}" ]]; then
		default_timestamp_ratio="${BENCHMARK_MIN_RECORD_FPS_RATIO}"
	fi
	if [[ -n "${default_timestamp_ratio}" ]]; then
		compare_args+=(--min-timestamp-fps-ratio "${default_timestamp_ratio}")
	fi
	"${script_dir}/../benchmarks/compare-benchmarks.py" "${BENCHMARK_BASELINE}" "${artifact_path}" "${compare_args[@]}"
fi

echo
echo "QEMU benchmark matrix complete."
