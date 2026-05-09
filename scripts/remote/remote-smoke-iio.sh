#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

remote_model_path="${1:-models/iio/environment-i2c.yaml}"

echo "Running remote IIO smoke test on ${remote_target}"

SKIP_SYNC="${SKIP_SYNC:-0}" "${script_dir}/remote-apply-model.sh" "${remote_model_path}"
remote_ssh_retry "cd '${remote_repo_dir}' && IIO_SMOKE_SKIP_APPLY=1 ./scripts/local/smoke-iio-model.sh '${remote_repo_dir}/${remote_model_path}'"

echo
echo "Remote IIO smoke test complete."
