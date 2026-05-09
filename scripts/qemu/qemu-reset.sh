#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/qemu-common.sh"

"${script_dir}/qemu-stop.sh" >/dev/null 2>&1 || true
qemu_prepare_assets
qemu_reset_overlay
qemu_write_cloud_init

echo "QEMU guest overlay reset."
