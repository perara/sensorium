#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sensorium-common.sh"

sensorium_require_clean_env
remote_env_file="${sensorium_repo_root}/.env.remote"

if [[ -f "${remote_env_file}" ]]; then
	# shellcheck disable=SC1090
	source "${remote_env_file}"
fi

remote_host="${REMOTE_HOST:-}"
remote_user="${REMOTE_USER:-root}"
remote_port="${REMOTE_PORT:-22}"
remote_repo_dir="${REMOTE_REPO_DIR:-/opt/sensorium}"
remote_retry_count="${REMOTE_RETRY_COUNT:-8}"
remote_retry_delay_seconds="${REMOTE_RETRY_DELAY_SECONDS:-5}"

if [[ -n "${REMOTE_SSH_OPTS:-}" ]]; then
	# shellcheck disable=SC2206
	remote_ssh_opts=( ${REMOTE_SSH_OPTS} )
else
	remote_ssh_opts=(
		-o StrictHostKeyChecking=accept-new
		-o ConnectTimeout=10
		-o ServerAliveInterval=5
		-o ServerAliveCountMax=6
	)
fi

if [[ -z "${remote_host}" ]]; then
	echo "REMOTE_HOST is not set." >&2
	echo "Copy .env.remote.example to .env.remote and set the droplet IP." >&2
	exit 1
fi

remote_target="${remote_user}@${remote_host}"

with_remote_retries() {
	local attempt=1

	while true; do
		if "$@"; then
			return 0
		fi

		if (( attempt >= remote_retry_count )); then
			return 1
		fi

		echo "Remote command failed, retrying in ${remote_retry_delay_seconds}s (attempt ${attempt}/${remote_retry_count})..." >&2
		sleep "${remote_retry_delay_seconds}"
		attempt=$((attempt + 1))
	done
}

remote_ssh() {
	ssh -p "${remote_port}" "${remote_ssh_opts[@]}" "${remote_target}" "$@"
}

remote_ssh_retry() {
	with_remote_retries remote_ssh "$@"
}

remote_rsync_to() {
	rsync -az --delete \
		-e "ssh -p ${remote_port} ${remote_ssh_opts[*]}" \
		--exclude .git/ \
		--exclude .cache/ \
		--exclude .env.kernel \
		--exclude .env.remote \
		--exclude '*.o' \
		--exclude '*.ko' \
		--exclude '*.mod' \
		--exclude '*.mod.c' \
		--exclude '*.cmd' \
		--exclude 'Module.symvers' \
		--exclude 'modules.order' \
		--exclude 'tools/libcamera-capture' \
		--exclude 'tools/libcamera-record' \
		--exclude 'tools/rgb24-to-rggb10' \
		"${sensorium_repo_root}/" "${remote_target}:${remote_repo_dir}/"
}

remote_rsync_to_retry() {
	with_remote_retries remote_rsync_to
}

remote_rsync_from() {
	local remote_path="$1"
	local local_path="$2"

	rsync -az \
		-e "ssh -p ${remote_port} ${remote_ssh_opts[*]}" \
		"${remote_target}:${remote_path}" "${local_path}"
}

remote_rsync_from_retry() {
	with_remote_retries remote_rsync_from "$@"
}

remote_install_ipa_config() {
	local local_profile_yaml="${sensorium_repo_root}/config/ipa/simple/overrides/${sensorium_sensor}.yaml"
	local local_generic_yaml="${sensorium_repo_root}/config/ipa/simple/imx-generic.yaml"
	local remote_profile_yaml="${sensorium_libcamera_prefix}/share/libcamera/ipa/simple/${sensorium_sensor}.yaml"
	local skip_sync="${SENSORIUM_SKIP_IPA_SYNC:-0}"

	if [[ "${sensorium_family}" != "imx" ]]; then
		echo "Unsupported SENSORIUM_FAMILY for current tuning installer: ${sensorium_family}" >&2
		exit 2
	fi

	remote_ssh_retry "mkdir -p '${sensorium_libcamera_prefix}/share/libcamera/ipa/simple'"

	if [[ ! -f "${local_generic_yaml}" ]]; then
		echo "Missing generic IMX IPA config: ${local_generic_yaml}" >&2
		exit 2
	fi

	if [[ "${skip_sync}" != "1" ]]; then
		remote_rsync_to_retry >/dev/null
	fi

	if [[ -f "${local_profile_yaml}" ]]; then
		remote_ssh_retry "install -m 0644 '${remote_repo_dir}/config/ipa/simple/overrides/${sensorium_sensor}.yaml' '${remote_profile_yaml}'"
	else
		remote_ssh_retry "install -m 0644 '${remote_repo_dir}/config/ipa/simple/imx-generic.yaml' '${remote_profile_yaml}'"
	fi
}

