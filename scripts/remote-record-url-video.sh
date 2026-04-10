#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
source_url="${1:-https://filesamples.com/samples/video/mp4/sample_640x360.mp4}"
capture_role="${CAPTURE_ROLE:-raw}"
stream_fps="${STREAM_FPS:-10}"
sensor_fps="${SENSOR_FPS:-}"
record_seconds="${RECORD_SECONDS:-5}"
capture_timeout_seconds="${CAPTURE_TIMEOUT_SECONDS:-$((record_seconds * 4 + 20))}"
local_output_dir="${LOCAL_OUTPUT_DIR:-${repo_root}/.cache/remote-captures}"
timestamp="$(date +%Y%m%d-%H%M%S)"
local_output_path="${LOCAL_OUTPUT_PATH:-${local_output_dir}/sensorium-record-${timestamp}.mp4}"
remote_raw_path="/tmp/sensorium-record.raw"
remote_mp4_path="/tmp/sensorium-record.mp4"
remote_log_path="/tmp/sensorium-record.log"

# shellcheck disable=SC1091
source "${script_dir}/remote-common.sh"

if [[ -n "${STREAM_WIDTH:-}" && -n "${STREAM_HEIGHT:-}" ]]; then
	stream_width="${STREAM_WIDTH}"
	stream_height="${STREAM_HEIGHT}"
elif [[ "${capture_role}" == "raw" ]]; then
	read -r stream_width stream_height < <(sensorium_default_raw_size)
else
	read -r stream_width stream_height < <(sensorium_default_processed_size)
fi

record_frames="${RECORD_FRAMES:-$((stream_fps * record_seconds))}"

case "${capture_role}" in
raw)
	inject_width="${INJECT_WIDTH:-${stream_width}}"
	inject_height="${INJECT_HEIGHT:-${stream_height}}"
	ffmpeg_input_pix_fmt="${FFMPEG_INPUT_PIX_FMT:-bayer_rggb16le}"
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
	ffmpeg_input_pix_fmt="${FFMPEG_INPUT_PIX_FMT:-rgba}"
	;;
*)
	echo "Unsupported CAPTURE_ROLE: ${capture_role}" >&2
	exit 2
	;;
esac

mkdir -p "${local_output_dir}"

STREAM_WIDTH="${inject_width}" STREAM_HEIGHT="${inject_height}" \
	STREAM_FPS="${stream_fps}" \
	"${script_dir}/remote-start-url-stream.sh" "${source_url}"

cleanup() {
	"${script_dir}/remote-stop-url-stream.sh" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Waiting for the remote injector to warm up..."
sleep 3

"${script_dir}/remote-build-libcamera-capture.sh" >/dev/null

remote_install_ipa_config
remote_ssh_retry "rm -f '${remote_raw_path}' '${remote_mp4_path}' '${remote_log_path}'"

fps_arg=()
if [[ -n "${sensor_fps}" ]]; then
	fps_arg=(--fps "${sensor_fps}")
fi

remote_ssh "cd '${remote_repo_dir}' && source ./scripts/sensorium-common.sh && sensorium_export_libcamera_runtime && timeout ${capture_timeout_seconds} ./tools/libcamera-record --role ${capture_role} --width ${stream_width} --height ${stream_height} --frames ${record_frames} ${fps_arg[*]} --timeout-ms $((capture_timeout_seconds * 1000)) --output ${remote_raw_path} > ${remote_log_path} 2>&1"

echo
echo "Remote recording log:"
remote_ssh_retry "tail -n 120 '${remote_log_path}'"

encode_fps="${stream_fps}"
if [[ "${capture_role}" == "raw" && -n "${sensor_fps}" ]]; then
	encode_fps="${sensor_fps}"
fi

remote_ssh_retry "ffmpeg -hide_banner -loglevel warning -y -f rawvideo -pixel_format ${ffmpeg_input_pix_fmt} -video_size ${stream_width}x${stream_height} -framerate ${encode_fps} -i '${remote_raw_path}' -an -c:v libx264 -preset veryfast -pix_fmt yuv420p '${remote_mp4_path}'"

echo
echo "Remote recorded files:"
remote_ssh_retry "ls -l '${remote_raw_path}' '${remote_mp4_path}'"

remote_rsync_from_retry "${remote_mp4_path}" "${local_output_path}"

echo
echo "Downloaded recorded MP4 to ${local_output_path}"
