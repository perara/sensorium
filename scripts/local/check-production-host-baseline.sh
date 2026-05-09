#!/usr/bin/env bash
set -euo pipefail

profile="runtime"
strict=0

while [[ $# -gt 0 ]]; do
	case "$1" in
	--profile)
		profile="${2:-}"
		shift 2
		;;
	--strict)
		strict=1
		shift
		;;
	-h|--help)
		cat <<'EOF'
Usage: check-production-host-baseline.sh [--profile driver|runtime|full|ops] [--strict]
EOF
		exit 0
		;;
	*)
		echo "Unknown argument: $1" >&2
		exit 1
		;;
	esac
done

case "${profile}" in
driver|runtime|full|ops)
	;;
*)
	echo "Unsupported profile: ${profile}" >&2
	exit 2
	;;
esac

need_cmd() {
	command -v "$1" >/dev/null 2>&1
}

failures=()
warnings=()

check_cmds() {
	local missing=()
	local cmd
	for cmd in "$@"; do
		if ! need_cmd "${cmd}"; then
			missing+=( "${cmd}" )
		fi
	done
	if (( ${#missing[@]} != 0 )); then
		failures+=( "missing commands: ${missing[*]}" )
	fi
}

check_cmds gcc g++ make pkg-config

if [[ ! -e /lib/modules/"$(uname -r)"/build && -z "${KDIR:-}" ]]; then
	warnings+=( "no matching kernel build tree detected; set KDIR for module builds" )
fi

case "${profile}" in
runtime|full|ops)
	check_cmds python3
	python3 - <<'PY' >/dev/null 2>&1 || failures+=( "missing Python runtime modules: yaml and/or serial" )
import serial  # noqa: F401
import yaml  # noqa: F401
PY
	;;
esac

case "${profile}" in
full|ops)
	check_cmds cam v4l2-ctl ffmpeg curl i2cdetect media-ctl
	;;
esac

case "${profile}" in
ops)
	check_cmds ansible-playbook ssh rsync qemu-system-x86_64 qemu-img cloud-localds
	;;
esac

state_dir="${SENSORIUM_STATE_DIR:-/var/lib/sensorium}"
if [[ ! -d "${state_dir}" ]]; then
	warnings+=( "state directory does not exist yet: ${state_dir}" )
fi

if command -v systemctl >/dev/null 2>&1; then
	if systemctl list-unit-files sensoriumd.service >/dev/null 2>&1; then
		:
	else
		warnings+=( "sensoriumd.service is not installed in systemd" )
	fi
else
	warnings+=( "systemctl not present; systemd service management unavailable" )
fi

echo "profile=${profile}"
echo "state_dir=${state_dir}"
echo "strict=${strict}"
for item in "${warnings[@]}"; do
	echo "warning=${item}"
done
for item in "${failures[@]}"; do
	echo "failure=${item}"
done

if (( ${#failures[@]} != 0 )); then
	exit 1
fi

if (( strict == 1 && ${#warnings[@]} != 0 )); then
	exit 1
fi
