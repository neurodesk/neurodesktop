#!/bin/bash
# Open the OpenCode web interface from the Neurodesktop VNC/RDP desktop.
#
# Inside the desktop there is no URL prefix, so the upstream web UI works
# unmodified: this starts (or reuses) the opencode_web.py launcher on a fixed
# local port and opens Firefox with a one-time ?auth= credential exchange
# (opencode_web.py swaps it for a cookie and strips it from the URL).

set -u

PORT="${OPENCODE_WEB_DESKTOP_PORT:-4747}"
SECRET_FILE="${OPENCODE_WEB_SECRET_FILE:-${HOME}/.neurodesk/secrets/opencode_server_password}"
LAUNCHER="${OPENCODE_WEB_LAUNCHER:-/opt/neurodesktop/opencode_web.py}"
LOG_DIR="${HOME}/.neurodesk/logs"
LOG_FILE="${LOG_DIR}/opencode-web-desktop.log"
BROWSER="${OPENCODE_WEB_BROWSER:-/usr/local/bin/neurodesktop-firefox}"

port_is_listening() {
    (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null || return 1
    exec 3>&- 3<&-
    return 0
}

if ! port_is_listening; then
    mkdir -p "${LOG_DIR}"
    nohup python3 "${LAUNCHER}" --port "${PORT}" >> "${LOG_FILE}" 2>&1 &
fi

for _ in $(seq 1 60); do
    if port_is_listening && [ -s "${SECRET_FILE}" ]; then
        break
    fi
    sleep 0.5
done

if [ ! -s "${SECRET_FILE}" ]; then
    echo "OpenCode web launcher did not create ${SECRET_FILE}; see ${LOG_FILE}" >&2
    exit 1
fi

PASSWORD=$(head -n 1 "${SECRET_FILE}" | tr -d '\r\n')
exec "${BROWSER}" "http://127.0.0.1:${PORT}/?auth=${PASSWORD}"
