#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
lines="${1:-200}"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

remote_ssh_retry "journalctl -k -n '${lines}' --no-pager || dmesg | tail -n '${lines}'"
