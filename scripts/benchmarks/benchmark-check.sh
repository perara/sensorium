#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
artifact_dir="${SENSORIUM_BENCHMARK_DIR:-${repo_root}/.cache/benchmarks}"

artifact_before="${1:-}"
artifact_after="${2:-}"

compare_args=()
if [[ -n "${artifact_before}" || -n "${artifact_after}" ]]; then
	if [[ -z "${artifact_before}" || -z "${artifact_after}" ]]; then
		echo "usage: $0 [before-artifact after-artifact]" >&2
		exit 2
	fi
	compare_args+=( "${artifact_before}" "${artifact_after}" )
else
	compare_args+=( --artifact-dir "${artifact_dir}" )
	if [[ "${BENCHMARK_REQUIRE_BASELINE:-0}" != "1" ]]; then
		compare_args+=( --allow-missing-baseline )
	fi
fi

if [[ "${BENCHMARK_FAIL_ON_REGRESSION:-1}" == "1" ]]; then
	compare_args+=( --fail-on-regression )
fi
compare_args+=( --prefer-sustained-rate )
if [[ -n "${BENCHMARK_MAX_FIRST_FRAME_DELTA_MS:-}" ]]; then
	compare_args+=( --max-first-frame-delta-ms "${BENCHMARK_MAX_FIRST_FRAME_DELTA_MS}" )
fi
default_timestamp_ratio="${BENCHMARK_MIN_TIMESTAMP_FPS_RATIO:-}"
if [[ -z "${default_timestamp_ratio}" && -n "${BENCHMARK_MIN_RECORD_FPS_RATIO:-}" ]]; then
	default_timestamp_ratio="${BENCHMARK_MIN_RECORD_FPS_RATIO}"
fi
if [[ -n "${default_timestamp_ratio}" ]]; then
	compare_args+=( --min-timestamp-fps-ratio "${default_timestamp_ratio}" )
fi
if [[ -n "${BENCHMARK_BASELINE_WINDOW:-}" ]]; then
	compare_args+=( --baseline-window "${BENCHMARK_BASELINE_WINDOW}" )
fi

"${script_dir}/compare-benchmarks.py" "${compare_args[@]}"
