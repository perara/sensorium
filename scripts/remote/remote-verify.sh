#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"
target_name="${1:-${sensorium_sensor}}"

echo "Verifying libcamera detection on ${remote_target}"
remote_install_ipa_config
remote_ssh_retry "cd '${remote_repo_dir}' && ./scripts/local/verify-libcamera-detect.sh '${target_name}'"
