#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"

# shellcheck disable=SC1091
source "${script_dir}/../lib/remote-common.sh"

expected_major="${1:-${REMOTE_EXPECT_KERNEL_MAJOR:-}}"

if [[ -z "${expected_major}" ]]; then
	echo "Usage: remote-assert-kernel-major.sh <major-version>" >&2
	exit 2
fi

case "${expected_major}" in
*[!0-9]*)
	echo "Kernel major version must be numeric: ${expected_major}" >&2
	exit 2
	;;
esac

remote_kernel="$(remote_ssh_retry_no_stdin "uname -r" | tr -d '\r' | head -n 1)"
remote_major="${remote_kernel%%.*}"

if [[ "${remote_major}" != "${expected_major}" ]]; then
	echo "Remote kernel major mismatch: expected ${expected_major}, got ${remote_kernel}" >&2
	exit 1
fi

echo "Remote kernel major ${expected_major} confirmed: ${remote_kernel}"
