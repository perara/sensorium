#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
# shellcheck disable=SC1091
source "${script_dir}/../lib/package-common.sh"

dist_dir="${DIST_DIR:-${sensorium_repo_root}/dist}"
stage_dir="${dist_dir}/sensorium-${sensorium_version}"
tarball="${dist_dir}/sensorium-${sensorium_version}.tar.gz"

rm -rf "${stage_dir}" "${tarball}"
mkdir -p "${dist_dir}"
sensorium_stage_tree "${stage_dir}"
tar -C "${dist_dir}" -czf "${tarball}" "sensorium-${sensorium_version}"

echo "${tarball}"
