#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/remote-common.sh"

target_name="${1:-${sensorium_sensor}}"

if ! "${script_dir}/remote-reload.sh"; then
	echo
	echo "Remote reload failed. Recent remote kernel log:"
	"${script_dir}/remote-klogs.sh" 160 || true
	exit 1
fi

if ! "${script_dir}/remote-verify.sh" "${target_name}"; then
	echo
	echo "Remote verification failed. Recent remote kernel log:"
	"${script_dir}/remote-klogs.sh" 160 || true
	exit 1
fi

echo
echo "Remote cycle complete."
