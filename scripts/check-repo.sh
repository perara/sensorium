#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

cd "${repo_root}"

echo "Checking shell syntax..."
bash -n scripts/*.sh

if command -v shellcheck >/dev/null 2>&1; then
	echo "Running shellcheck..."
	shellcheck \
		-e SC2034 \
		-e SC2046 \
		-e SC2154 \
		-e SC2178 \
		-e SC2317 \
		scripts/*.sh
fi

echo "Checking for stale legacy names..."
if rg -n "imx708-sim|sony-imx-sim|camera-mock-drivers" . \
	--glob '!**/.git/**' \
	--glob '!dist/**' \
	--glob '!scripts/check-repo.sh'; then
	echo "Found stale legacy naming in the repo." >&2
	exit 1
fi

echo "Checking required top-level files..."
required_files=(
	README.md
	LICENSE
	VERSION
	CHANGELOG.md
	CONTRIBUTING.md
	SECURITY.md
	CODE_OF_CONDUCT.md
	.env.remote.example
	.gitignore
	.gitattributes
	.editorconfig
)

for path in "${required_files[@]}"; do
	if [[ ! -f "${path}" ]]; then
		echo "Missing required file: ${path}" >&2
		exit 1
	fi
done

echo "Checking for generated artifacts in the repo root..."
generated_paths=(
	.cache
	dist
	.env.kernel
	.env.remote
	tools/libcamera-capture
	tools/libcamera-record
	tools/rgb24-to-rggb10
)

for path in "${generated_paths[@]}"; do
	if [[ -e "${path}" ]]; then
		echo "Generated or local-only path should not be present: ${path}" >&2
		exit 1
	fi
done

echo "Repository checks passed."
