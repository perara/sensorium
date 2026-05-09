#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

remote_model_path="${1:-models/runtime/rpi-multibus.yaml}"

echo "Running remote runtime smoke test on ${remote_target}"

if [[ "${SKIP_SYNC:-0}" != "1" ]]; then
	remote_rsync_to_retry >/dev/null
fi

remote_ssh_retry "cd '${remote_repo_dir}' && ./scripts/local/smoke-runtime-model.sh '${remote_repo_dir}/${remote_model_path}'"

echo
echo "Remote runtime smoke test complete."
