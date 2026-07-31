#!/bin/bash
# print_access_url.sh
# Reprint the Jupyter access URL at the end of the startup log.
#
# The ServerApp banner that carries the token URL is printed early and then
# scrolls out of view behind extension and deferred-startup log output, so
# users have to dig through the log to find their login link. This helper is
# launched in the background from before_notebook.sh, waits until the Jupyter
# server actually answers HTTP, lets the startup log burst settle, and then
# prints one final, easy-to-spot access link.
#
# The URL and token are read from the server's own runtime info file
# (jpserver-<pid>.json), so the printed link always matches what the server
# generated. Disable with NEURODESKTOP_PRINT_ACCESS_URL=0.

set -o pipefail

if [ "${NEURODESKTOP_PRINT_ACCESS_URL:-1}" = "0" ]; then
    exit 0
fi

MAX_WAIT="${NEURODESKTOP_ACCESS_URL_MAX_WAIT:-180}"
SETTLE_SECONDS="${NEURODESKTOP_ACCESS_URL_SETTLE:-3}"

# Newest server info file across the runtime dirs the server may use. The
# watcher can run as root while the server runs as ${NB_USER}, so check both
# HOME-derived locations.
newest_server_info() {
    local dir
    local -a candidates=()
    for dir in \
        "${JUPYTER_RUNTIME_DIR:-}" \
        "${HOME:-}/.local/share/jupyter/runtime" \
        "/home/${NB_USER:-jovyan}/.local/share/jupyter/runtime"
    do
        if [ -n "${dir}" ] && [ -d "${dir}" ]; then
            candidates+=("${dir}"/jpserver-*.json)
        fi
    done
    if [ "${#candidates[@]}" -eq 0 ]; then
        return 1
    fi
    ls -1t "${candidates[@]}" 2>/dev/null | head -n 1
}

# Emit two lines from the info file: a 127.0.0.1 probe URL and the
# user-facing access URL. Fails (non-zero, no output) on unreadable or
# incomplete files, e.g. one the server is still writing.
urls_from_info() {
    python3 - "$1" <<'PY' 2>/dev/null
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fp:
        info = json.load(fp)
except (OSError, ValueError):
    sys.exit(1)

port = info.get("port")
if not port:
    sys.exit(1)
base_url = info.get("base_url") or "/"
if not base_url.endswith("/"):
    base_url += "/"

access = f"http://localhost:{port}{base_url}lab"
token = info.get("token") or ""
if token:
    access += f"?token={token}"

print(f"http://127.0.0.1:{port}{base_url}")
print(access)
PY
}

WAITED=0
PROBE_URL=""
while :; do
    INFO_FILE="$(newest_server_info || true)"
    if [ -n "${INFO_FILE}" ]; then
        URLS="$(urls_from_info "${INFO_FILE}")"
        PROBE_URL="$(printf '%s\n' "${URLS}" | sed -n 1p)"
        # Any HTTP response (even a redirect to the login page) means the
        # server is up; no -f so status codes do not matter.
        if [ -n "${PROBE_URL}" ] && \
            curl -so /dev/null --connect-timeout 1 --max-time 2 "${PROBE_URL}"; then
            break
        fi
    fi
    if [ "${WAITED}" -ge "${MAX_WAIT}" ]; then
        echo "[WARN] print_access_url.sh: Jupyter did not answer within ${MAX_WAIT}s; no access link printed."
        exit 0
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done

# Let the extension/startup log burst finish so the link lands last.
sleep "${SETTLE_SECONDS}"

# Re-resolve after the wait: a restarted server may have written a newer
# info file (with a new token) in the meantime.
INFO_FILE="$(newest_server_info || true)"
ACCESS_URL=""
if [ -n "${INFO_FILE}" ]; then
    ACCESS_URL="$(urls_from_info "${INFO_FILE}" | sed -n 2p)"
fi
if [ -z "${ACCESS_URL}" ]; then
    ACCESS_URL="$(printf '%s\n' "${URLS}" | sed -n 2p)"
fi
if [ -z "${ACCESS_URL}" ]; then
    echo "[WARN] print_access_url.sh: could not determine the Jupyter access URL."
    exit 0
fi

echo ""
echo "=========================================================================="
echo " Neurodesktop is ready. Open this link in your browser:"
echo ""
echo "     ${ACCESS_URL}"
echo ""
echo "=========================================================================="
