#!/usr/bin/env bash
set -euo pipefail

source_ref="${1:-https://filesamples.com/samples/video/mp4/sample_640x360.mp4}"
width="${STREAM_WIDTH:-1536}"
height="${STREAM_HEIGHT:-864}"
fps="${STREAM_FPS:-10}"
inject_device="${INJECT_DEVICE:-/dev/video0}"
cache_dir="${STREAM_CACHE_DIR:-$(pwd)/.cache/media}"

need_cmd() {
	local cmd="$1"
	if ! command -v "${cmd}" >/dev/null 2>&1; then
		echo "Missing required command: ${cmd}" >&2
		exit 1
	fi
}

need_cmd ffmpeg
need_cmd v4l2-ctl
mkdir -p "${cache_dir}"

source_name="$(basename "${source_ref%%\?*}")"
if [[ -z "${source_name}" || "${source_name}" == "/" ]]; then
	source_name="stream-source.mp4"
fi

source_path=""

if [[ -f "${source_ref}" ]]; then
	source_path="${source_ref}"
elif [[ "${source_ref}" =~ ^https?:// ]]; then
	need_cmd curl
	source_path="${cache_dir}/${source_name}"

	if [[ ! -s "${source_path}" ]]; then
		echo "Downloading source video to ${source_path}"
		curl --fail --location --silent --show-error \
			--output "${source_path}.part" "${source_ref}"
		mv "${source_path}.part" "${source_path}"
	fi
else
	echo "Input must be an existing local file or an http(s) URL: ${source_ref}" >&2
	exit 2
fi

if [[ ! -s "${source_path}" ]]; then
	echo "Input file is empty or missing: ${source_path}" >&2
	exit 2
fi

echo "Streaming ${source_path} into ${inject_device} at ${width}x${height} ${fps}fps"
echo "Press Ctrl+C to stop."

ffmpeg \
	-hide_banner \
	-loglevel warning \
	-stream_loop -1 \
	-re \
	-i "${source_path}" \
	-an -sn -dn \
	-vf "fps=${fps},scale=${width}:${height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2:black,format=bgr0" \
	-pix_fmt bgr0 \
	-f rawvideo - | \
v4l2-ctl \
	-d "${inject_device}" \
	--set-fmt-video-out="width=${width},height=${height},pixelformat=BGR4" \
	--stream-out-mmap=4 \
	--stream-from=-
