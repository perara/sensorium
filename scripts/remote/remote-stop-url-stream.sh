#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
url_stream_id_raw="${URL_STREAM_ID:-default}"
url_stream_id="$(printf '%s' "${url_stream_id_raw}" | tr -c 'A-Za-z0-9._-' '-')"
stream_pid_file=".cache/url-stream-${url_stream_id}.pid"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

remote_ssh_retry bash -s <<EOF
set -euo pipefail
cd '${remote_repo_dir}'
if [[ -f '${stream_pid_file}' ]]; then
	stream_pid="\$(cat '${stream_pid_file}' 2>/dev/null || true)"
	stream_pgid=""
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
fi
EOF

echo "Remote file-backed stream stopped on ${remote_target}"
