#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${script_dir}/package-common.sh"

render_dir="${1:-${sensorium_repo_root}/dist/pkgmeta}"
rm -rf "${render_dir}"
mkdir -p "${render_dir}/arch" "${render_dir}/alpine"

sensorium_render_template \
	"${sensorium_repo_root}/packaging/arch/PKGBUILD" \
	"${render_dir}/arch/PKGBUILD"
sensorium_render_template \
	"${sensorium_repo_root}/packaging/arch/sensorium-dkms.install" \
	"${render_dir}/arch/sensorium-dkms.install"
sensorium_render_template \
	"${sensorium_repo_root}/packaging/alpine/APKBUILD" \
	"${render_dir}/alpine/APKBUILD"
sensorium_render_template \
	"${sensorium_repo_root}/packaging/alpine/sensorium-dkms.post-install" \
	"${render_dir}/alpine/sensorium-dkms.post-install"
sensorium_render_template \
	"${sensorium_repo_root}/packaging/alpine/sensorium-dkms.pre-deinstall" \
	"${render_dir}/alpine/sensorium-dkms.pre-deinstall"

echo "${render_dir}"
