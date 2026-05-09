#!/usr/bin/env bash
set -euo pipefail

sensorium_package_script_path="$(readlink -f "${BASH_SOURCE[0]}")"
sensorium_package_script_dir="$(cd "$(dirname "${sensorium_package_script_path}")" && pwd)"
sensorium_repo_root="$(cd "${sensorium_package_script_dir}/../.." && pwd)"
sensorium_version="$(tr -d ' \n' < "${sensorium_repo_root}/VERSION")"
sensorium_pkgname="sensorium-dkms"

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	echo "package-common.sh is an internal library; source it from package scripts." >&2
	exit 2
fi

sensorium_package_rsync_excludes=(
	--exclude '.git'
	--exclude '.github'
	--exclude '.cache'
	--exclude 'build'
	--exclude 'dist'
	--exclude '*.egg-info'
	--exclude '*.egg-info/'
	--exclude '.env.remote'
	--exclude '.env.kernel'
	--exclude 'compile_commands.json'
	--exclude '__pycache__'
	--exclude '__pycache__/'
	--exclude '*.o'
	--exclude '*.ko'
	--exclude '*.mod'
	--exclude '*.mod.c'
	--exclude '*.cmd'
	--exclude '*.pyc'
	--exclude '*.pyo'
	--exclude '*.swp'
	--exclude '*.swo'
	--exclude '.*.cmd'
	--exclude '.*.d'
	--exclude '.DS_Store'
	--exclude '.tmp_versions'
	--exclude '.tmp_versions/'
	--exclude 'Module.symvers'
	--exclude 'modules.order'
	--exclude 'Module.markers'
	--exclude 'tools/libcamera-capture'
	--exclude 'tools/libcamera-record'
	--exclude 'tools/rgb24-to-rggb10'
)

sensorium_stage_tree() {
	local dest="$1"

	mkdir -p "${dest}"
	rsync -a \
		"${sensorium_package_rsync_excludes[@]}" \
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
		"${sensorium_package_rsync_excludes[@]}" \
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