sensorium_profile_template() {
	case "${sensorium_sensor}" in
	imx708)
		printf 'imx708_wide\n'
		;;
	imx477|imx347|imx367|imx387|imx412|imx577)
		printf 'imx477_12mp\n'
		;;
	imx219)
		printf 'imx219_8mp\n'
		;;
	imx304|imx305|imx415|imx485|imx515|imx585|imx678|imx715|imx908)
		printf 'imx8mp_wide\n'
		;;
	imx335|imx675|imx676)
		printf 'imx5mp_wide\n'
		;;
	imx464|imx664)
		printf 'imx4mp_wide\n'
		;;
	imx250|imx252|imx253|imx264|imx265|imx420|imx421|imx422|imx426|imx428|imx429|imx430|imx568)
		printf 'imx5mp_43\n'
		;;
	imx425|imx432)
		printf 'imx3mp_43\n'
		;;
	imx273|imx287|imx290|imx296|imx297|imx327|imx392|imx462|imx662)
		printf 'imx2mp_fhd\n'
		;;
	imx519)
		printf 'imx16mp_43\n'
		;;
	imx294|imx492)
		printf 'imx20mp_43\n'
		;;
	imx530|imx531|imx532|imx535|imx536|imx537|imx900)
		printf 'imx24mp_43\n'
		;;
	imx410|imx455|imx461|imx571)
		printf 'imx26mp_43\n'
		;;
	imx533)
		printf 'imx9mp_square\n'
		;;
	*)
		printf 'imx708_wide\n'
		;;
	esac
}

sensorium_default_raw_size() {
	case "$(sensorium_profile_template)" in
	imx708_wide)
		printf '1536 864\n'
		;;
	imx477_12mp)
		printf '1332 990\n'
		;;
	imx219_8mp|imx8mp_wide|imx5mp_wide|imx4mp_wide|imx2mp_fhd)
		printf '1280 720\n'
		;;
	imx5mp_43)
		printf '1296 972\n'
		;;
	imx3mp_43)
		printf '1280 960\n'
		;;
	imx16mp_43|imx20mp_43|imx24mp_43|imx26mp_43)
		printf '1920 1080\n'
		;;
	imx9mp_square)
		printf '1504 1504\n'
		;;
	*)
		printf '1536 864\n'
		;;
	esac
}

sensorium_default_processed_size() {
	case "$(sensorium_profile_template)" in
	imx219_8mp)
		printf '1636 1232\n'
		;;
	imx8mp_wide|imx5mp_wide|imx4mp_wide|imx2mp_fhd)
		printf '1916 1080\n'
		;;
	imx5mp_43)
		printf '2588 1944\n'
		;;
	imx3mp_43)
		printf '2044 1536\n'
		;;
	*)
		local raw_width raw_height

		read -r raw_width raw_height < <(sensorium_default_raw_size)
		if (( raw_width > 4 )); then
			printf '%d %d\n' "$((raw_width - 4))" "${raw_height}"
		else
			printf '%d %d\n' "${raw_width}" "${raw_height}"
		fi
		;;
	esac
}

sensorium_default_processed_inject_size() {
	case "$(sensorium_profile_template)" in
	imx219_8mp)
		printf '1640 1232\n'
		;;
	imx8mp_wide|imx5mp_wide|imx4mp_wide|imx2mp_fhd)
		printf '1920 1080\n'
		;;
	imx5mp_43)
		printf '2592 1944\n'
		;;
	imx3mp_43)
		printf '2048 1536\n'
		;;
	*)
		sensorium_default_raw_size
		;;
	esac
}
