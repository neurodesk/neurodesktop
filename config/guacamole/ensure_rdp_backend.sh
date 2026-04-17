#!/bin/bash

# Probe for a free TCP port starting at $1, stepping by 1, up to $2 attempts.
find_free_tcp_port() {
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

# Pick a free RDP port. Under Apptainer all users share the host netns, so the
# default 3389 is very likely taken by the first user on the node. Probe from
# 3389 upwards and export the chosen port so guacamole.sh can stamp it into the
# user's Guacamole mapping.
RDP_PORT="${NEURODESKTOP_RDP_PORT:-}"
if [ -z "${RDP_PORT}" ]; then
    RDP_PORT="$(find_free_tcp_port 3389 20 || true)"
fi
export NEURODESKTOP_RDP_PORT="${RDP_PORT}"

# Publish the chosen port so guacamole.sh (parent process) can read it back.
# This file lives under the per-user runtime state directory. Subshell exports
# don't propagate up, so a file is the simplest handoff.
NEURODESKTOP_RUNTIME_DIR="${NEURODESKTOP_RUNTIME_DIR:-${HOME}/.neurodesk/runtime}"
mkdir -p "${NEURODESKTOP_RUNTIME_DIR}" 2>/dev/null || true
printf '%s\n' "${RDP_PORT}" > "${NEURODESKTOP_RUNTIME_DIR}/rdp_port" 2>/dev/null || true

port_is_listening() {
    if ! command -v ss >/dev/null 2>&1; then
        return 1
    fi

    ss -lnt 2>/dev/null | awk 'NR>1 {print $4}' | grep -Eq "(^|:)${RDP_PORT}$"
}

start_rdp_service() {
    # xrdp's configured port lives in /etc/xrdp/xrdp.ini. On a read-only Apptainer
    # rootfs we can't mutate that file, so we fall back to the systemd default
    # (3389). In Docker (writable rootfs) we rewrite the port before starting.
    if [ -w /etc/xrdp/xrdp.ini ] && [ "${RDP_PORT}" != "3389" ]; then
        sed -i -E "s|^port=[0-9]+|port=${RDP_PORT}|" /etc/xrdp/xrdp.ini 2>/dev/null || true
    fi

    if [ "$EUID" -eq 0 ]; then
        service xrdp start
        return $?
    fi

    if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        sudo -n service xrdp start
        return $?
    fi

    echo "[WARN] xrdp is not running and passwordless sudo is unavailable."
    return 1
}

wait_for_rdp_port() {
    local attempt

    for attempt in $(seq 1 15); do
        if port_is_listening; then
            return 0
        fi
        sleep 1
    done

    echo "[WARN] xrdp did not begin listening on port ${RDP_PORT}."
    return 1
}

main() {
    if port_is_listening; then
        return 0
    fi

    if ! command -v service >/dev/null 2>&1; then
        echo "[WARN] service command is unavailable; cannot start xrdp."
        return 1
    fi

    if ! start_rdp_service; then
        echo "[WARN] Failed to start xrdp service."
        return 1
    fi

    wait_for_rdp_port
}

main "$@"
