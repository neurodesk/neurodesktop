#!/usr/bin/env bash
# Stream a private LXDE desktop to the browser with voxl (prototype).
#
# Jupyter Server Proxy starts this script with a free loopback port and the
# per-launch token in VOXL_PASSWORD; it sends the same token as a Bearer header
# on every proxied request, so other users on a shared host cannot reach the
# stream without it. The desktop is a dedicated TigerVNC X server whose RFB
# endpoint is a mode-0600 Unix socket: voxl captures the X display directly
# and no TCP VNC listener is opened.
set -euo pipefail

port="${1:?usage: voxl.sh PORT}"
if [ -z "${VOXL_PASSWORD:-}" ]; then
    echo "[ERROR] VOXL_PASSWORD is empty; start voxl through Jupyter Server Proxy." >&2
    exit 1
fi

voxl_bin="${NEURODESKTOP_VOXL_BIN:-/usr/local/bin/voxl}"
state_dir="${HOME}/.neurodesk/runtime-voxl"
install -d -m 0700 "${state_dir}"

xstartup="${HOME}/.vnc/xstartup"
if [ ! -x "${xstartup}" ]; then
    xstartup="/opt/jovyan_defaults/.vnc/xstartup"
fi

# Guacamole probes and kills :1..:42, so voxl keeps to its own range.
display_num=""
for candidate in $(seq 60 99); do
    if [ -e "/tmp/.X11-unix/X${candidate}" ] || [ -e "/tmp/.X${candidate}-lock" ]; then
        continue
    fi
    if vncserver ":${candidate}" \
        -geometry 1280x720 -depth 24 -desktop "Neurodesktop (voxl)" \
        -localhost yes -rfbport -1 \
        -rfbunixpath "${state_dir}/rfb-${candidate}.sock" -rfbunixmode 0600 \
        -SecurityTypes None \
        -xstartup "${xstartup}" >"${state_dir}/vncserver.log" 2>&1; then
        display_num="${candidate}"
        break
    fi
done
if [ -z "${display_num}" ]; then
    echo "[ERROR] Could not start an X display for voxl (tried :60 to :99)." >&2
    cat "${state_dir}/vncserver.log" >&2 || true
    exit 1
fi

voxl_pid=""
cleanup() {
    if [ -n "${voxl_pid}" ]; then
        kill "${voxl_pid}" 2>/dev/null || true
        wait "${voxl_pid}" 2>/dev/null || true
    fi
    vncserver -kill ":${display_num}" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 143' TERM INT HUP

export DISPLAY=":${display_num}"
xset s off >/dev/null 2>&1 || true

# The Host header is the browser's Jupyter host, which voxl cannot know in
# advance. The Bearer token, not the host name, is what admits a request.
"${voxl_bin}" \
    --addr 127.0.0.1 \
    --port "${port}" \
    --title "Neurodesktop" \
    --allowed-hosts '*' \
    --enable-resize \
    --audio-enabled=false \
    --file-transfers none &
voxl_pid=$!
wait "${voxl_pid}"
