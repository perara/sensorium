#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

remote_model_path="${1:-models/runtime/rpi-managed-workers.yaml}"

echo "Running remote managed-worker runtime smoke test on ${remote_target}"

if [[ "${SKIP_SYNC:-0}" != "1" ]]; then
	remote_rsync_to_retry >/dev/null
fi

remote_ssh_retry "cd '${remote_repo_dir}' && ./scripts/local/smoke-runtime-managed-workers.sh '${remote_repo_dir}/${remote_model_path}'"

echo
echo "Remote managed-worker runtime smoke test complete."
