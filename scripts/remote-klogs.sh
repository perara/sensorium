#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
lines="${1:-200}"

# shellcheck disable=SC1091
source "${script_dir}/remote-common.sh"

remote_ssh_retry "journalctl -k -n '${lines}' --no-pager || dmesg | tail -n '${lines}'"
