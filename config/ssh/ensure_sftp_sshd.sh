#!/bin/bash

# Publish a port only after sshd binds it; stale ports can break VNC connections.
NEURODESKTOP_RUNTIME_DIR="${NEURODESKTOP_RUNTIME_DIR:-${HOME}/.neurodesk/runtime}"
mkdir -p "${NEURODESKTOP_RUNTIME_DIR}" 2>/dev/null || true
rm -f "${NEURODESKTOP_RUNTIME_DIR}/sftp_port" 2>/dev/null || true
if [ "${NEURODESKTOP_DISABLE_SSH:-false}" = "true" ]; then
    echo "[INFO] NEURODESKTOP_DISABLE_SSH=true: skipping SSH/SFTP service."
    exit 0
fi

SSH_DIR="${HOME}/.ssh"
SSH_CONFIG="${SSH_DIR}/sshd_config"
DEFAULT_SSH_CONFIG="/opt/jovyan_defaults/.ssh/sshd_config"
HOSTKEY_DIR="${SSH_DIR}/hostkeys"
SSH_HOST_ED25519_KEY="${HOSTKEY_DIR}/ssh_host_ed25519_key"
SSH_HOST_RSA_KEY="${HOSTKEY_DIR}/ssh_host_rsa_key"
AUTHORIZED_KEYS_FILE="${SSH_DIR}/authorized_keys"
GUACAMOLE_HOME="${GUACAMOLE_HOME:-${HOME}/.neurodesk/guacamole}"
GUACAMOLE_MAPPING_FILE="${GUACAMOLE_HOME}/user-mapping.xml"
if [ ! -f "${GUACAMOLE_MAPPING_FILE}" ]; then
    # Fall back to the build-time template (read-only) if the per-user copy
    # hasn't been seeded yet - just for key extraction below.
    GUACAMOLE_MAPPING_FILE="/etc/guacamole/user-mapping-vnc-rdp.xml"
fi

find_free_tcp_port_for_sftp() {
    local start_port="$1"
    local max_attempts="${2:-20}"
    local port="${start_port}"
    local attempts=0
    if ! command -v ss >/dev/null 2>&1; then
        printf '%s' "${start_port}"
        return 0
    fi
    while [ "${attempts}" -lt "${max_attempts}" ]; do
        if ! ss -lnt 2>/dev/null | awk 'NR>1 {print $4}' | grep -Eq "(^|:)${port}$"; then
            printf '%s' "${port}"
            return 0
        fi
        port=$((port + 1))
        attempts=$((attempts + 1))
    done
    printf '%s' "${port}"
    return 1
}

# Pick a unique SFTP/SSH port on shared Apptainer HPC nodes. 2222 may be taken
# by another user's sibling container, so probe from 2222 upwards. Remember
# whether the caller selected an exact port: automatic selections may retry a
# bind race, but an explicit request must either use that port or fail.
SFTP_SSH_PORT_EXPLICIT=1
if [ -z "${SFTP_SSH_PORT:-}" ]; then
    SFTP_SSH_PORT_EXPLICIT=0
    if ! SFTP_SSH_PORT="$(find_free_tcp_port_for_sftp 2222 20)"; then
        echo "[WARN] No free SSH/SFTP port found from 2222 through 2241."
        exit 1
    fi
fi
export NEURODESKTOP_SFTP_PORT="${SFTP_SSH_PORT}"

# Keep daemon state in the user's private SSH directory on shared HPC hosts.
SFTP_SSH_PID_FILE="${SSH_DIR}/sshd_${SFTP_SSH_PORT}.pid"

warn() {
    echo "[WARN] $1"
}

emit_sshd_log_preview() {
    local log_file="$1"
    if [ -f "${log_file}" ]; then
        sed -n '1,12p' "${log_file}" | while IFS= read -r line; do
            warn "sshd: ${line}"
        done
    fi
}

ensure_sshd_config() {
    mkdir -p "${SSH_DIR}"
    chmod 700 "${SSH_DIR}" 2>/dev/null || true

    if [ ! -f "${SSH_CONFIG}" ]; then
        if [ -f "${DEFAULT_SSH_CONFIG}" ]; then
            cp "${DEFAULT_SSH_CONFIG}" "${SSH_CONFIG}"
        else
            warn "Default sshd_config not found at ${DEFAULT_SSH_CONFIG}."
            return 1
        fi
    fi

    sed -i "s|/home/jovyan|${HOME}|g" "${SSH_CONFIG}" 2>/dev/null || true
}

