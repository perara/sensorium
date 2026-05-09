#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
min_version="${LIBCAMERA_MIN_VERSION:-0.4.0}"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

remote_ssh_retry bash -s -- "${min_version}" <<'EOF'
set -euo pipefail

min_version="$1"
installed_version="$(dpkg-query -W -f='${Version}\n' libcamera-tools 2>/dev/null | head -n 1 || true)"

if [[ -z "${installed_version}" ]]; then
	echo "libcamera-tools is not installed on the remote host." >&2
	exit 1
fi

echo "Remote libcamera-tools version: ${installed_version}"

if ! dpkg --compare-versions "${installed_version}" ge "${min_version}"; then
	echo "libcamera-tools ${installed_version} is older than required minimum ${min_version}." >&2
	exit 1
fi
EOF
