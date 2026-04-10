#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
record_seconds="${RECORD_SECONDS:-2}"

# shellcheck disable=SC1091
source "${script_dir}/remote-common.sh"

target_name="${1:-${sensorium_sensor}}"

run_step() {
	local label="$1"
	shift

	echo
	echo "==> ${label}"
	if "$@"; then
		return 0
	fi

	echo
	echo "Step failed: ${label}" >&2
	echo "Recent remote kernel log:" >&2
	"${script_dir}/remote-klogs.sh" 160 || true
	exit 1
}

run_step "Reload and verify detection" \
	"${script_dir}/remote-cycle.sh" "${target_name}"

run_step "Raw single-frame smoke capture" \
	env CAPTURE_ROLE=raw "${script_dir}/remote-smoke-url-stream.sh"

run_step "Processed single-frame smoke capture" \
	env CAPTURE_ROLE=viewfinder "${script_dir}/remote-smoke-url-stream.sh"

run_step "Short raw MP4 record" \
	env CAPTURE_ROLE=raw RECORD_SECONDS="${record_seconds}" \
	"${script_dir}/remote-record-url-video.sh"

echo
echo "Remote regression complete."
