#!/bin/bash
# Open the OpenCode web interface from the Neurodesktop VNC/RDP desktop.
#
# Inside the desktop there is no URL prefix, so the upstream web UI works
# unmodified: this starts (or reuses) the opencode_web.py launcher on a
# per-user dynamic port and opens Firefox with a single-use ?auth= login
# token (opencode_web.py swaps it for a cookie, rotates the token, and
# strips it from the URL).
#
# The launcher's port is recorded in a 0600 state file as "PID PORT", and a
# recorded listener is only reused after verifying we own that PID and it is
# still running our launcher — on a shared host another user could otherwise
# squat a well-known port and receive our login token.

set -u

STATE_FILE="${OPENCODE_WEB_DESKTOP_STATE:-${HOME}/.neurodesk/run/opencode_web_desktop.state}"
LAUNCHER="${OPENCODE_WEB_LAUNCHER:-/opt/neurodesktop/opencode_web.py}"
LOG_DIR="${HOME}/.neurodesk/logs"
LOG_FILE="${LOG_DIR}/opencode-web-desktop.log"
BROWSER="${OPENCODE_WEB_BROWSER:-/usr/local/bin/neurodesktop-firefox}"

PORT=""

port_is_listening() {
    (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null || return 1
    exec 3>&- 3<&-
    return 0
}

launcher_alive() {
    [ -f "${STATE_FILE}" ] || return 1
    local pid port
    read -r pid port < "${STATE_FILE}" 2>/dev/null || return 1
    [ -n "${pid}" ] && [ -n "${port}" ] || return 1
    # Only trust a listener we own: the recorded PID must be alive, belong
    # to the current user, and still be running our launcher script.
    [ -d "/proc/${pid}" ] || return 1
    [ -O "/proc/${pid}" ] || return 1
    grep -qz -- "opencode_web.py" "/proc/${pid}/cmdline" 2>/dev/null || return 1
    PORT="${port}"
    return 0
}

if ! launcher_alive; then
    mkdir -p "$(dirname "${STATE_FILE}")" "${LOG_DIR}"
    chmod 700 "$(dirname "${STATE_FILE}")"
    PORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
    if [ -z "${PORT}" ]; then
        echo "Could not allocate a local port for the OpenCode web launcher" >&2
        exit 1
    fi
    nohup python3 "${LAUNCHER}" --port "${PORT}" >> "${LOG_FILE}" 2>&1 &
    umask 077
    printf '%s %s\n' "$!" "${PORT}" > "${STATE_FILE}"
fi

TOKEN_FILE="${OPENCODE_WEB_LOGIN_TOKEN_FILE:-${HOME}/.neurodesk/secrets/opencode_web_login_token.${PORT}}"

for _ in $(seq 1 60); do
    if port_is_listening && [ -s "${TOKEN_FILE}" ]; then
        break
    fi
    sleep 0.5
done

if [ ! -s "${TOKEN_FILE}" ]; then
    echo "OpenCode web launcher did not create ${TOKEN_FILE}; see ${LOG_FILE}" >&2
    exit 1
fi

TOKEN=$(head -n 1 "${TOKEN_FILE}" | tr -d '\r\n')
exec "${BROWSER}" "http://127.0.0.1:${PORT}/?auth=${TOKEN}"
