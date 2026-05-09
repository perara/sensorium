#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
source_url="${1:-https://filesamples.com/samples/video/mp4/sample_640x360.mp4}"
capture_role="${CAPTURE_ROLE:-raw}"
stream_fps="${STREAM_FPS:-10}"
benchmark_seconds="${BENCHMARK_SECONDS:-5}"
warmup_seconds="${WARMUP_SECONDS:-3}"
capture_timeout_seconds="${CAPTURE_TIMEOUT_SECONDS:-$((benchmark_seconds * 4 + 20))}"
sensor_fps="${SENSOR_FPS:-}"
url_stream_id="${URL_STREAM_ID:-benchmark-$$-$(date +%s)}"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

benchmark_host_output="$("${script_dir}/../benchmarks/check-benchmark-host.sh" --mode local)"
printf '%s\n' "${benchmark_host_output}"

if [[ -n "${STREAM_WIDTH:-}" && -n "${STREAM_HEIGHT:-}" ]]; then
	stream_width="${STREAM_WIDTH}"
	stream_height="${STREAM_HEIGHT}"
elif [[ "${capture_role}" == "raw" ]]; then
	read -r stream_width stream_height < <(sensorium_default_raw_size)
else
	read -r stream_width stream_height < <(sensorium_default_processed_size)
fi

case "${capture_role}" in
raw)
	inject_width="${INJECT_WIDTH:-${stream_width}}"
	inject_height="${INJECT_HEIGHT:-${stream_height}}"
	stream_pixel_format="RG10"
	if [[ -z "${sensor_fps}" ]]; then
		sensor_fps="${stream_fps}"
	fi
	;;
viewfinder|still|video)
	if [[ -n "${INJECT_WIDTH:-}" && -n "${INJECT_HEIGHT:-}" ]]; then
		inject_width="${INJECT_WIDTH}"
		inject_height="${INJECT_HEIGHT}"
	else
		read -r inject_width inject_height < <(sensorium_default_processed_inject_size)
	fi
	stream_pixel_format="BGR4"
	;;
*)
	echo "Unsupported CAPTURE_ROLE: ${capture_role}" >&2
	exit 2
	;;
esac

reference_fps="${sensor_fps:-${stream_fps}}"
benchmark_frames="${BENCHMARK_FRAMES:-$((reference_fps * benchmark_seconds))}"

SKIP_SYNC=1 "${script_dir}/remote-reload.sh" >/dev/null

STREAM_WIDTH="${inject_width}" STREAM_HEIGHT="${inject_height}" \
	STREAM_PIXEL_FORMAT="${stream_pixel_format}" \
	STREAM_FPS="${stream_fps}" \
	URL_STREAM_ID="${url_stream_id}" \
	"${script_dir}/remote-start-url-stream.sh" "${source_url}"

