#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/remote-common.sh"

if [[ "${SKIP_SYNC:-0}" != "1" ]]; then
	"${script_dir}/remote-sync.sh"
fi

remote_ssh_retry "cd '${remote_repo_dir}' && ./scripts/build-libcamera-capture.sh"

