#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_url="${1:-https://newbringer.ams3.cdn.digitaloceanspaces.com/csgo-stream.mp4}"
fps_list="${FPS_LIST:-10 20 30}"

for fps in ${fps_list}; do
	echo
	echo "==> Benchmarking ${fps} fps"
	env SENSOR_FPS="${fps}" STREAM_FPS="${fps}" \
		"${script_dir}/remote-benchmark.sh" "${source_url}"
done
