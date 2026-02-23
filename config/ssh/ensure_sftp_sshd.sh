#!/bin/bash

SSH_DIR="${HOME}/.ssh"
SSH_CONFIG="${SSH_DIR}/sshd_config"
DEFAULT_SSH_CONFIG="/opt/jovyan_defaults/.ssh/sshd_config"
HOSTKEY_DIR="${SSH_DIR}/hostkeys"
SSH_HOST_ED25519_KEY="${HOSTKEY_DIR}/ssh_host_ed25519_key"
SSH_HOST_RSA_KEY="${HOSTKEY_DIR}/ssh_host_rsa_key"
AUTHORIZED_KEYS_FILE="${SSH_DIR}/authorized_keys"
GUACAMOLE_MAPPING_FILE="/etc/guacamole/user-mapping.xml"
SFTP_SSH_PORT="${SFTP_SSH_PORT:-2222}"
SFTP_SSH_PID_FILE="/tmp/sshd_2222.pid"

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
StrictModes no
PidFile ${SFTP_SSH_PID_FILE}
Subsystem sftp internal-sftp
EOF
}

port_is_listening() {
    if ! command -v ss >/dev/null 2>&1; then
        return 1
    fi

    ss -lnt 2>/dev/null | awk 'NR>1 {print $4}' | grep -Eq "(^|:)${SFTP_SSH_PORT}$"
}

start_sshd() {
    local -a sshd_prefix=()
    local ssh_config_to_use validation_log startup_log fallback_config

    if port_is_listening; then
        return 0
    fi

    if [ -f "${SFTP_SSH_PID_FILE}" ]; then
        local existing_pid
        existing_pid="$(cat "${SFTP_SSH_PID_FILE}" 2>/dev/null || true)"
        if [ -n "${existing_pid}" ] && kill -0 "${existing_pid}" 2>/dev/null; then
            return 0
        fi
        rm -f "${SFTP_SSH_PID_FILE}"
    fi

    if sudo -n true 2>/dev/null; then
        sshd_prefix=(sudo)
        sudo mkdir -p /run/sshd
        sudo chmod 755 /run/sshd
    fi

    ssh_config_to_use="${SSH_CONFIG}"
    validation_log="$(mktemp /tmp/sshd-validate.XXXXXX)"
    startup_log="$(mktemp /tmp/sshd-start.XXXXXX)"

    if ! "${sshd_prefix[@]}" /usr/sbin/sshd -t -f "${ssh_config_to_use}" >"${validation_log}" 2>&1; then
        warn "sshd config validation failed for ${ssh_config_to_use}. Retrying with minimal fallback config."
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

    if ! "${sshd_prefix[@]}" /usr/sbin/sshd \
        -f "${ssh_config_to_use}" \
        -p "${SFTP_SSH_PORT}" \
        -h "${SSH_HOST_ED25519_KEY}" \
        -h "${SSH_HOST_RSA_KEY}" \
        -o "PidFile=${SFTP_SSH_PID_FILE}" >"${startup_log}" 2>&1; then
        warn "Failed to start sshd on port ${SFTP_SSH_PORT}."
        emit_sshd_log_preview "${startup_log}"
        rm -f "${validation_log}" "${startup_log}"
        return 1
    fi

    rm -f "${validation_log}" "${startup_log}"
    return 0
}

main() {
    if ! ensure_sshd_config; then
        return 1
    fi
    ensure_host_keys
    ensure_authorized_sftp_key || true
    start_sshd
}

main "$@"
