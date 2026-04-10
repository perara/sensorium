#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/remote-common.sh"

echo "Syncing repo to ${remote_target}:${remote_repo_dir}"
remote_ssh_retry "mkdir -p '${remote_repo_dir}'"
remote_rsync_to_retry
echo "Sync complete."
