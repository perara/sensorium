#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
source_url="${1:-https://filesamples.com/samples/video/mp4/sample_640x360.mp4}"
capture_timeout="${CAPTURE_TIMEOUT_SECONDS:-20}"
capture_role="${CAPTURE_ROLE:-viewfinder}"
capture_extension="${CAPTURE_EXTENSION:-bin}"
libcamera_client="${LIBCAMERA_CLIENT:-tool}"
url_stream_id="${URL_STREAM_ID:-smoke-$$-$(date +%s)}"
capture_prefix="/tmp/sensorium-stream-${url_stream_id}"
capture_log="/tmp/sensorium-cam-stream-${url_stream_id}.log"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

if [[ -n "${STREAM_WIDTH:-}" && -n "${STREAM_HEIGHT:-}" ]]; then
	stream_width="${STREAM_WIDTH}"
	stream_height="${STREAM_HEIGHT}"
elif [[ "${capture_role}" == "raw" ]]; then
	read -r stream_width stream_height < <(sensorium_default_raw_size)
else
	read -r stream_width stream_height < <(sensorium_default_processed_size)
fi

SKIP_SYNC=1 "${script_dir}/remote-reload.sh" >/dev/null

case "${capture_role}" in
raw)
	inject_width="${INJECT_WIDTH:-${stream_width}}"
	inject_height="${INJECT_HEIGHT:-${stream_height}}"
	stream_pixel_format="RG10"
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

STREAM_WIDTH="${inject_width}" STREAM_HEIGHT="${inject_height}" STREAM_PIXEL_FORMAT="${stream_pixel_format}" URL_STREAM_ID="${url_stream_id}" \
	"${script_dir}/remote-start-url-stream.sh" "${source_url}"

dump_failure_context() {
	echo
	echo "Remote libcamera runtime context:"
	remote_ssh_retry bash -s <<EOF
set -euo pipefail
cd '${remote_repo_dir}'
source ./scripts/lib/sensorium-common.sh
sensorium_export_libcamera_runtime
printf 'LIBCAMERA_PREFIX=%s\n' "\${sensorium_libcamera_prefix}"
printf 'LIBCAMERA_BINDIR=%s\n' "\${sensorium_libcamera_bindir}"
printf 'LIBCAMERA_IPA_DIR=%s\n' "\${sensorium_libcamera_ipa_dir}"
printf 'LIBCAMERA_IPA_CONFIG_PATH=%s\n' "\${LIBCAMERA_IPA_CONFIG_PATH:-}"
if [[ -x "\${sensorium_libcamera_bindir}/cam" ]]; then
	printf 'Preferred cam binary: %s\n' "\${sensorium_libcamera_bindir}/cam"
	"\${sensorium_libcamera_bindir}/cam" --version || true
elif command -v cam >/dev/null 2>&1; then
	printf 'Fallback cam binary: %s\n' "\$(command -v cam)"
	cam --version || true
else
	echo "cam binary not found on remote PATH"
fi
ls -ld /dev/dma_heap /dev/dma_heap/system 2>/dev/null || true
if [[ -f "\${sensorium_libcamera_ipa_dir}/${sensorium_sensor}.yaml" ]]; then
	ls -l "\${sensorium_libcamera_ipa_dir}/${sensorium_sensor}.yaml"
else
	echo "Missing libcamera tuning file: \${sensorium_libcamera_ipa_dir}/${sensorium_sensor}.yaml"
fi
EOF

	echo
	echo "Recent remote kernel log:"
	"${script_dir}/remote-klogs.sh" 120 || true
}

cleanup() {
	URL_STREAM_ID="${url_stream_id}" "${script_dir}/remote-stop-url-stream.sh" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Waiting for the remote injector to warm up..."
sleep 3

remote_install_ipa_config

printf -v capture_prefix_q "%q" "${capture_prefix}"
printf -v capture_log_q "%q" "${capture_log}"

remote_ssh_retry bash -s <<EOF
set -euo pipefail
while read -r pid; do
	[[ -n "\${pid}" ]] || continue
	kill "\${pid}" 2>/dev/null || true
done < <(
	ps -eo pid=,args= | awk -v prefix=${capture_prefix_q} -v log_path=${capture_log_q} '
		index(\$0, prefix) || index(\$0, log_path) {
			print \$1
		}
	'
)
EOF
remote_ssh_retry "rm -f ${capture_prefix_q}-*.${capture_extension} ${capture_log_q}"
set +e
if [[ "${libcamera_client}" == "tool" ]]; then
	remote_ssh "cd '${remote_repo_dir}' && source ./scripts/lib/sensorium-common.sh && sensorium_export_libcamera_runtime && ./tools/libcamera-capture --role ${capture_role} --width ${stream_width} --height ${stream_height} --timeout-ms $((capture_timeout * 1000)) --output ${capture_prefix_q}-0.${capture_extension} > ${capture_log_q} 2>&1"
else
	remote_ssh "cd '${remote_repo_dir}' && source ./scripts/lib/sensorium-common.sh && sensorium_export_libcamera_runtime && \"${sensorium_libcamera_bindir}/cam\" -c1 -C1 -s role=${capture_role},width=${stream_width},height=${stream_height} -F ${capture_prefix_q}-#.${capture_extension} > ${capture_log_q} 2>&1"
fi
capture_rc=$?
set -e

echo
echo "Remote libcamera capture log:"
remote_ssh_retry "tail -n 80 ${capture_log_q}"

echo
echo "Remote captured files:"
remote_ssh_retry "ls -l ${capture_prefix_q}-*.${capture_extension} 2>/dev/null || true"

if (( capture_rc != 0 )); then
	dump_failure_context
fi

exit "${capture_rc}"
