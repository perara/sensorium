#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

target_name="${1:-${sensorium_sensor}}"

echo "Checking remote camera contract on ${remote_target}"
remote_ssh_retry "cd '${remote_repo_dir}' && ./scripts/local/assert-camera-contract.sh '${target_name}'"

echo
echo "Remote camera contract check complete."
