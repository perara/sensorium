#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
original_args=( "$@" )
unit_target="/etc/systemd/system/sensoriumd.service"
env_target="/etc/default/sensoriumd"
enable_service=0
start_service=0

while [[ $# -gt 0 ]]; do
	case "$1" in
	--enable)
		enable_service=1
		shift
		;;
	--start)
		start_service=1
		enable_service=1
		shift
		;;
	-h|--help)
		cat <<'EOF'
Usage: install-systemd-service.sh [--enable] [--start]

Installs a source-checkout systemd unit for sensoriumd and creates
/etc/default/sensoriumd if it does not already exist.
EOF
		exit 0
		;;
	*)
		echo "Unknown argument: $1" >&2
		exit 1
		;;
	esac
done

if [[ "${EUID}" -ne 0 ]]; then
	exec sudo bash "$0" "${original_args[@]}"
fi

if ! command -v systemctl >/dev/null 2>&1; then
	echo "systemctl is required to install the sensoriumd service." >&2
	exit 1
fi

cat >"${unit_target}" <<EOF
[Unit]
Description=Sensorium runtime daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=SENSORIUM_STATE_DIR=/var/lib/sensorium
Environment=SENSORIUM_SOCKET_PATH=/run/sensorium/sensoriumd.sock
Environment=SENSORIUM_PIDFILE_PATH=/run/sensorium/sensoriumd.pid
EnvironmentFile=-/etc/default/sensoriumd
WorkingDirectory=${repo_root}
ExecStart=${repo_root}/scripts/runtime/sensoriumd --foreground --socket-path \${SENSORIUM_SOCKET_PATH} --pidfile \${SENSORIUM_PIDFILE_PATH}
Restart=on-failure
RestartSec=2
RuntimeDirectory=sensorium
StateDirectory=sensorium
LogsDirectory=sensorium

[Install]
WantedBy=multi-user.target
EOF

if [[ ! -e "${env_target}" ]]; then
	cat >"${env_target}" <<'EOF'
SENSORIUM_STATE_DIR=/var/lib/sensorium
# Optional overrides. The unit already defaults to these paths:
# SENSORIUM_SOCKET_PATH=/run/sensorium/sensoriumd.sock
# SENSORIUM_PIDFILE_PATH=/run/sensorium/sensoriumd.pid
EOF
fi

systemctl daemon-reload

if [[ "${enable_service}" == "1" ]]; then
	systemctl enable sensoriumd.service
fi

if [[ "${start_service}" == "1" ]]; then
	systemctl restart sensoriumd.service
fi

echo "Installed ${unit_target}"
echo "Environment file: ${env_target}"
