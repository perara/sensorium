#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

if [[ "${SKIP_SYNC:-0}" != "1" ]]; then
	"${script_dir}/remote-sync.sh"
fi

remote_ssh_retry "cd '${remote_repo_dir}' && ./scripts/local/build-libcamera-capture.sh"
