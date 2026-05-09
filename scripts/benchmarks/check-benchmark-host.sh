#!/usr/bin/env bash
set -euo pipefail

mode="qemu"
qemu_cpus="${QEMU_CPUS:-4}"
strict="${BENCHMARK_STRICT_HOST_BASELINE:-0}"

while [[ $# -gt 0 ]]; do
	case "$1" in
	--mode)
		mode="${2:-}"
		shift 2
		;;
	--qemu-cpus)
		qemu_cpus="${2:-}"
		shift 2
		;;
	--strict)
		strict=1
		shift
		;;
	*)
		echo "Unknown argument: $1" >&2
		exit 1
		;;
	esac
done

host_nproc="$(nproc)"
load1="$(awk '{print $1}' /proc/loadavg 2>/dev/null || echo 0)"
kvm_access="false"
if [[ -r /dev/kvm && -w /dev/kvm ]]; then
	kvm_access="true"
fi

reasons=()
qemu_accel="native"
if [[ "${mode}" == "qemu" ]]; then
	if [[ "${kvm_access}" == "true" ]]; then
		qemu_accel="kvm"
	else
		qemu_accel="tcg"
		reasons+=( "qemu_accel_tcg" )
	fi
	if (( host_nproc < qemu_cpus )); then
		reasons+=( "host_nproc_lt_qemu_cpus" )
	fi
fi

load_limit="$(awk -v n="${host_nproc}" 'BEGIN { printf "%.2f", (n * 1.50) }')"
if awk -v l="${load1}" -v max="${load_limit}" 'BEGIN { exit !(l > max) }'; then
	reasons+=( "host_load_high" )
fi

env_ok="true"
if (( ${#reasons[@]} != 0 )); then
	env_ok="false"
fi

echo "benchmark_mode=${mode}"
echo "benchmark_host_nproc=${host_nproc}"
echo "benchmark_host_load1=${load1}"
echo "benchmark_host_kvm_access=${kvm_access}"
echo "benchmark_qemu_accel=${qemu_accel}"
echo "benchmark_env_ok=${env_ok}"
if (( ${#reasons[@]} != 0 )); then
	echo "benchmark_env_reasons=${reasons[*]}"
fi

if [[ "${strict}" == "1" && "${env_ok}" != "true" ]]; then
	exit 1
fi
