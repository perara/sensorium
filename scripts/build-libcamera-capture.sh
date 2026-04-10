#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sensorium-common.sh"

sensorium_require_clean_env
mkdir -p "${sensorium_repo_root}/tools"

sensorium_setup_libcamera_build_env

build_tool() {
	local source_path="$1"
	local output_path="$2"

	g++ -std=c++17 -O2 -Wall -Wextra \
		"-L${sensorium_libcamera_libdir}" \
		"-Wl,-rpath,${sensorium_libcamera_libdir}" \
		"${source_path}" \
		-o "${output_path}" \
		$(pkg-config --cflags --libs libcamera)
}

build_plain_tool() {
	local source_path="$1"
	local output_path="$2"

	g++ -std=c++17 -O2 -Wall -Wextra \
		"${source_path}" \
		-o "${output_path}"
}

build_tool "${sensorium_repo_root}/tools/libcamera-capture.cpp" \
	"${sensorium_repo_root}/tools/libcamera-capture"
build_tool "${sensorium_repo_root}/tools/libcamera-record.cpp" \
	"${sensorium_repo_root}/tools/libcamera-record"
build_plain_tool "${sensorium_repo_root}/tools/rgb24-to-rggb10.cpp" \
	"${sensorium_repo_root}/tools/rgb24-to-rggb10"

echo "Built ${sensorium_repo_root}/tools/libcamera-capture"
echo "Built ${sensorium_repo_root}/tools/libcamera-record"
echo "Built ${sensorium_repo_root}/tools/rgb24-to-rggb10"
