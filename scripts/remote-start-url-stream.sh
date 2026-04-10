#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_url="${1:-https://newbringer.ams3.cdn.digitaloceanspaces.com/csgo-stream.mp4}"
stream_width="${STREAM_WIDTH:-1536}"
stream_height="${STREAM_HEIGHT:-864}"
stream_fps="${STREAM_FPS:-10}"

# shellcheck disable=SC1091
source "${script_dir}/remote-common.sh"

"${script_dir}/remote-sync.sh" >/dev/null
remote_ssh_retry "cd '${remote_repo_dir}' && ./scripts/build-libcamera-capture.sh >/dev/null"

printf -v source_url_q "%q" "${source_url}"
printf -v stream_width_q "%q" "${stream_width}"
printf -v stream_height_q "%q" "${stream_height}"
printf -v stream_fps_q "%q" "${stream_fps}"

remote_ssh_retry bash -s <<EOF
set -euo pipefail
cd '${remote_repo_dir}'
mkdir -p .cache
if [[ -f .cache/url-stream.pid ]]; then
	kill \$(cat .cache/url-stream.pid) 2>/dev/null || true
	rm -f .cache/url-stream.pid
fi
pkill -x ffmpeg || true
pkill -x v4l2-ctl || true
nohup env STREAM_WIDTH=${stream_width_q} STREAM_HEIGHT=${stream_height_q} STREAM_FPS=${stream_fps_q} bash ./scripts/stream-url-to-sensorium.sh ${source_url_q} > .cache/url-stream.log 2>&1 < /dev/null &
echo \$! > .cache/url-stream.pid
for _ in \$(seq 1 20); do
	if [[ -s .cache/url-stream.pid ]] && kill -0 "\$(cat .cache/url-stream.pid)" 2>/dev/null; then
		exit 0
	fi
	sleep 0.2
done
echo "Remote stream process did not come up cleanly" >&2
tail -n 40 .cache/url-stream.log 2>/dev/null || true
exit 1
EOF

echo "Remote file-backed stream started on ${remote_target}"
echo "Log: ${remote_repo_dir}/.cache/url-stream.log"
remote_ssh_retry "cd '${remote_repo_dir}' && cat .cache/url-stream.pid"
