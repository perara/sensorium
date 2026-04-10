#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${script_dir}/package-common.sh"

out_dir="${OUT_DIR:-${sensorium_repo_root}/dist/deb}"
root_dir="${out_dir}/${sensorium_pkgname}_${sensorium_version}_all"
pkg_path="${out_dir}/${sensorium_pkgname}_${sensorium_version}_all.deb"

rm -rf "${root_dir}" "${pkg_path}"
mkdir -p "${root_dir}/DEBIAN" "${root_dir}/usr/src" \
	"${root_dir}/usr/share/doc/${sensorium_pkgname}" \
	"${root_dir}/usr/share/sensorium"

sensorium_stage_dkms_source "${root_dir}/usr/src/sensorium-${sensorium_version}"
rsync -a \
	--exclude '.git' \
	--exclude '.github' \
	--exclude '.cache' \
	--exclude '.env.remote' \
	--exclude '.env.kernel' \
	--exclude 'tools/libcamera-capture' \
	--exclude 'tools/libcamera-record' \
	--exclude 'tools/rgb24-to-rggb10' \
	"${sensorium_repo_root}/config" \
	"${sensorium_repo_root}/docs" \
	"${sensorium_repo_root}/scripts" \
	"${sensorium_repo_root}/tools" \
	"${root_dir}/usr/share/sensorium/"
install -m 0644 "${sensorium_repo_root}/README.md" \
	"${root_dir}/usr/share/doc/${sensorium_pkgname}/README.md"
install -m 0644 "${sensorium_repo_root}/LICENSE" \
	"${root_dir}/usr/share/doc/${sensorium_pkgname}/LICENSE"

cat > "${root_dir}/DEBIAN/control" <<EOF
Package: ${sensorium_pkgname}
Version: ${sensorium_version}
Section: kernel
Priority: optional
Architecture: all
Maintainer: Sensorium contributors
Depends: dkms, make, gcc
Description: Virtual media-controller camera simulator DKMS package
 sensorium provides a virtual, sensor-shaped camera pipeline for Linux media
 controller and libcamera testing.
EOF

sensorium_render_template \
	"${sensorium_repo_root}/packaging/debian/postinst" \
	"${root_dir}/DEBIAN/postinst"
sensorium_render_template \
	"${sensorium_repo_root}/packaging/debian/prerm" \
	"${root_dir}/DEBIAN/prerm"
chmod 0755 "${root_dir}/DEBIAN/postinst" "${root_dir}/DEBIAN/prerm"

fakeroot dpkg-deb --build "${root_dir}" "${pkg_path}" >/dev/null
echo "${pkg_path}"
