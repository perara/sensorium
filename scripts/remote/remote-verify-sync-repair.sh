#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

model_path="${1:-${REMOTE_VERIFY_SYNC_REPAIR_MODEL:-models/runtime/rpi-sparse-uart.yaml}}"
local_model_path="${model_path}"
remote_model_path="${model_path}"

if [[ "${local_model_path}" != /* ]]; then
	local_model_path="${sensorium_repo_root}/${local_model_path}"
fi

if [[ "${remote_model_path}" != /* ]]; then
	remote_model_path="${remote_repo_dir}/${remote_model_path}"
fi

if [[ ! -f "${local_model_path}" ]]; then
	echo "Missing local model for sync-repair verification: ${local_model_path}" >&2
	exit 1
fi

local_sha="$(sha256sum "${local_model_path}" | awk '{print $1}')"

echo "Verifying remote sync repair on ${remote_target}"
echo "Model: ${model_path}"

remote_ssh_retry "mkdir -p '${remote_repo_dir}'"
remote_rsync_to_retry >/dev/null

remote_sha_before="$(remote_ssh_retry "sha256sum '${remote_model_path}' | awk '{print \$1}'")"
if [[ "${remote_sha_before}" != "${local_sha}" ]]; then
	echo "Remote model checksum mismatch before corruption." >&2
	echo "local:  ${local_sha}" >&2
	echo "remote: ${remote_sha_before}" >&2
	exit 1
fi

echo "Corrupting remote copy to verify checksum-based repair"
remote_ssh_retry bash -s <<EOF
set -euo pipefail
python3 - '${remote_model_path}' <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
data = path.read_bytes()
path.write_bytes(b"\x00" * len(data))
print(len(data))
PY
EOF

remote_sha_corrupt="$(remote_ssh_retry "sha256sum '${remote_model_path}' | awk '{print \$1}'")"
if [[ "${remote_sha_corrupt}" == "${local_sha}" ]]; then
	echo "Remote corruption step did not change the model checksum." >&2
	exit 1
fi

REMOTE_SYNC_REPAIR=1 SKIP_SYNC=0 "${script_dir}/remote-smoke-runtime.sh" "${model_path}"

remote_sha_after="$(remote_ssh_retry "sha256sum '${remote_model_path}' | awk '{print \$1}'")"
if [[ "${remote_sha_after}" != "${local_sha}" ]]; then
	echo "Remote sync did not repair the corrupted model copy." >&2
	echo "local:  ${local_sha}" >&2
	echo "remote: ${remote_sha_after}" >&2
	exit 1
fi

echo
echo "Remote sync repair verification complete."
