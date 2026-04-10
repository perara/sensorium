#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target_fps="${1:-${SENSOR_FPS:-}}"

# shellcheck disable=SC1091
source "${script_dir}/remote-common.sh"

if [[ -n "${2:-}" && -n "${3:-}" ]]; then
	sensor_width="${2}"
	sensor_height="${3}"
elif [[ -n "${SENSOR_WIDTH:-}" && -n "${SENSOR_HEIGHT:-}" ]]; then
	sensor_width="${SENSOR_WIDTH}"
	sensor_height="${SENSOR_HEIGHT}"
else
	read -r sensor_width sensor_height < <(sensorium_default_raw_size)
fi

if [[ -z "${target_fps}" ]]; then
	echo "Usage: remote-set-sensor-fps.sh <fps> [width height]" >&2
	exit 2
fi

printf -v target_fps_q "%q" "${target_fps}"
printf -v sensor_width_q "%q" "${sensor_width}"
printf -v sensor_height_q "%q" "${sensor_height}"

remote_ssh_retry bash -s <<EOF
set -euo pipefail

fps=${target_fps_q}
width=${sensor_width_q}
height=${sensor_height_q}
ctrls=\$(v4l2-ctl -d /dev/v4l-subdev0 --get-ctrl=pixel_rate,horizontal_blanking)
pixel_rate=\$(awk -F': ' '/pixel_rate/ {print \$2}' <<<"\${ctrls}")
hblank=\$(awk -F': ' '/horizontal_blanking/ {print \$2}' <<<"\${ctrls}")

if [[ -z "\${pixel_rate}" || -z "\${hblank}" ]]; then
	echo "Failed to read pixel_rate or horizontal_blanking from /dev/v4l-subdev0" >&2
	exit 1
fi

vblank=\$(awk -v pr="\${pixel_rate}" -v width="\${width}" -v hb="\${hblank}" -v height="\${height}" -v fps="\${fps}" 'BEGIN {
	if (fps <= 0)
		exit 1;
	raw = (pr / ((width + hb) * fps)) - height;
	if (raw < 1)
		raw = 1;
	printf "%d", raw + 0.5;
}')

v4l2-ctl -d /dev/v4l-subdev0 --set-ctrl vertical_blanking="\${vblank}" >/dev/null
actual_fps=\$(awk -v pr="\${pixel_rate}" -v width="\${width}" -v hb="\${hblank}" -v height="\${height}" -v vb="\${vblank}" 'BEGIN {
	printf "%.2f", pr / ((width + hb) * (height + vb));
}')

cat <<METRICS
sensor_target_fps=\${fps}
sensor_width=\${width}
sensor_height=\${height}
vertical_blanking=\${vblank}
sensor_actual_fps=\${actual_fps}
METRICS
EOF
