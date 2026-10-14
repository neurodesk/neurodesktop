#!/bin/bash
# Host Slurm client environment for NEURODESKTOP_SLURM_MODE=host.
#
# Sourced by before_notebook.sh, which docker-stacks sources into the shell
# that later execs Jupyter, and by environment_variables.sh in every shell and
# kernel. Sourcing only defines functions. configure_host_slurm_environment
# exports SLURM_CONF, MUNGE_SOCKET and SLURM_SACK_SOCKET into the calling
# shell, so the helper must be sourced rather than executed. Detection is
# best-effort and the function always returns 0.
#
# The optional root argument prefixes the built-in search paths so tests can
# use a fixture tree; startup passes none. Paths supplied through SLURM_CONF,
# MUNGE_SOCKET and SLURM_SACK_SOCKET are used as given.

slurm_conf_is_local_neurodesktop() {
	local conf_file="$1"
	[ -r "${conf_file}" ] && grep -Eq '^ClusterName=neurodesktop([[:space:]]|$)' "${conf_file}" 2>/dev/null
}

resolve_host_slurm_conf() {
	local root="${1:-}"
	local conf_file

	if [ -n "${SLURM_CONF:-}" ] && [ -r "${SLURM_CONF}" ]; then
		if ! slurm_conf_is_local_neurodesktop "${SLURM_CONF}"; then
			echo "${SLURM_CONF}"
			return 0
		fi
	fi

	for conf_file in \
		"${root}/etc/slurm/slurm.conf" \
		"${root}/etc/slurm-llnl/slurm.conf" \
		"${root}/run/host/etc/slurm/slurm.conf" \
		"${root}/host/etc/slurm/slurm.conf"; do
		if [ ! -r "${conf_file}" ]; then
			continue
		fi
		if slurm_conf_is_local_neurodesktop "${conf_file}"; then
			continue
		fi
		echo "${conf_file}"
		return 0
	done

	return 1
}

read_slurm_conf_value() {
	local conf_file="$1"
	local key="$2"
	[ -r "${conf_file}" ] || return 1

	awk -F= -v key="${key}" '
        /^[[:space:]]*#/ { next }
        {
            line=$0
            sub(/^[[:space:]]+/, "", line)
            if (line ~ ("^" key "[[:space:]]*=")) {
                sub(/^[^=]*=/, "", line)
                sub(/[[:space:]]+#.*$/, "", line)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
                print line
                exit
            }
        }
    ' "${conf_file}"
}

resolve_slurm_auth_type() {
	local conf_file="$1"
	local auth_type

	auth_type="$(read_slurm_conf_value "${conf_file}" "AuthType" || true)"
	if [ -n "${auth_type}" ]; then
		printf '%s\n' "${auth_type}" | tr '[:upper:]' '[:lower:]'
		return 0
	fi
	return 1
}

resolve_slurm_cluster_name() {
	local conf_file="$1"
	read_slurm_conf_value "${conf_file}" "ClusterName"
}

resolve_munge_socket() {
	local root="${1:-}"
	local socket_path
	for socket_path in \
		"${MUNGE_SOCKET:-}" \
		"${root}/run/munge/munge.socket.2" \
		"${root}/var/run/munge/munge.socket.2"; do
		if [ -n "${socket_path}" ] && [ -S "${socket_path}" ]; then
			echo "${socket_path}"
			return 0
		fi
	done
	return 1
}

resolve_sack_socket() {
	local cluster_name="$1"
	local root="${2:-}"
	local cluster_socket=""
	local socket_path

	if [ -n "${cluster_name}" ]; then
		cluster_socket="${root}/run/slurm-${cluster_name}/sack.socket"
	fi

	for socket_path in \
		"${SLURM_SACK_SOCKET:-}" \
		"${cluster_socket}" \
		"${root}/run/slurm/sack.socket" \
		"${root}/run/slurmctld/sack.socket" \
		"${root}/run/slurmdbd/sack.socket" \
		"${root}/var/run/slurm/sack.socket" \
		"${root}/var/run/slurmctld/sack.socket" \
		"${root}/var/run/slurmdbd/sack.socket"; do
		if [ -n "${socket_path}" ] && [ -S "${socket_path}" ]; then
			echo "${socket_path}"
			return 0
		fi
	done
	return 1
}

# Usage: configure_host_slurm_environment [--warn] [root]
# --warn prints startup guidance for a missing config or auth socket.
configure_host_slurm_environment() {
	local warn=0
	if [ "${1:-}" = "--warn" ]; then
		warn=1
		shift
	fi
	local root="${1:-}"
	local host_slurm_conf host_munge_socket host_sack_socket host_auth_type host_cluster_name

	host_slurm_conf="$(resolve_host_slurm_conf "${root}" || true)"
	if [ -n "${host_slurm_conf}" ]; then
		export SLURM_CONF="${host_slurm_conf}"
	else
		if [ -n "${SLURM_CONF:-}" ] && slurm_conf_is_local_neurodesktop "${SLURM_CONF}"; then
			unset SLURM_CONF
		fi
		if [ "${warn}" -eq 1 ]; then
			echo "[WARN] Host Slurm mode selected but no readable slurm.conf was found."
			echo "[WARN] Bind your host Slurm config (for example: --bind /etc/slurm:/etc/slurm)."
		fi
	fi

	host_auth_type=""
	host_cluster_name=""
	if [ -n "${host_slurm_conf}" ]; then
		host_auth_type="$(resolve_slurm_auth_type "${host_slurm_conf}" || true)"
		host_cluster_name="$(resolve_slurm_cluster_name "${host_slurm_conf}" || true)"
	fi

	host_munge_socket="$(resolve_munge_socket "${root}" || true)"
	if [ -n "${host_munge_socket}" ]; then
		export MUNGE_SOCKET="${host_munge_socket}"
	fi

	host_sack_socket="$(resolve_sack_socket "${host_cluster_name}" "${root}" || true)"
	if [ -n "${host_sack_socket}" ]; then
		export SLURM_SACK_SOCKET="${host_sack_socket}"
	elif [ "${host_auth_type}" = "auth/slurm" ]; then
		# A stale inherited value would point sackd clients at a dead socket.
		unset SLURM_SACK_SOCKET
	fi

	if [ "${warn}" -eq 1 ]; then
		case "${host_auth_type}" in
		auth/munge)
			if [ -z "${host_munge_socket}" ]; then
				echo "[WARN] Host Slurm uses AuthType=auth/munge but no MUNGE socket was found."
				echo "[WARN] Bind /run/munge or /var/run/munge into the container."
			fi
			;;
		auth/slurm)
			if [ -z "${host_sack_socket}" ]; then
				echo "[WARN] Host Slurm uses AuthType=auth/slurm but no sack.socket was found."
				echo "[WARN] Bind the host Slurm runtime directory (for example: --bind /run/slurm:/run/slurm)."
			fi
			;;
		*)
			if [ -z "${host_munge_socket}" ] && [ -z "${host_sack_socket}" ]; then
				echo "[WARN] Host Slurm mode selected but neither MUNGE nor sack.socket was found."
				echo "[WARN] Bind your host auth socket paths (for example /run/munge or /run/slurm)."
			fi
			;;
		esac
	fi
	return 0
}
