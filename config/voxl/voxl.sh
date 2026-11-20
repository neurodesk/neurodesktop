#!/usr/bin/env bash
# Stream a private LXDE desktop to the browser with voxl.
#
# Jupyter Server Proxy starts this script with a free loopback port and the
# per-launch token in VOXL_PASSWORD; it sends the same token as a Bearer header
# on every proxied request, so other users on a shared host cannot reach the
# stream without it.
#
# The desktop is a dedicated Xvfb started with -shmem, so voxl reads the
# framebuffer in place instead of copying it out through the X server. Xvfb
# creates that segment world-accessible; neurodesktop-shm-private restricts it
# to its owner and refuses it if anyone else attached first.
set -euo pipefail

port="${1:?usage: voxl.sh PORT}"
if [ -z "${VOXL_PASSWORD:-}" ]; then
    echo "[ERROR] VOXL_PASSWORD is empty; start voxl through Jupyter Server Proxy." >&2
    exit 1
fi

# Desktop pixels per CSS pixel of the viewer's window. voxl's own default of 1.5
# assumes a session drawn at 144 DPI; the LXDE session draws at 96 DPI, so 1
# shows applications at the browser's size. A viewer can override it with
# ?scale= in the page URL.
resize_scale="${NEURODESKTOP_VOXL_RESIZE_SCALE:-1}"
if ! [[ "${resize_scale}" =~ ^[0-9]*\.?[0-9]+$ && "${resize_scale}" =~ [1-9] ]]; then
    echo "[ERROR] NEURODESKTOP_VOXL_RESIZE_SCALE must be a positive number such as 1 or 2, not '${resize_scale}'." >&2
    exit 1
fi
# voxl lowers the resolution to fit the viewer; RandR cannot grow an Xvfb
# screen beyond the size it started with.
screen_size="${NEURODESKTOP_VOXL_SCREEN_SIZE:-3840x2160}"
if ! [[ "${screen_size}" =~ ^[1-9][0-9]*x[1-9][0-9]*$ ]]; then
    echo "[ERROR] NEURODESKTOP_VOXL_SCREEN_SIZE must be WIDTHxHEIGHT such as 3840x2160, not '${screen_size}'." >&2
    exit 1
fi

voxl_bin="${NEURODESKTOP_VOXL_BIN:-/usr/local/bin/voxl}"
# Built with voxl's XTEST scroll-axis patch, so scrolling is smooth.
xvfb_bin="${NEURODESKTOP_XVFB_BIN:-/usr/local/libexec/voxl/Xvfb}"
state_dir="${HOME}/.neurodesk/runtime-voxl"
install -d -m 0700 "${state_dir}"
xauthority="${XAUTHORITY:-${HOME}/.Xauthority}"
export XAUTHORITY="${xauthority}"

xstartup="${HOME}/.vnc/xstartup"
if [ ! -x "${xstartup}" ]; then
    xstartup="/opt/jovyan_defaults/.vnc/xstartup"
fi

display_num=""
framebuffer_ids=""
xvfb_pid=""
session_pid=""
voxl_pid=""
cleanup() {
    if [ -n "${voxl_pid}" ]; then
        kill "${voxl_pid}" 2>/dev/null || true
        wait "${voxl_pid}" 2>/dev/null || true
    fi
    if [ -n "${session_pid}" ]; then
        kill -- "-${session_pid}" 2>/dev/null || true
        # Do not remove the display while the desktop is still handling TERM.
        # A watchdog keeps a custom xstartup that ignores TERM from hanging the
        # proxy shutdown forever.
        (
            trap - EXIT TERM INT HUP
            sleep 5
            kill -KILL -- "-${session_pid}" 2>/dev/null || true
        ) &
        local session_watchdog_pid
        session_watchdog_pid=$!
        wait "${session_pid}" 2>/dev/null || true
        kill "${session_watchdog_pid}" 2>/dev/null || true
        wait "${session_watchdog_pid}" 2>/dev/null || true
    fi
    if [ -n "${xvfb_pid}" ]; then
        kill "${xvfb_pid}" 2>/dev/null || true
        wait "${xvfb_pid}" 2>/dev/null || true
    fi
    # Xvfb leaves its -shmem segment behind when it exits.
    local shmid
    for shmid in ${framebuffer_ids}; do
        ipcrm -m "${shmid}" 2>/dev/null || true
    done
    if [ -n "${display_num}" ]; then
        xauth remove ":${display_num}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'exit 143' TERM INT HUP

# Starts Xvfb on :$1 and waits until it reports, through -displayfd, that it
# accepts connections. Another server answering on that display cannot pass
# for ours. Returns 1 if Xvfb exits first, for example because another session
# holds the display.
start_xvfb() {
    local candidate="$1"
    local ready_file="${state_dir}/xvfb.displayfd"
    : >"${ready_file}"
    xauth add ":${candidate}" . "$(mcookie)"
    "${xvfb_bin}" ":${candidate}" -displayfd 3 -shmem -screen 0 "${screen_size}x24" -nolisten tcp \
        -auth "${xauthority}" 3>"${ready_file}" >"${state_dir}/xvfb.log" 2>&1 &
    xvfb_pid=$!
    display_num="${candidate}"
    local _
    for _ in $(seq 1 100); do
        if [ "$(tr -d '[:space:]' <"${ready_file}")" = "${candidate}" ]; then
            return 0
        fi
        if ! kill -0 "${xvfb_pid}" 2>/dev/null; then
            wait "${xvfb_pid}" 2>/dev/null || true
            xvfb_pid=""
            xauth remove ":${candidate}" >/dev/null 2>&1 || true
            display_num=""
            return 1
        fi
        sleep 0.1
    done
    echo "[ERROR] Xvfb on :${candidate} did not answer within 10 seconds." >&2
    cat "${state_dir}/xvfb.log" >&2 || true
    exit 1
}

# Guacamole probes and kills :1..:42, so voxl keeps to its own range.
for candidate in $(seq 60 99); do
    if [ -e "/tmp/.X11-unix/X${candidate}" ] || [ -e "/tmp/.X${candidate}-lock" ]; then
        continue
    fi
    if start_xvfb "${candidate}"; then
        break
    fi
done
if [ -z "${display_num}" ]; then
    echo "[ERROR] Could not start an X display for voxl (tried :60 to :99)." >&2
    cat "${state_dir}/xvfb.log" >&2 || true
    exit 1
fi

framebuffer_ids="$(awk -v pid="${xvfb_pid}" -v uid="$(id -u)" \
    'NR > 1 && $5 == pid && $8 == uid { print $2 }' /proc/sysvipc/shm)"
if ! neurodesktop-shm-private "${xvfb_pid}"; then
    echo "[ERROR] Could not make the Xvfb framebuffer on :${display_num} private; not streaming it." >&2
    exit 1
fi

export DISPLAY=":${display_num}"
xset s off -dpms >/dev/null 2>&1 || true
setsid "${xstartup}" >"${state_dir}/session.log" 2>&1 &
session_pid=$!

# The Host header is the browser's Jupyter host, which voxl cannot know in
# advance. The Bearer token, not the host name, is what admits a request.
"${voxl_bin}" \
    --addr 127.0.0.1 \
    --port "${port}" \
    --title "Neurodesktop" \
    --allowed-hosts '*' \
    --enable-resize \
    --resize-scale "${resize_scale}" \
    --audio-enabled=false \
    --file-transfers none &
voxl_pid=$!
wait "${voxl_pid}"
