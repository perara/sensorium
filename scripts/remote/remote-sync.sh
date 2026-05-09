#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib" && pwd)/remote-common.sh"

remote_sync_prune_kernel_build="${REMOTE_SYNC_PRUNE_KERNEL_BUILD:-0}"
remote_sync_skip_if_current="${REMOTE_SYNC_SKIP_IF_CURRENT:-1}"
remote_sync_repair="${REMOTE_SYNC_REPAIR:-0}"

echo "Syncing repo to ${remote_target}:${remote_repo_dir}"
remote_ssh_retry "mkdir -p '${remote_repo_dir}'"
if [[ "${remote_sync_skip_if_current}" == "1" && "${remote_sync_repair}" != "1" ]]; then
	local_manifest="$(remote_sync_manifest_local)"
	remote_manifest="$(remote_sync_manifest_remote | tr -d '\r\n')"
	if [[ -n "${remote_manifest}" && "${remote_manifest}" == "${local_manifest}" ]]; then
		echo "Remote repo manifest unchanged; skipping sync."
		exit 0
	fi
fi
if [[ "${remote_sync_prune_kernel_build}" == "1" ]]; then
	remote_ssh_retry "find '${remote_repo_dir}/kernel' -maxdepth 1 \\( -name '*.o' -o -name '*.ko' -o -name '*.mod' -o -name '*.mod.c' -o -name '*.cmd' -o -name '.*.cmd' -o -name '.*.d' -o -name 'Module.symvers' -o -name 'modules.order' \\) -delete 2>/dev/null || true"
	remote_ssh_retry "rm -rf '${remote_repo_dir}/kernel/.tmp_versions' 2>/dev/null || true"
fi
if [[ "${remote_sync_repair}" == "1" ]]; then
	REMOTE_RSYNC_CHECKSUM=1 remote_rsync_to_retry
else
	remote_rsync_to_retry
fi
remote_write_sync_manifest "$(remote_sync_manifest_local)"
echo "Sync complete."
