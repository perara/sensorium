#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/remote-common.sh"

remote_ssh_retry bash -s <<EOF
set -euo pipefail
cd '${remote_repo_dir}'
if [[ -f .cache/url-stream.pid ]]; then
	kill \$(cat .cache/url-stream.pid) 2>/dev/null || true
	for _ in \$(seq 1 20); do
		if ! kill -0 \$(cat .cache/url-stream.pid) 2>/dev/null; then
			break
		fi
		sleep 0.1
	done
	rm -f .cache/url-stream.pid
fi
pkill -x ffmpeg || true
pkill -x v4l2-ctl || true
EOF

echo "Remote file-backed stream stopped on ${remote_target}"