cleanup() {
	URL_STREAM_ID="${url_stream_id}" "${script_dir}/remote-stop-url-stream.sh" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Waiting ${warmup_seconds}s for the remote injector to warm up..."
sleep "${warmup_seconds}"

remote_install_ipa_config

printf -v capture_role_q "%q" "${capture_role}"
printf -v stream_width_q "%q" "${stream_width}"
printf -v stream_height_q "%q" "${stream_height}"
printf -v benchmark_frames_q "%q" "${benchmark_frames}"
printf -v capture_timeout_ms_q "%q" "$((capture_timeout_seconds * 1000))"
printf -v sensor_fps_q "%q" "${sensor_fps}"
printf -v stream_pid_file_q "%q" ".cache/url-stream-${url_stream_id}.pid"

metrics_output="$(remote_ssh_retry bash -s <<EOF
set -euo pipefail
cd '${remote_repo_dir}'

stream_pid_file=${stream_pid_file_q}
latency_log='/tmp/sensorium-benchmark-latency.log'
latency_raw='/tmp/sensorium-benchmark-latency.raw'
record_log='/tmp/sensorium-benchmark-record.log'
record_metrics='/tmp/sensorium-benchmark-record.metrics'
record_raw='/tmp/sensorium-benchmark-record.raw'
stream_root_pid=""

collect_tree_pids() {
	local root="\$1"
	local child

	[[ -n "\${root}" ]] || return 0
	[[ -d "/proc/\${root}" ]] || return 0

	echo "\${root}"
	for child in \$(pgrep -P "\${root}" || true); do
		collect_tree_pids "\${child}"
	done
}

sum_proc_ticks() {
	local root="\$1"
	local pid
	local ticks=0
	local pid_ticks

	while read -r pid; do
		[[ -n "\${pid}" ]] || continue
		[[ -r "/proc/\${pid}/stat" ]] || continue
		pid_ticks=\$(awk '{print \$14 + \$15}' "/proc/\${pid}/stat")
		ticks=\$((ticks + pid_ticks))
	done < <(collect_tree_pids "\${root}" | sort -u)

	echo "\${ticks}"
}

rm -f "\${latency_log}" "\${latency_raw}" "\${record_log}" "\${record_metrics}" "\${record_raw}"

if [[ -f "\${stream_pid_file}" ]]; then
	stream_root_pid="\$(cat "\${stream_pid_file}")"
fi

latency_start_ns=\$(date +%s%N)
source ./scripts/lib/sensorium-common.sh
sensorium_export_libcamera_runtime
./tools/libcamera-record \
		--role ${capture_role_q} \
		--width ${stream_width_q} \
		--height ${stream_height_q} \
		--frames 1 \
		${sensor_fps:+--fps ${sensor_fps_q}} \
		--timeout-ms ${capture_timeout_ms_q} \
		--output "\${latency_raw}" > "\${latency_log}" 2>&1
latency_end_ns=\$(date +%s%N)

clk_tck=\$(getconf CLK_TCK)
record_start_ns=\$(date +%s%N)
stream_ticks_before=\$(sum_proc_ticks "\${stream_root_pid}")
CAPTURE_ROLE=${capture_role_q} \
STREAM_WIDTH=${stream_width_q} \
STREAM_HEIGHT=${stream_height_q} \
BENCHMARK_FRAMES=${benchmark_frames_q} \
SENSOR_FPS=${sensor_fps_q} \
CAPTURE_TIMEOUT_MS=${capture_timeout_ms_q} \
RECORD_RAW_PATH="\${record_raw}" \
RECORD_METRICS_PATH="\${record_metrics}" \
python3 - <<'PY' > "\${record_log}" 2>&1
import os
import resource
import subprocess
import sys

cmd = [
    "./tools/libcamera-record",
    "--role", os.environ["CAPTURE_ROLE"],
    "--width", os.environ["STREAM_WIDTH"],
    "--height", os.environ["STREAM_HEIGHT"],
    "--frames", os.environ["BENCHMARK_FRAMES"],
    "--timeout-ms", os.environ["CAPTURE_TIMEOUT_MS"],
    "--output", os.environ["RECORD_RAW_PATH"],
]

sensor_fps = os.environ.get("SENSOR_FPS", "")
if sensor_fps:
    cmd.extend(["--fps", sensor_fps])

result = subprocess.run(cmd, stdout=sys.stdout, stderr=sys.stderr)
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
with open(os.environ["RECORD_METRICS_PATH"], "w", encoding="utf-8") as handle:
    handle.write(f"capture_user_s={usage.ru_utime:.6f}\n")
    handle.write(f"capture_sys_s={usage.ru_stime:.6f}\n")
    handle.write(f"capture_rss_kb={usage.ru_maxrss}\n")
sys.exit(result.returncode)
PY
record_end_ns=\$(date +%s%N)
stream_ticks_after=\$(sum_proc_ticks "\${stream_root_pid}")

capture_user_s=\$(awk -F= '/^capture_user_s=/{print \$2}' "\${record_metrics}")
capture_sys_s=\$(awk -F= '/^capture_sys_s=/{print \$2}' "\${record_metrics}")
capture_rss_kb=\$(awk -F= '/^capture_rss_kb=/{print \$2}' "\${record_metrics}")
timestamp_span_s=\$(awk -F= '/^timestamp_span_s=/{print \$2}' "\${record_log}")
timestamp_fps=\$(awk -F= '/^timestamp_fps=/{print \$2}' "\${record_log}")
remote_nproc=\$(nproc)

latency_ms=\$(( (latency_end_ns - latency_start_ns) / 1000000 ))
record_elapsed_ns=\$((record_end_ns - record_start_ns))
record_elapsed_s=\$(awk -v ns="\${record_elapsed_ns}" 'BEGIN { printf "%.3f", ns / 1000000000.0 }')
record_fps=\$(awk -v frames="${benchmark_frames}" -v ns="\${record_elapsed_ns}" 'BEGIN { if (ns <= 0) printf "0.00"; else printf "%.2f", frames * 1000000000.0 / ns }')
stream_cpu_pct=\$(awk -v before="\${stream_ticks_before}" -v after="\${stream_ticks_after}" -v ns="\${record_elapsed_ns}" -v hz="\${clk_tck}" 'BEGIN { delta = after - before; secs = ns / 1000000000.0; if (secs <= 0 || delta < 0) printf "0.00"; else printf "%.2f", (delta / hz) / secs * 100.0 }')
capture_cpu_pct=\$(awk -v user="\${capture_user_s}" -v sys="\${capture_sys_s}" -v ns="\${record_elapsed_ns}" 'BEGIN { secs = ns / 1000000000.0; if (secs <= 0) printf "0.00"; else printf "%.2f", ((user + sys) / secs) * 100.0 }')
record_bytes=\$(wc -c < "\${record_raw}")

cat <<METRICS
capture_role=${capture_role}
sensor_target_fps=${sensor_fps:-}
stream_width=${stream_width}
stream_height=${stream_height}
stream_fps=${stream_fps}
benchmark_frames=${benchmark_frames}
first_frame_latency_ms=\${latency_ms}
record_elapsed_s=\${record_elapsed_s}
record_fps=\${record_fps}
timestamp_span_s=\${timestamp_span_s}
timestamp_fps=\${timestamp_fps}
stream_cpu_pct=\${stream_cpu_pct}
capture_cpu_pct=\${capture_cpu_pct}
capture_user_s=\${capture_user_s}
capture_sys_s=\${capture_sys_s}
capture_rss_kb=\${capture_rss_kb}
record_bytes=\${record_bytes}
remote_nproc=\${remote_nproc}
METRICS
EOF
)"

echo
echo "Remote benchmark metrics:"
printf '%s\n' "${metrics_output}"

echo
echo "Remote first-frame log:"
remote_ssh_retry "tail -n 40 /tmp/sensorium-benchmark-latency.log"

echo
echo "Remote sustained record log:"
remote_ssh_retry "tail -n 80 /tmp/sensorium-benchmark-record.log"