ensure_host_keys() {
    mkdir -p "${HOSTKEY_DIR}"
    chmod 700 "${HOSTKEY_DIR}" 2>/dev/null || true

    if [ ! -f "${SSH_HOST_ED25519_KEY}" ]; then
        ssh-keygen -q -t ed25519 -f "${SSH_HOST_ED25519_KEY}" -N ''
    fi

    if [ ! -f "${SSH_HOST_RSA_KEY}" ]; then
        ssh-keygen -q -t rsa -b 2048 -f "${SSH_HOST_RSA_KEY}" -N ''
    fi

    chmod 600 "${SSH_HOST_ED25519_KEY}" "${SSH_HOST_RSA_KEY}" 2>/dev/null || true
}

extract_sftp_public_key() {
    local temp_private_key
    temp_private_key="$(mktemp /tmp/guacamole_sftp_private_key.XXXXXX)" || return 1

    awk '
        /<param name="sftp-private-key">/ {capture=1; next}
        capture && /<\/param>/ {capture=0; exit}
        capture {
            sub(/^[[:space:]]+/, "", $0)
            if (length($0) > 0) {
                print $0
            }
        }
    ' "${GUACAMOLE_MAPPING_FILE}" > "${temp_private_key}"

    if ! grep -q 'BEGIN .*PRIVATE KEY' "${temp_private_key}" 2>/dev/null; then
        rm -f "${temp_private_key}"
        return 1
    fi

    chmod 600 "${temp_private_key}"
    ssh-keygen -y -f "${temp_private_key}" 2>/dev/null
    local status=$?
    rm -f "${temp_private_key}"
    return "${status}"
}

ensure_authorized_sftp_key() {
    local fallback_public_key_file
    local sftp_public_key
    fallback_public_key_file="${SSH_DIR}/guacamole_rsa.pub"

    if [ ! -f "${GUACAMOLE_MAPPING_FILE}" ]; then
        warn "Guacamole mapping file not found at ${GUACAMOLE_MAPPING_FILE}."
        return 1
    fi

    touch "${AUTHORIZED_KEYS_FILE}"
    chmod 600 "${AUTHORIZED_KEYS_FILE}" 2>/dev/null || true

    sftp_public_key="$(extract_sftp_public_key || true)"
    if [ -z "${sftp_public_key}" ]; then
        if [ -f "${fallback_public_key_file}" ]; then
            sftp_public_key="$(cat "${fallback_public_key_file}")"
        else
            warn "Could not extract SFTP key from ${GUACAMOLE_MAPPING_FILE}."
            return 1
        fi
    fi

    if ! grep -qxF -- "${sftp_public_key}" "${AUTHORIZED_KEYS_FILE}" 2>/dev/null; then
        printf '%s\n' "${sftp_public_key}" >> "${AUTHORIZED_KEYS_FILE}"
    fi

    return 0
}

write_fallback_sshd_config() {
    local config_file="$1"
    cat > "${config_file}" <<EOF
Port ${SFTP_SSH_PORT}
ListenAddress 127.0.0.1
HostKey ${SSH_HOST_ED25519_KEY}
HostKey ${SSH_HOST_RSA_KEY}
AuthorizedKeysFile ${AUTHORIZED_KEYS_FILE}
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
UsePAM no
PermitRootLogin no
X11Forwarding no
DisableForwarding yes
PermitTTY no
StrictModes no
PidFile ${SFTP_SSH_PID_FILE}
Subsystem sftp internal-sftp
ForceCommand internal-sftp
EOF
}

port_is_listening() {
    if ! command -v ss >/dev/null 2>&1; then
        return 1
    fi

    ss -lnt 2>/dev/null | awk 'NR>1 {print $4}' | grep -Eq "(^|:)${SFTP_SSH_PORT}$"
}

