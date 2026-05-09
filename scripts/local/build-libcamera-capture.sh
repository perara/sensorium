#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib" && pwd)/sensorium-common.sh"

sensorium_require_clean_env
mkdir -p "${sensorium_repo_root}/tools"
build_state_dir="${sensorium_repo_root}/.cache/tool-build"
helper_fingerprint_stamp="${build_state_dir}/helpers-fingerprint"
helper_in_progress_stamp="${build_state_dir}/helpers-build-in-progress"

sensorium_setup_libcamera_build_env

compute_helper_fingerprint() {
	local gpp_version
	local libcamera_version
	local libcamera_flags

	gpp_version="$(g++ --version | head -n 1)"
	libcamera_version="$(pkg-config --modversion libcamera)"
	libcamera_flags="$(pkg-config --cflags --libs libcamera)"

	{
		sha256sum \
			"${sensorium_repo_root}/scripts/local/build-libcamera-capture.sh" \
			"${sensorium_repo_root}/tools/libcamera-capture.cpp" \
			"${sensorium_repo_root}/tools/libcamera-record.cpp" \
			"${sensorium_repo_root}/tools/rgb24-to-rggb10.cpp"
		printf '%s\n' "${gpp_version}"
		printf '%s\n' "${libcamera_version}"
		printf '%s\n' "${libcamera_flags}"
		printf '%s\n' "${sensorium_libcamera_libdir}"
	} | sha256sum | awk '{print $1}'
}

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

mkdir -p "${build_state_dir}"
current_helper_fingerprint="$(compute_helper_fingerprint)"
if [[ -f "${helper_in_progress_stamp}" ]]; then
	echo "Previous helper build did not finish cleanly; rebuilding helper tools" >&2
fi
if [[ -f "${helper_fingerprint_stamp}" ]] && \
   [[ ! -f "${helper_in_progress_stamp}" ]] && \
   [[ "$(<"${helper_fingerprint_stamp}")" == "${current_helper_fingerprint}" ]] && \
   [[ -x "${sensorium_repo_root}/tools/libcamera-capture" ]] && \
   [[ -x "${sensorium_repo_root}/tools/libcamera-record" ]] && \
   [[ -x "${sensorium_repo_root}/tools/rgb24-to-rggb10" ]]; then
	echo "Helper build inputs unchanged; reusing existing helper tools"
	exit 0
fi

: >"${helper_in_progress_stamp}"
build_tool "${sensorium_repo_root}/tools/libcamera-capture.cpp" \
	"${sensorium_repo_root}/tools/libcamera-capture"
build_tool "${sensorium_repo_root}/tools/libcamera-record.cpp" \
	"${sensorium_repo_root}/tools/libcamera-record"
build_plain_tool "${sensorium_repo_root}/tools/rgb24-to-rggb10.cpp" \
	"${sensorium_repo_root}/tools/rgb24-to-rggb10"
printf '%s\n' "${current_helper_fingerprint}" >"${helper_fingerprint_stamp}"
rm -f "${helper_in_progress_stamp}"

echo "Built ${sensorium_repo_root}/tools/libcamera-capture"
echo "Built ${sensorium_repo_root}/tools/libcamera-record"
echo "Built ${sensorium_repo_root}/tools/rgb24-to-rggb10"
