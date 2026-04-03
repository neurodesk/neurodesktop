#!/bin/bash

RDP_PORT="${NEURODESKTOP_RDP_PORT:-3389}"

port_is_listening() {
    if ! command -v ss >/dev/null 2>&1; then
        return 1
    fi

    ss -lnt 2>/dev/null | awk 'NR>1 {print $4}' | grep -Eq "(^|:)${RDP_PORT}$"
}

start_rdp_service() {
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
