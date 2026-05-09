#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

remote_model_path="${1:-models/camera/imx708.yaml}"

if [[ "${SKIP_SYNC:-0}" != "1" ]]; then
	"${script_dir}/remote-sync.sh"
fi

echo "Applying model ${remote_model_path} on ${remote_target}"
remote_ssh_retry "cd '${remote_repo_dir}' && ./scripts/runtime/sensoriumctl apply './${remote_model_path}'"

echo
echo "Remote model apply complete."
