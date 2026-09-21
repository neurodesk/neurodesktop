#!/bin/bash
# xrdp starts during root initialization; notebook users cannot start root services.
set -euo pipefail

credentials="/run/neurodesktop/rdp/$(id -u)"
if [ ! -r "${credentials}/port" ] || [ ! -r "${credentials}/password" ]; then
    echo "[WARN] RDP was not provisioned by root startup; use the VNC desktop." >&2
    exit 1
fi
RDP_PORT="$(cat "${credentials}/port")"
if ! ss -lnt | awk 'NR>1 {print $4}' | grep -Eq "(^|:)${RDP_PORT}$"; then
    echo "[WARN] The RDP service is unavailable on its provisioned port." >&2
    exit 1
fi
NEURODESKTOP_RUNTIME_DIR="${NEURODESKTOP_RUNTIME_DIR:-${HOME}/.neurodesk/runtime}"
mkdir -p "${NEURODESKTOP_RUNTIME_DIR}"
printf '%s\n' "${RDP_PORT}" > "${NEURODESKTOP_RUNTIME_DIR}/rdp_port"
