#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
source_url="${1:-https://filesamples.com/samples/video/mp4/sample_640x360.mp4}"
test_detect="${TEST_DETECT:-1}"
test_raw="${TEST_RAW:-1}"
test_processed="${TEST_PROCESSED:-1}"
test_record="${TEST_RECORD:-0}"
record_seconds="${RECORD_SECONDS:-1}"
max_sensors="${MAX_SENSORS:-0}"
timestamp="$(date +%Y%m%d-%H%M%S)"
results_dir="${RESULTS_DIR:-${repo_root}/.cache/all-sensors-${timestamp}}"
results_tsv="${results_dir}/results.tsv"
summary_txt="${results_dir}/summary.txt"

# shellcheck disable=SC1091
source "${script_dir}/remote-common.sh"

mkdir -p "${results_dir}/logs"

mapfile -t sensors < <("${script_dir}/list-sensorium-sensors.sh")

if (( max_sensors > 0 )) && (( max_sensors < ${#sensors[@]} )); then
	sensors=( "${sensors[@]:0:max_sensors}" )
fi

run_step() {
	local sensor="$1"
	local step="$2"
	local logfile="$3"
	shift 3

	if "$@" >"${logfile}" 2>&1; then
		printf 'pass'
		return 0
	fi

	printf 'fail'
	return 1
}

echo "Preparing remote repo on ${remote_target}"
"${script_dir}/remote-sync.sh" >/dev/null
SKIP_SYNC=1 "${script_dir}/remote-build-libcamera-capture.sh" >/dev/null

printf 'sensor\tdetect\traw\tprocessed\trecord\toverall\n' >"${results_tsv}"

total=0
passed=0
failed=0

for sensor in "${sensors[@]}"; do
	detect_result="skip"
	raw_result="skip"
	processed_result="skip"
	record_result="skip"
	overall="pass"

	total=$((total + 1))
	echo
	echo "==> Testing ${sensor} (${total}/${#sensors[@]})"

	common_env=(
		env
		SENSORIUM_FAMILY=imx
		SENSORIUM_SENSOR="${sensor}"
		SKIP_SYNC=1
		SENSORIUM_SKIP_IPA_SYNC=1
	)

	if [[ "${test_detect}" == "1" ]]; then
		logfile="${results_dir}/logs/${sensor}-detect.log"
		detect_result="$("${common_env[@]}" "${script_dir}/remote-cycle.sh" "${sensor}" >"${logfile}" 2>&1 && printf 'pass' || printf 'fail')"
		if [[ "${detect_result}" != "pass" ]]; then
			overall="fail"
		fi
	else
		"${common_env[@]}" "${script_dir}/remote-reload.sh" >/dev/null 2>&1 || true
	fi

	if [[ "${overall}" == "pass" && "${test_raw}" == "1" ]]; then
		logfile="${results_dir}/logs/${sensor}-raw.log"
		raw_result="$("${common_env[@]}" CAPTURE_ROLE=raw "${script_dir}/remote-smoke-url-stream.sh" "${source_url}" >"${logfile}" 2>&1 && printf 'pass' || printf 'fail')"
		if [[ "${raw_result}" != "pass" ]]; then
			overall="fail"
		fi
	fi

	if [[ "${overall}" == "pass" && "${test_processed}" == "1" ]]; then
		logfile="${results_dir}/logs/${sensor}-processed.log"
		processed_result="$("${common_env[@]}" CAPTURE_ROLE=viewfinder "${script_dir}/remote-smoke-url-stream.sh" "${source_url}" >"${logfile}" 2>&1 && printf 'pass' || printf 'fail')"
		if [[ "${processed_result}" != "pass" ]]; then
			overall="fail"
		fi
	fi

	if [[ "${overall}" == "pass" && "${test_record}" == "1" ]]; then
		logfile="${results_dir}/logs/${sensor}-record.log"
		record_result="$("${common_env[@]}" CAPTURE_ROLE=raw RECORD_SECONDS="${record_seconds}" "${script_dir}/remote-record-url-video.sh" "${source_url}" >"${logfile}" 2>&1 && printf 'pass' || printf 'fail')"
		if [[ "${record_result}" != "pass" ]]; then
			overall="fail"
		fi
	fi

	if [[ "${overall}" == "pass" ]]; then
		passed=$((passed + 1))
	else
		failed=$((failed + 1))
	fi

	printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
		"${sensor}" "${detect_result}" "${raw_result}" "${processed_result}" \
		"${record_result}" "${overall}" >>"${results_tsv}"

	echo "Result: detect=${detect_result} raw=${raw_result} processed=${processed_result} record=${record_result} overall=${overall}"
done

{
	echo "results_dir=${results_dir}"
	echo "tested=${total}"
	echo "passed=${passed}"
	echo "failed=${failed}"
	echo "results_tsv=${results_tsv}"
} | tee "${summary_txt}"

if (( failed > 0 )); then
	echo
	echo "Failures:"
	awk -F '\t' 'NR > 1 && $6 != "pass" { print $1 }' "${results_tsv}"
	exit 1
fi
