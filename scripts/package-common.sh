#!/usr/bin/env bash
set -euo pipefail

sensorium_package_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sensorium_repo_root="$(cd "${sensorium_package_script_dir}/.." && pwd)"
sensorium_version="$(tr -d ' \n' < "${sensorium_repo_root}/VERSION")"
sensorium_pkgname="sensorium-dkms"

sensorium_stage_tree() {
	local dest="$1"

	mkdir -p "${dest}"
	rsync -a \
		--exclude '.git' \
		--exclude '.github' \
		--exclude '.cache' \
		--exclude 'dist' \
		--exclude '.env.remote' \
		--exclude '.env.kernel' \
		--exclude 'tools/libcamera-capture' \
		--exclude 'tools/libcamera-record' \
		--exclude 'tools/rgb24-to-rggb10' \
		"${sensorium_repo_root}/" "${dest}/"
}

sensorium_render_template() {
	local src="$1"
	local dest="$2"

	sed "s/@VERSION@/${sensorium_version}/g" "${src}" > "${dest}"
}

sensorium_stage_dkms_source() {
	local dest="$1"

	mkdir -p "${dest}"
	rsync -a \
		"${sensorium_repo_root}/Makefile" \
		"${sensorium_repo_root}/VERSION" \
		"${sensorium_repo_root}/LICENSE" \
		"${sensorium_repo_root}/README.md" \
		"${sensorium_repo_root}/kernel" \
		"${dest}/"
	sensorium_render_template \
		"${sensorium_repo_root}/packaging/dkms/dkms.conf" \
		"${dest}/dkms.conf"
}
