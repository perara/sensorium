#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
source_ref="${1:-https://filesamples.com/samples/video/mp4/sample_640x360.mp4}"
stream_width="${STREAM_WIDTH:-1536}"
stream_height="${STREAM_HEIGHT:-864}"
stream_fps="${STREAM_FPS:-10}"
stream_pixel_format="${STREAM_PIXEL_FORMAT:-BGR4}"
auto_install_stream_deps="${REMOTE_AUTO_INSTALL_STREAM_DEPS:-0}"
url_stream_id_raw="${URL_STREAM_ID:-default}"
url_stream_id="$(printf '%s' "${url_stream_id_raw}" | tr -c 'A-Za-z0-9._-' '-')"
stream_pid_file=".cache/url-stream-${url_stream_id}.pid"
stream_log_file=".cache/url-stream-${url_stream_id}.log"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

remote_source_ref="${source_ref}"
required_remote_cmds=(ffmpeg v4l2-ctl)
if [[ -f "${source_ref}" ]]; then
	remote_ssh_retry "mkdir -p '${remote_repo_dir}/.cache/uploaded-media'"
	remote_source_ref="${remote_repo_dir}/.cache/uploaded-media/$(basename "${source_ref}")"
	remote_rsync_from_args=()
	rsync -az \
		-e "ssh -p ${remote_port} ${remote_ssh_opts[*]}" \
		"${source_ref}" "${remote_target}:${remote_source_ref}"
elif [[ "${remote_source_ref}" =~ ^https?:// ]]; then
	required_remote_cmds+=(curl)
fi

"${script_dir}/remote-sync.sh" >/dev/null
remote_ssh_retry "cd '${remote_repo_dir}' && ./scripts/local/build-libcamera-capture.sh >/dev/null"

printf -v source_ref_q "%q" "${remote_source_ref}"
printf -v stream_width_q "%q" "${stream_width}"
printf -v stream_height_q "%q" "${stream_height}"
printf -v stream_fps_q "%q" "${stream_fps}"
printf -v stream_pixel_format_q "%q" "${stream_pixel_format}"
printf -v required_remote_cmds_q "%q " "${required_remote_cmds[@]}"

check_remote_stream_cmds() {
remote_ssh bash -s <<EOF
set -euo pipefail
missing=()
for cmd in ${required_remote_cmds_q}; do
	if ! command -v "\${cmd}" >/dev/null 2>&1; then
		missing+=("\${cmd}")
	fi
done
if (( \${#missing[@]} != 0 )); then
	printf 'Missing required remote command(s): %s\n' "\${missing[*]}" >&2
	echo "Remote URL-stream workflows require ffmpeg, v4l2-ctl, and curl for URL sources." >&2
	exit 2
fi
EOF
}

if ! check_remote_stream_cmds; then
	if [[ "${auto_install_stream_deps}" == "1" ]]; then
		bash "${script_dir}/remote-ensure-stream-deps.sh"
		check_remote_stream_cmds
	else
		echo "Run ./scripts/remote/remote-ensure-stream-deps.sh or set REMOTE_AUTO_INSTALL_STREAM_DEPS=1." >&2
		exit 2
	fi
fi

remote_ssh_retry bash -s <<EOF
set -euo pipefail
cd '${remote_repo_dir}'
mkdir -p .cache
stop_url_stream() {
	local stream_pid=""
	local stream_pgid=""

	if [[ ! -f '${stream_pid_file}' ]]; then
		return 0
	fi

	stream_pid="\$(cat '${stream_pid_file}' 2>/dev/null || true)"
	if [[ -n "\${stream_pid}" ]]; then
		stream_pgid="\$(ps -o pgid= -p "\${stream_pid}" 2>/dev/null | tr -d ' ' || true)"
		if [[ -n "\${stream_pgid}" ]]; then
			kill -- "-\${stream_pgid}" 2>/dev/null || kill "\${stream_pid}" 2>/dev/null || true
		else
			kill "\${stream_pid}" 2>/dev/null || true
		fi
		for _ in \$(seq 1 30); do
			if ! kill -0 "\${stream_pid}" 2>/dev/null; then
				break
			fi
			sleep 0.1
		done
		if kill -0 "\${stream_pid}" 2>/dev/null; then
			if [[ -n "\${stream_pgid}" ]]; then
				kill -KILL -- "-\${stream_pgid}" 2>/dev/null || true
			fi
			kill -KILL "\${stream_pid}" 2>/dev/null || true
		fi
	fi

	rm -f '${stream_pid_file}'
}

stop_url_stream
rm -f '${stream_log_file}'
nohup setsid env STREAM_WIDTH=${stream_width_q} STREAM_HEIGHT=${stream_height_q} STREAM_FPS=${stream_fps_q} STREAM_PIXEL_FORMAT=${stream_pixel_format_q} \
	bash ./scripts/runtime/stream-url-to-sensorium.sh ${source_ref_q} > '${stream_log_file}' 2>&1 < /dev/null &
echo \$! > '${stream_pid_file}'
for _ in \$(seq 1 40); do
	if [[ -s '${stream_pid_file}' ]] && kill -0 "\$(cat '${stream_pid_file}')" 2>/dev/null; then
		sleep 0.2
		if kill -0 "\$(cat '${stream_pid_file}')" 2>/dev/null; then
			exit 0
		fi
	fi
	sleep 0.2
done
echo "Remote stream process did not come up cleanly" >&2
tail -n 40 '${stream_log_file}' 2>/dev/null || true
exit 1
EOF

echo "Remote file-backed stream started on ${remote_target}"
echo "Log: ${remote_repo_dir}/${stream_log_file}"
remote_ssh_retry "cd '${remote_repo_dir}' && cat '${stream_pid_file}'"