sshd_owns_listener() {
    local pid
    pid="$(cat "${SFTP_SSH_PID_FILE}" 2>/dev/null)" || return 1
    [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
    "${sshd_prefix[@]}" kill -0 "${pid}" 2>/dev/null || return 1
    "${sshd_prefix[@]}" ss -lntpH "sport = :${SFTP_SSH_PORT}" 2>/dev/null | grep -Eq "pid=${pid},"
}

start_sshd() {
    local -a sshd_prefix=()
    local ssh_config_to_use validation_log startup_log fallback_config

    if port_is_listening; then
        warn "Port ${SFTP_SSH_PORT} became unavailable before sshd startup."
        return 75
    fi

    # A persisted PID can belong to another host or a previous container.
    # The listener check above and sshd's bind determine local availability.
    rm -f "${SFTP_SSH_PID_FILE}"

    if sudo -n true 2>/dev/null; then
        sshd_prefix=(sudo)
        sudo mkdir -p /run/sshd
        sudo chmod 755 /run/sshd
    fi

    ssh_config_to_use="${SSH_CONFIG}"
    validation_log="$(mktemp /tmp/sshd-validate.XXXXXX)"
    startup_log="$(mktemp /tmp/sshd-start.XXXXXX)"

    # ListenAddress is additive, including between -o and Include files. Check
    # the complete effective configuration before trusting a persisted config.
    if ! "${sshd_prefix[@]}" /usr/sbin/sshd -T -f "${ssh_config_to_use}" \
        -p "${SFTP_SSH_PORT}" -o "ListenAddress=127.0.0.1" >"${validation_log}" 2>&1 \
        || ! awk -v expected="127.0.0.1:${SFTP_SSH_PORT}" '
            $1 == "listenaddress" {
                found = 1
                if ($2 != expected) unsafe = 1
            }
            END { exit unsafe || !found }
        ' "${validation_log}"; then
        warn "sshd config is invalid or allows a non-loopback listener: ${ssh_config_to_use}. Retrying with minimal fallback config."
        emit_sshd_log_preview "${validation_log}"

        fallback_config="${SSH_DIR}/sshd_config.fallback"
        write_fallback_sshd_config "${fallback_config}"
        ssh_config_to_use="${fallback_config}"

        if ! "${sshd_prefix[@]}" /usr/sbin/sshd -t -f "${ssh_config_to_use}" >"${validation_log}" 2>&1; then
            warn "Fallback sshd config validation failed for ${ssh_config_to_use}."
            emit_sshd_log_preview "${validation_log}"
            rm -f "${validation_log}" "${startup_log}"
            return 1
        fi
    fi

    # Forcefully override key options at the command line so the unprivileged
    # (non-root sshd) path works regardless of which config file happened to
    # load:
    #   UsePAM=no      - a non-root sshd cannot validate via PAM (no access
    #                    to /etc/shadow), so pam_unix account-phase fails and
    #                    Guacamole's SFTP side-channel aborts the whole VNC
    #                    tunnel with CLIENT_UNAUTHORIZED (0x0301). Only the
    #                    fallback config set this; the primary sshd_config
    #                    inherited from the image default leaves UsePAM=yes.
    #   StrictModes=no - the simulated HPC HOME (bind-mounted host tempdir
    #                    or the real /home/jovyan with unusual ownership) is
    #                    not guaranteed to satisfy sshd's default 0700/0750
    #                    ownership checks, and sshd rejects pubkey auth on
    #                    failure. We are only ever accepting a key that we
    #                    wrote ourselves; no weaker security posture.
    # ListenAddress supplies the loopback default for legacy configurations.
    # The effective-address check above excludes additional saved listeners.
    # This daemon exists only for Guacamole file transfer. Persisted home
    # configs predate that boundary, so enforce it on the command line too:
    # internal-sftp denies shell/command execution, DisableForwarding covers
    # TCP, stream-local, agent and X11 forwarding, and no client needs a TTY.
    # sshd -E appends to this log; it does not read it as an input file.
    # shellcheck disable=SC2094
    if ! "${sshd_prefix[@]}" /usr/sbin/sshd \
        -E "${startup_log}" \
        -f "${ssh_config_to_use}" \
        -p "${SFTP_SSH_PORT}" \
        -h "${SSH_HOST_ED25519_KEY}" \
        -h "${SSH_HOST_RSA_KEY}" \
        -o "PidFile=${SFTP_SSH_PID_FILE}" \
        -o "UsePAM=no" \
        -o "StrictModes=no" \
        -o "ListenAddress=127.0.0.1" \
        -o "DisableForwarding=yes" \
        -o "ForceCommand=internal-sftp" \
        -o "PermitTTY=no" \
        -o "KbdInteractiveAuthentication=no" \
        -o "PasswordAuthentication=no" \
        -o "PubkeyAuthentication=yes" >>"${startup_log}" 2>&1; then
        warn "Failed to start sshd on port ${SFTP_SSH_PORT}."
        emit_sshd_log_preview "${startup_log}"
        if grep -qi 'address already in use' "${startup_log}"; then
            rm -f "${validation_log}" "${startup_log}" "${SFTP_SSH_PID_FILE}"
            return 75
        fi
        rm -f "${validation_log}" "${startup_log}"
        return 1
    fi

    # sshd's parent exits before its daemon child binds. Keep the startup log
    # until readiness is known, and never advertise a merely successful fork.
    local attempt
    for ((attempt = 0; attempt < 50; attempt++)); do
        if sshd_owns_listener; then
            rm -f "${validation_log}" "${startup_log}"
            return 0
        fi
        if grep -qi 'address already in use' "${startup_log}"; then
            emit_sshd_log_preview "${startup_log}"
            rm -f "${validation_log}" "${startup_log}" "${SFTP_SSH_PID_FILE}"
            return 75
        fi
        sleep 0.1
    done
    warn "sshd did not start listening on ${SFTP_SSH_PORT} within 5 seconds."
    emit_sshd_log_preview "${startup_log}"
    rm -f "${validation_log}" "${startup_log}"
    return 1
}

start_sshd_with_port_retry() {
    local attempt=1
    local max_attempts=20
    local next_port remaining_attempts selected_port startup_status

    while [ "${attempt}" -le "${max_attempts}" ]; do
        start_sshd
        startup_status=$?
        if [ "${startup_status}" -eq 0 ]; then
            return 0
        fi

        if [ "${startup_status}" -ne 75 ] || [ "${SFTP_SSH_PORT_EXPLICIT}" -eq 1 ]; then
            return "${startup_status}"
        fi

        next_port=$((SFTP_SSH_PORT + 1))
        remaining_attempts=$((max_attempts - attempt))
        if [ "${remaining_attempts}" -le 0 ] \
            || ! selected_port="$(find_free_tcp_port_for_sftp "${next_port}" "${remaining_attempts}")"; then
            warn "Could not start sshd after ${attempt} automatic port attempts."
            return 1
        fi

        warn "Retrying SSH/SFTP startup on port ${selected_port}."
        SFTP_SSH_PORT="${selected_port}"
        export NEURODESKTOP_SFTP_PORT="${SFTP_SSH_PORT}"
        SFTP_SSH_PID_FILE="${SSH_DIR}/sshd_${SFTP_SSH_PORT}.pid"
        attempt=$((attempt + 1))
    done

    return 1
}

main() {
    # Bail out early on HPC/Apptainer when the current UID has no passwd
    # entry. sshd's privilege-separation refuses to service logins it cannot
    # resolve via NSS, so even if the daemon binds it will reject every
    # connection. Leaving NEURODESKTOP_SFTP_PORT unset here is the contract
    # guacamole.sh relies on to disable the SFTP side-channel, which would
    # otherwise abort the whole VNC tunnel with upstream error 515.
    local _current_uid
    _current_uid="$(id -u 2>/dev/null || echo)"
    if [ -z "${_current_uid}" ] || ! getent passwd "${_current_uid}" >/dev/null 2>&1; then
        warn "Current UID (${_current_uid:-unknown}) has no /etc/passwd entry; sshd cannot accept logins. Skipping SFTP side-channel."
        return 1
    fi

    if ! ensure_sshd_config; then
        return 1
    fi
    ensure_host_keys
    ensure_authorized_sftp_key || true
    # Port probes cannot reserve a socket. On a shared Apptainer network,
    # another session can bind the selected port before sshd does. Retry an
    # automatically selected port and publish only a confirmed listener.
    if ! start_sshd_with_port_retry; then
        return 1
    fi
    printf '%s\n' "${SFTP_SSH_PORT}" \
        > "${NEURODESKTOP_RUNTIME_DIR}/sftp_port" 2>/dev/null || true
    return 0
}

main "$@"
