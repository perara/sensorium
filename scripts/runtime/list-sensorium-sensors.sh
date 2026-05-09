#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
catalog="${repo_root}/kernel/sensorium-family-imx.h"

awk '
	match($0, /SENSORIUM_IMX_PROFILE_ENTRY\("([^"]+)"/, m) {
		print m[1]
	}
' "${catalog}"
