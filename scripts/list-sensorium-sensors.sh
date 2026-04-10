#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
catalog="${repo_root}/kernel/sensorium-family-imx.h"

awk '
	match($0, /SENSORIUM_IMX_PROFILE_ENTRY\("([^"]+)"/, m) {
		print m[1]
	}
' "${catalog}"
