#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_url="${1:-https://filesamples.com/samples/video/mp4/sample_640x360.mp4}"
capture_timeout="${CAPTURE_TIMEOUT_SECONDS:-20}"
capture_role="${CAPTURE_ROLE:-viewfinder}"
capture_extension="${CAPTURE_EXTENSION:-bin}"
libcamera_client="${LIBCAMERA_CLIENT:-tool}"

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

if [[ "${libcamera_client}" == "tool" ]]; then
	"${script_dir}/remote-build-libcamera-capture.sh" >/dev/null
fi

case "${capture_role}" in
raw)
	inject_width="${INJECT_WIDTH:-${stream_width}}"
	inject_height="${INJECT_HEIGHT:-${stream_height}}"
	;;
viewfinder|still|video)
	if [[ -n "${INJECT_WIDTH:-}" && -n "${INJECT_HEIGHT:-}" ]]; then
		inject_width="${INJECT_WIDTH}"
		inject_height="${INJECT_HEIGHT}"
	else
		read -r inject_width inject_height < <(sensorium_default_processed_inject_size)
	fi
	;;
*)
	echo "Unsupported CAPTURE_ROLE: ${capture_role}" >&2
	exit 2
	;;
esac

STREAM_WIDTH="${inject_width}" STREAM_HEIGHT="${inject_height}" \
	"${script_dir}/remote-start-url-stream.sh" "${source_url}"

cleanup() {
	"${script_dir}/remote-stop-url-stream.sh" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Waiting for the remote injector to warm up..."
sleep 3

remote_install_ipa_config

remote_ssh_retry bash -s <<EOF
set -euo pipefail
pkill -x cam 2>/dev/null || true
while read -r pid _; do
	kill "\${pid}" 2>/dev/null || true
done < <(ps -eo pid,args | grep "${remote_repo_dir}/tools/libcamera-capture" | grep -v grep || true)
EOF
remote_ssh_retry "rm -f /tmp/sensorium-stream-*.${capture_extension} /tmp/sensorium-cam-stream.log"
set +e
if [[ "${libcamera_client}" == "tool" ]]; then
	remote_ssh "cd '${remote_repo_dir}' && source ./scripts/sensorium-common.sh && sensorium_export_libcamera_runtime && ./tools/libcamera-capture --role ${capture_role} --width ${stream_width} --height ${stream_height} --timeout-ms $((capture_timeout * 1000)) --output /tmp/sensorium-stream-0.${capture_extension} > /tmp/sensorium-cam-stream.log 2>&1"
else
	remote_ssh "cd '${remote_repo_dir}' && source ./scripts/sensorium-common.sh && sensorium_export_libcamera_runtime && \"${sensorium_libcamera_bindir}/cam\" -c1 -C1 -s role=${capture_role},width=${stream_width},height=${stream_height} -F /tmp/sensorium-stream-#.${capture_extension} > /tmp/sensorium-cam-stream.log 2>&1"
fi
capture_rc=$?
set -e

echo
echo "Remote libcamera capture log:"
remote_ssh_retry "tail -n 80 /tmp/sensorium-cam-stream.log"

echo
echo "Remote captured files:"
remote_ssh_retry "ls -l /tmp/sensorium-stream-*.${capture_extension} 2>/dev/null || true"

exit "${capture_rc}"
