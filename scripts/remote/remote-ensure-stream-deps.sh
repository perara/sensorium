#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

packages=(ffmpeg curl v4l-utils)

printf -v packages_q "%q " "${packages[@]}"

remote_ssh_retry bash -s <<EOF
set -euo pipefail
if ! command -v apt-get >/dev/null 2>&1; then
	echo "Remote stream dependency bootstrap currently supports apt-get hosts only." >&2
	exit 2
fi
sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ${packages_q}
EOF

echo "Remote stream dependencies installed on ${remote_target}"
