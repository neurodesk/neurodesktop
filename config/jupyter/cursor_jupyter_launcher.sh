#!/usr/bin/env bash
set -euo pipefail

PORT="${1:?missing port}"
HOME_DIR="${2:-${HOME:-/home/jovyan}}"
ARCH="$(uname -m || true)"

run_code_server() {
    exec /usr/local/bin/code-server \
        --auth none \
        --disable-telemetry \
        --disable-update-check \
        --bind-addr "127.0.0.1:${PORT}" \
        "${HOME_DIR}"
}

# Cursor serve-web currently loops indefinitely on linux x64 because the
# required web server artifact is unavailable upstream for the resolved commit.
# Fall back to code-server on x64 so the launcher remains functional.
if [ "${ARCH}" = "x86_64" ] || [ "${ARCH}" = "amd64" ]; then
    echo "cursor serve-web is unavailable on ${ARCH}; falling back to code-server" >&2
    run_code_server
fi

mkdir -p \
    "${HOME_DIR}/.local/share/cursor-cli" \
    "${HOME_DIR}/.local/share/cursor-server"

exec /usr/local/bin/cursor \
    serve-web \
    --host 127.0.0.1 \
    --port "${PORT}" \
    --without-connection-token \
    --accept-server-license-terms \
    --cli-data-dir "${HOME_DIR}/.local/share/cursor-cli" \
    --server-data-dir "${HOME_DIR}/.local/share/cursor-server"
