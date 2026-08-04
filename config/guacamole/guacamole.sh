#!/bin/bash

# Phase timing helpers
_phase_start() { _PHASE_T0=$(date +%s%3N); echo "[TIMING] $1 started"; }
_phase_end()   { local elapsed=$(( $(date +%s%3N) - _PHASE_T0 )); echo "[TIMING] $1 completed in ${elapsed}ms"; }

_phase_start "guacamole-startup"

# -XX:UseSVE=0 only exists on aarch64 so only add it if it exists. Check using `uname -m`
if [ "$(uname -m)" == "aarch64" ]; then
    export JAVA_TOOL_OPTIONS="-XX:UseSVE=0"
fi

is_apptainer_runtime() {
    [ -n "${SINGULARITY_NAME:-}" ] || \
    [ -n "${APPTAINER_NAME:-}" ] || \
    [ -n "${APPTAINER_CONTAINER:-}" ] || \
    [ -n "${SINGULARITY_CONTAINER:-}" ] || \
    [ -n "${APPTAINER_COMMAND:-}" ] || \
    [ -n "${SINGULARITY_COMMAND:-}" ] || \
    [ -d "/.apptainer.d" ] || \
    [ -d "/.singularity.d" ]
}

NEURODESKTOP_DESKTOP_BACKEND="$(
    printf '%s' "${NEURODESKTOP_DESKTOP_BACKEND:-both}" | tr '[:upper:]' '[:lower:]'
)"
case "${NEURODESKTOP_DESKTOP_BACKEND}" in
    rdp)
        _start_rdp=1
        _start_vnc=0
        ;;
    vnc)
        _start_rdp=0
        _start_vnc=1
        ;;
    both|all)
        NEURODESKTOP_DESKTOP_BACKEND="both"
        _start_rdp=1
        _start_vnc=1
        ;;
    *)
        echo "[ERROR] Unsupported NEURODESKTOP_DESKTOP_BACKEND=${NEURODESKTOP_DESKTOP_BACKEND}. Use rdp, vnc, or both." >&2
        exit 1
        ;;
esac
export NEURODESKTOP_DESKTOP_BACKEND

HOME_DIR="${HOME:-/home/jovyan}"
_neurodesktop_state_root="${HOME_DIR}/.neurodesk"

# RDP and VNC are exposed as separate jupyter-server-proxy entries. Keep their
# Guacamole/Tomcat/runtime state separate so starting one backend cannot reuse
# the other backend's cached user-mapping.xml or Tomcat work directory.
if [ -z "${GUACAMOLE_HOME:-}" ]; then
    export GUACAMOLE_HOME="${_neurodesktop_state_root}/guacamole-${NEURODESKTOP_DESKTOP_BACKEND}"
else
    export GUACAMOLE_HOME
fi
CATALINA_BASE_PER_USER="${CATALINA_BASE:-${_neurodesktop_state_root}/tomcat-${NEURODESKTOP_DESKTOP_BACKEND}}"
export NEURODESKTOP_RUNTIME_DIR="${NEURODESKTOP_RUNTIME_DIR:-${_neurodesktop_state_root}/runtime-${NEURODESKTOP_DESKTOP_BACKEND}}"

# Ensure per-user Guacamole config and credentials exist. init_secrets.sh is
# idempotent - guacamole.sh invokes it defensively in case the container was
# started in a path that skipped the jupyterlab_startup hook.
if [ -x /opt/neurodesktop/init_secrets.sh ]; then
    # shellcheck disable=SC1091
    source /opt/neurodesktop/init_secrets.sh || {
        echo "[ERROR] init_secrets.sh failed - refusing to start Guacamole with defaults." >&2
        exit 1
    }
else
    echo "[ERROR] /opt/neurodesktop/init_secrets.sh is missing - cannot start Guacamole." >&2
    exit 1
fi

GUACAMOLE_MAPPING_FILE="${GUACAMOLE_HOME}/user-mapping.xml"
GUACAMOLE_WEB_PASSWORD_FILE="${HOME}/.neurodesk/secrets/guacamole_web_password"

# Probe for a free TCP port starting at $1, stepping by 1, up to N attempts.
find_free_tcp_port() {
    local start_port="$1"
    local max_attempts="${2:-50}"
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

# XML-escape a value so we can stamp arbitrary secrets into user-mapping.xml.
xml_escape() {
    local raw="$1"
    raw="${raw//&/&amp;}"
    raw="${raw//</&lt;}"
    raw="${raw//>/&gt;}"
    raw="${raw//\"/&quot;}"
    printf '%s' "${raw}"
}

# Rewrite a <param name="KEY">VALUE</param> inside the <connection> whose
# <protocol> matches. Fails non-zero if the target was not found.
update_mapping_param() {
    local protocol="$1"
    local key="$2"
    local value="$3"
    local tmp_mapping
    local value_escaped

    if [ ! -f "${GUACAMOLE_MAPPING_FILE}" ]; then
        echo "[ERROR] Guacamole mapping file not found at ${GUACAMOLE_MAPPING_FILE}" >&2
        return 1
    fi

    value_escaped="$(xml_escape "${value}")"

    tmp_mapping="$(mktemp /tmp/guacamole-user-mapping.XXXXXX)" || {
        echo "[ERROR] Failed to create temporary mapping file." >&2
        return 1
    }

    awk -v key="${key}" -v value="${value_escaped}" -v proto="${protocol}" '
        BEGIN { in_connection=0; is_match=0; updated=0 }
        {
            line=$0
            if ($0 ~ /<connection[[:space:]>]/) {
                in_connection=1
                is_match=0
            }
            if (in_connection && $0 ~ ("<protocol>[[:space:]]*" proto "[[:space:]]*</protocol>")) {
                is_match=1
            }
            if (in_connection && is_match && $0 ~ ("<param name=\"" key "\">[^<]*</param>") && !updated) {
                sub(("<param name=\"" key "\">[^<]*</param>"), ("<param name=\"" key "\">" value "</param>"), line)
                updated=1
            }
            print line
            if ($0 ~ /<\/connection>/) {
                in_connection=0
                is_match=0
                updated=0
            }
        }
    ' "${GUACAMOLE_MAPPING_FILE}" > "${tmp_mapping}"

    cat "${tmp_mapping}" > "${GUACAMOLE_MAPPING_FILE}"
    rm -f "${tmp_mapping}"

    if ! grep -q "<param name=\"${key}\">${value_escaped}</param>" "${GUACAMOLE_MAPPING_FILE}"; then
        echo "[ERROR] Failed to stamp ${protocol}/${key} into ${GUACAMOLE_MAPPING_FILE}" >&2
        return 1
    fi
    return 0
}

# --------------------------------------------------------------------------
# Port selection. Done up-front because we must stamp every port into
# user-mapping.xml BEFORE Tomcat starts - the Guacamole webapp caches the
# mapping when its servlet initialises, so any late mutation (RDP/SFTP/VNC
# port stamped after Tomcat was already serving requests) silently misses.
# --------------------------------------------------------------------------

# Tomcat port: honour NEURODESKTOP_TOMCAT_PORT from jupyter-server-proxy, else probe.
if [ -z "${NEURODESKTOP_TOMCAT_PORT:-}" ] || [ "${NEURODESKTOP_TOMCAT_PORT}" = "0" ]; then
    NEURODESKTOP_TOMCAT_PORT="$(find_free_tcp_port 8080 50 || true)"
fi
export NEURODESKTOP_TOMCAT_PORT

# guacd port: under Apptainer shared netns the default 4822 is taken by the
# first user to start.
if [ -z "${NEURODESKTOP_GUACD_PORT:-}" ] || [ "${NEURODESKTOP_GUACD_PORT}" = "0" ]; then
    NEURODESKTOP_GUACD_PORT="$(find_free_tcp_port 4822 50 || true)"
fi
export NEURODESKTOP_GUACD_PORT

GUACAMOLE_PROPERTIES_FILE="${GUACAMOLE_HOME}/guacamole.properties"
if [ -f "${GUACAMOLE_PROPERTIES_FILE}" ]; then
    sed -i -E "s|^guacd-port:.*|guacd-port: ${NEURODESKTOP_GUACD_PORT}|" "${GUACAMOLE_PROPERTIES_FILE}"
fi

# Per-backend CATALINA_BASE. server.xml gets the Tomcat port stamped in directly -
# property-substitution via -Dport.http=... has proven unreliable across builds.
mkdir -p \
    "${CATALINA_BASE_PER_USER}/conf" \
    "${CATALINA_BASE_PER_USER}/logs" \
    "${CATALINA_BASE_PER_USER}/temp" \
    "${CATALINA_BASE_PER_USER}/work" \
    "${CATALINA_BASE_PER_USER}/webapps" \
    2>/dev/null

cp -rfT /usr/local/tomcat/conf "${CATALINA_BASE_PER_USER}/conf" 2>/dev/null || \
    cp -rf /usr/local/tomcat/conf/. "${CATALINA_BASE_PER_USER}/conf/" 2>/dev/null || true

sed -i -E \
    "s|<Connector port=\"[^\"]+\" protocol=\"HTTP/1\.1\"|<Connector port=\"${NEURODESKTOP_TOMCAT_PORT}\" protocol=\"HTTP/1.1\"|" \
    "${CATALINA_BASE_PER_USER}/conf/server.xml"

# Disable the Tomcat shutdown port (default 8005): shared Apptainer netns
# collisions, and residual JVMs in rapid test teardown/startup, both break it.
sed -i -E \
    "s|<Server port=\"[0-9]+\"|<Server port=\"-1\"|" \
    "${CATALINA_BASE_PER_USER}/conf/server.xml"

if [ ! -e "${CATALINA_BASE_PER_USER}/webapps/ROOT" ]; then
    ln -sfn /usr/local/tomcat/webapps/ROOT "${CATALINA_BASE_PER_USER}/webapps/ROOT"
fi

export CATALINA_BASE="${CATALINA_BASE_PER_USER}"

mkdir -p "${NEURODESKTOP_RUNTIME_DIR}" 2>/dev/null || true
printf '%s\n' "${NEURODESKTOP_TOMCAT_PORT}" > "${NEURODESKTOP_RUNTIME_DIR}/tomcat_port" 2>/dev/null || true
printf '%s\n' "${NEURODESKTOP_GUACD_PORT}" > "${NEURODESKTOP_RUNTIME_DIR}/guacd_port" 2>/dev/null || true

# --------------------------------------------------------------------------
# Launch the slow, independent pieces in parallel:
#   - SSH keypair generation (usually already done at boot by
#     jupyterlab_startup.sh via ensure_ssh_keys.sh)
#   - xrdp backend (writes runtime/rdp_port; does not touch the mapping)
#   - Xvnc + desktop session (writes runtime/vnc_display; ditto)
# Every user-mapping.xml mutation stays in THIS shell, strictly sequential:
# update_mapping_param rewrites the whole file, so concurrent writers would
# silently lose each other's stamps.
# --------------------------------------------------------------------------

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"
_keygen_pid=""
if [ -x /opt/neurodesktop/ensure_ssh_keys.sh ]; then
    /opt/neurodesktop/ensure_ssh_keys.sh &
    _keygen_pid=$!
fi

NEURODESKTOP_RDP_PORT=""
_rdp_pid=""
if [ "${_start_rdp}" -eq 1 ]; then
    if [ -x /opt/neurodesktop/ensure_rdp_backend.sh ]; then
        /opt/neurodesktop/ensure_rdp_backend.sh &
        _rdp_pid=$!
    else
        echo "[WARN] /opt/neurodesktop/ensure_rdp_backend.sh not found."
    fi
fi

_vnc_pid=""
if [ "${_start_vnc}" -eq 1 ]; then
    echo "[DEBUG] VNC setup - checking prerequisites..."
    echo "[DEBUG] HOME=${HOME}"
    echo "[DEBUG] Contents of ${HOME}/.vnc:"
    ls -la "${HOME}/.vnc/" 2>&1 || echo "[DEBUG] .vnc directory does not exist!"

    mkdir -p "${HOME}/.vnc"
    if [ -z "${NEURODESKTOP_VNC_PASSWORD:-}" ]; then
        echo "[ERROR] NEURODESKTOP_VNC_PASSWORD is empty - init_secrets.sh did not run?" >&2
        exit 1
    fi

    # Stamp ${HOME}/.vnc/passwd with the rotated secret. `vncpasswd -f` reads the
    # plaintext from stdin and writes the DES-obfuscated 8-byte file Xvnc expects.
    #
    # Do NOT "feature-check" `-f` with `vncpasswd -f < /dev/null` - an empty
    # stdin returns non-zero even when -f is supported, pushing us into the
    # interactive fallback. That fallback CANNOT WORK: vncpasswd reads via
    # getpass() which reads from /dev/tty, not stdin, so piping produces a
    # garbage hash. The symptom is CLIENT_UNAUTHORIZED (Guacamole status 0x0301
    # / error code 769) because the garbage hash does not match the plaintext
    # stamped into user-mapping.xml. Just try -f with real input and check the
    # output size.
    _vnc_passwd_tmp="${HOME}/.vnc/passwd.tmp.$$"
    umask 077
    if /usr/bin/printf '%s\n' "${NEURODESKTOP_VNC_PASSWORD}" | vncpasswd -f > "${_vnc_passwd_tmp}" 2>/dev/null \
        && [ -s "${_vnc_passwd_tmp}" ]; then
        mv -f "${_vnc_passwd_tmp}" "${HOME}/.vnc/passwd"
    else
        rm -f "${_vnc_passwd_tmp}" 2>/dev/null || true
        echo "[ERROR] vncpasswd -f failed; Xvnc will reject Guacamole connections." >&2
        echo "[ERROR] TigerVNC's interactive vncpasswd uses getpass() from /dev/tty and" >&2
        echo "[ERROR] cannot be fed via a pipe, so we refuse to fall back to it here." >&2
        exit 1
    fi
    chmod 600 "${HOME}/.vnc/passwd" 2>/dev/null || true
    unset _vnc_passwd_tmp

    if [ ! -f "${HOME}/.vnc/xstartup" ]; then
        echo "[ERROR] VNC xstartup not found at ${HOME}/.vnc/xstartup"
        echo "[DEBUG] Creating xstartup..."
        printf '%s\n' '#!/bin/sh' 'eval "$(dbus-launch --sh-syntax)"' 'export DBUS_SESSION_BUS_ADDRESS' '/usr/bin/startlxde' 'vncconfig -nowin -noiconic &' > "${HOME}/.vnc/xstartup"
        chmod +x "${HOME}/.vnc/xstartup"
    fi

    # Probe displays :1..:42 and publish the winning display number so the
    # main shell can stamp the mapping once the server is up.
    start_vnc_server() {
        local display_num=1
        local max_display=42
        local vnc_output vnc_exit
        while [ ${display_num} -le ${max_display} ]; do
            vncserver -kill :${display_num} 2>/dev/null
            echo "[DEBUG] Attempting to start VNC on display :${display_num}..."
            # Note: we intentionally do NOT pass -SecurityTypes here. TigerVNC's default
            # list all require a password; restricting to VncAuth only has broken
            # compatibility with some libguac-client-vnc builds that negotiate a
            # TLS-wrapped variant first. -localhost yes is what closes off off-box access.
            vnc_output=$(vncserver -geometry 1280x720 -depth 24 -name "VNC" \
                -localhost yes :${display_num} 2>&1)
            vnc_exit=$?
            echo "[DEBUG] vncserver exit code: ${vnc_exit}"
            echo "[DEBUG] vncserver output: ${vnc_output}"
            if [ ${vnc_exit} -eq 0 ]; then
                echo "VNC server started on display :${display_num}"
                printf '%s\n' "${display_num}" > "${NEURODESKTOP_RUNTIME_DIR}/vnc_display"
                return 0
            fi
            echo "Display :${display_num} unavailable, trying next..."
            display_num=$((display_num + 1))
        done
        echo "ERROR: Could not find available display (tried :1 to :${max_display})"
        return 1
    }

    rm -f "${NEURODESKTOP_RUNTIME_DIR}/vnc_display" 2>/dev/null || true
    start_vnc_server &
    _vnc_pid=$!
fi

# --------------------------------------------------------------------------
# SSH keys & authorized_keys (needed for the Guacamole SSH/SFTP connection
# private-key block embedded in user-mapping.xml).
# --------------------------------------------------------------------------

if [ -n "${_keygen_pid}" ]; then
    wait "${_keygen_pid}" || echo "[WARN] ensure_ssh_keys.sh failed; falling back to inline key generation."
fi
# No-ops when ensure_ssh_keys.sh already generated them.
if [ ! -f "${HOME}/.ssh/guacamole_rsa" ]; then
    ssh-keygen -q -t rsa -f "${HOME}/.ssh/guacamole_rsa" -b 4096 -m PEM -N '' -C "guacamole@sftp-server"
fi
if [ ! -f "${HOME}/.ssh/id_rsa" ]; then
    ssh-keygen -q -t rsa -f "${HOME}/.ssh/id_rsa" -b 4096 -m PEM -N ''
fi

AUTHORIZED_KEYS_FILE="${HOME}/.ssh/authorized_keys"
touch "$AUTHORIZED_KEYS_FILE"
chmod 600 "$AUTHORIZED_KEYS_FILE"
if ! grep -qF "${NB_USER}@${HOSTNAME}" "$AUTHORIZED_KEYS_FILE" 2>/dev/null; then
    cat "${HOME}/.ssh/id_rsa.pub" >> "$AUTHORIZED_KEYS_FILE"
fi

if [ -f "${HOME}/.ssh/sshd_config" ]; then
    sed -i "s|/home/jovyan|${HOME}|g" "${HOME}/.ssh/sshd_config"
fi

if ! grep 'BEGIN RSA PRIVATE KEY' "${GUACAMOLE_MAPPING_FILE}" 2>/dev/null; then
    sed -i "/private-key/ r ${HOME}/.ssh/guacamole_rsa" "${GUACAMOLE_MAPPING_FILE}"
fi

# --------------------------------------------------------------------------
# Backend services. Start BEFORE Tomcat so every port/password stamp in
# user-mapping.xml is final when the Guacamole webapp reads the file.
# --------------------------------------------------------------------------

# Strip any <connection> block whose <protocol> matches $1 from user-mapping.xml.
# Used when the corresponding backend could not be brought up, so Guacamole does
# not advertise a connection that would return "500 Internal Server Error" when
# clicked.
remove_mapping_connection() {
    local protocol="$1"
    local tmp_mapping

    if [ ! -f "${GUACAMOLE_MAPPING_FILE}" ]; then
        return 0
    fi

    tmp_mapping="$(mktemp /tmp/guacamole-user-mapping.XXXXXX)" || return 1
    awk -v proto="${protocol}" '
        BEGIN { buf=""; in_connection=0; is_match=0 }
        {
            if ($0 ~ /<connection[[:space:]>]/) {
                in_connection=1
                is_match=0
                buf=$0 ORS
                next
            }
            if (in_connection) {
                buf=buf $0 ORS
                if ($0 ~ ("<protocol>[[:space:]]*" proto "[[:space:]]*</protocol>")) {
                    is_match=1
                }
                if ($0 ~ /<\/connection>/) {
                    if (!is_match) {
                        printf "%s", buf
                    }
                    buf=""; in_connection=0; is_match=0
                }
                next
            }
            print
        }
    ' "${GUACAMOLE_MAPPING_FILE}" > "${tmp_mapping}"
    cat "${tmp_mapping}" > "${GUACAMOLE_MAPPING_FILE}"
    rm -f "${tmp_mapping}"
}

# RDP. ensure_rdp_backend.sh was started in parallel above and writes the
# chosen port to runtime/rdp_port.
if [ "${_start_rdp}" -eq 1 ]; then
    _rdp_backend_ok=0
    if [ -n "${_rdp_pid}" ]; then
        if wait "${_rdp_pid}"; then
            _rdp_backend_ok=1
        else
            echo "[WARN] Failed to initialize RDP backend for Guacamole."
        fi
    fi
    if [ "${_rdp_backend_ok}" -eq 1 ] && [ -f "${NEURODESKTOP_RUNTIME_DIR}/rdp_port" ]; then
        NEURODESKTOP_RDP_PORT="$(cat "${NEURODESKTOP_RUNTIME_DIR}/rdp_port" 2>/dev/null || true)"
    fi
    if [ "${_rdp_backend_ok}" -eq 1 ] && [ -n "${NEURODESKTOP_RDP_PORT:-}" ]; then
        update_mapping_param "rdp" "port" "${NEURODESKTOP_RDP_PORT}" || \
            echo "[WARN] Could not stamp RDP port ${NEURODESKTOP_RDP_PORT} into mapping."
    else
        # No working xrdp (e.g. unprivileged container on HPC) - drop the RDP
        # connection from the mapping so the Guacamole UI does not list a dead
        # desktop that errors out when a user clicks it.
        remove_mapping_connection "rdp" || \
            echo "[WARN] Could not remove RDP connection from ${GUACAMOLE_MAPPING_FILE}."
        unset NEURODESKTOP_RDP_PORT
    fi
else
    echo "[INFO] NEURODESKTOP_DESKTOP_BACKEND=${NEURODESKTOP_DESKTOP_BACKEND}: skipping RDP backend."
    remove_mapping_connection "rdp" || \
        echo "[WARN] Could not remove RDP connection from ${GUACAMOLE_MAPPING_FILE}."
    unset NEURODESKTOP_RDP_PORT
fi

# SSH/SFTP. ensure_sftp_sshd.sh now only publishes runtime/sftp_port if sshd
# actually bound the port - an unconditional read on failure would leave us
# stamping a dead port into user-mapping.xml, which Guacamole then tries to
# dial as an SFTP side-channel at VNC connect time and aborts the whole tunnel
# with CLIENT_UNAUTHORIZED (0x0301).
NEURODESKTOP_SFTP_PORT=""
if [ "${_start_vnc}" -eq 1 ]; then
    if [ -x /opt/neurodesktop/ensure_sftp_sshd.sh ]; then
        /opt/neurodesktop/ensure_sftp_sshd.sh || \
            echo "[WARN] Failed to initialize SSH/SFTP service for Guacamole."
    else
        echo "[WARN] /opt/neurodesktop/ensure_sftp_sshd.sh not found."
    fi
    if [ -f "${NEURODESKTOP_RUNTIME_DIR}/sftp_port" ]; then
        NEURODESKTOP_SFTP_PORT="$(cat "${NEURODESKTOP_RUNTIME_DIR}/sftp_port" 2>/dev/null || true)"
    fi

    # Decide whether the SFTP side-channel can actually authenticate. libguac
    # aborts the WHOLE VNC tunnel (upstream error 0x0203 / 515) when its
    # SFTP pubkey auth fails, even if VNC auth itself succeeded. The three
    # failure modes we must gate on:
    #   1. sshd never bound a port (NEURODESKTOP_SFTP_PORT empty).
    #   2. NB_USER is not resolvable via NSS (Apptainer on HPC: NB_USER=jovyan
    #      is inherited from the image, but the host UID has no /etc/passwd
    #      entry). sshd cannot pubkey-auth a user it cannot look up.
    #   3. Current process UID != NB_USER's UID. sshd runs as the process UID
    #      and cannot accept a different login name without root.
    _sftp_nb_user="${NB_USER:-jovyan}"
    _sftp_ok=0
    if [ -n "${NEURODESKTOP_SFTP_PORT:-}" ] \
        && id -u "${_sftp_nb_user}" >/dev/null 2>&1 \
        && [ "$(id -u)" = "$(id -u "${_sftp_nb_user}" 2>/dev/null)" ]; then
        _sftp_ok=1
    fi

    if [ "${_sftp_ok}" -eq 1 ]; then
        update_mapping_param "vnc" "sftp-port" "${NEURODESKTOP_SFTP_PORT}" || \
            echo "[WARN] Could not stamp SFTP port ${NEURODESKTOP_SFTP_PORT} into mapping."
        update_mapping_param "vnc" "sftp-username" "${_sftp_nb_user}" || \
            echo "[WARN] Could not stamp SFTP username ${_sftp_nb_user} into mapping."
        if [ "${_start_rdp}" -eq 1 ]; then
            update_mapping_param "rdp" "sftp-username" "${_sftp_nb_user}" || true
        fi
    else
        # Disable SFTP entirely so Guacamole does not attempt the side-channel.
        # enable-sftp=false is the idiomatic way to turn off the feature in the
        # libguac-client-vnc connection params.
        echo "[WARN] SFTP side-channel unavailable (port=${NEURODESKTOP_SFTP_PORT:-unset}," \
             "nb_user=${_sftp_nb_user}, resolvable=$(id -u "${_sftp_nb_user}" 2>/dev/null || echo no)," \
             "cur_uid=$(id -u)); disabling enable-sftp in mapping."
        update_mapping_param "vnc" "enable-sftp" "false" || true
        if [ "${_start_rdp}" -eq 1 ]; then
            update_mapping_param "rdp" "enable-sftp" "false" || true
        fi
    fi
    unset _sftp_nb_user _sftp_ok
else
    echo "[INFO] NEURODESKTOP_DESKTOP_BACKEND=${NEURODESKTOP_DESKTOP_BACKEND}: skipping VNC SFTP side-channel."
fi

# VNC. The server was started in parallel above; wait for it and stamp the
# published display's port + rotated password into the mapping.
if [ "${_start_vnc}" -eq 1 ]; then
    DISPLAY_NUM=""
    if [ -n "${_vnc_pid}" ] && wait "${_vnc_pid}"; then
        DISPLAY_NUM="$(cat "${NEURODESKTOP_RUNTIME_DIR}/vnc_display" 2>/dev/null || true)"
    fi

    if [ -z "${DISPLAY_NUM}" ]; then
        echo "ERROR: VNC server failed to start (see [DEBUG] output above)."
        exit 1
    fi

    export DISPLAY=:${DISPLAY_NUM}
    VNC_PORT=$((5900 + DISPLAY_NUM))
    export NEURODESKTOP_VNC_PORT="${VNC_PORT}"

    # Stamp the live VNC port + password into the mapping NOW, before Tomcat reads
    # it. Failures are fatal: if we can't write the per-user port/password the
    # browser session would silently connect to whichever VNC happens to be
    # listening on the hardcoded default - exactly the cross-user leak we aim to
    # prevent, and also the specific symptom of the 'VNC desktop doesn't work'
    # regression where Guacamole cached the template port (5901) and vncserver
    # landed on :2 / 5902.
    update_mapping_param "vnc" "port" "${VNC_PORT}" || exit 1
    update_mapping_param "vnc" "password" "${NEURODESKTOP_VNC_PASSWORD}" || exit 1

    xset -display :${DISPLAY_NUM} s off || true
else
    echo "[INFO] NEURODESKTOP_DESKTOP_BACKEND=${NEURODESKTOP_DESKTOP_BACKEND}: skipping VNC backend."
    remove_mapping_connection "vnc" || \
        echo "[WARN] Could not remove VNC connection from ${GUACAMOLE_MAPPING_FILE}."
    unset NEURODESKTOP_VNC_PORT VNC_PORT DISPLAY_NUM
fi

# --------------------------------------------------------------------------
# Tomcat. Now that every backend port + password is in the mapping, start the
# Guacamole webapp. It reads user-mapping.xml on init and from here on can
# route client connections to real listeners.
# --------------------------------------------------------------------------

/usr/local/tomcat/bin/startup.sh

# Guacamole daemon, started while Tomcat is still deploying - guacd forks and
# binds in milliseconds and only needs to be up before the first client
# connection. -b 127.0.0.1 keeps guacd unreachable off-host; -l picks a
# per-user port so two users on a shared Apptainer netns do not fight over 4822.
guacd -b 127.0.0.1 -l "${NEURODESKTOP_GUACD_PORT}"
echo "    Running guacamole"

_tomcat_ready=0
for _ in $(seq 1 120); do
    if ss -lnt 2>/dev/null | awk 'NR>1 {print $4}' | grep -Eq "(^|:)${NEURODESKTOP_TOMCAT_PORT}$"; then
        _tomcat_ready=1
        break
    fi
    sleep 0.25
done
if [ "${_tomcat_ready}" -ne 1 ]; then
    echo "[ERROR] Tomcat did not bind 127.0.0.1:${NEURODESKTOP_TOMCAT_PORT} within 30s." >&2
    echo "[ERROR] CATALINA_BASE=${CATALINA_BASE_PER_USER}" >&2
    if [ -f "${CATALINA_BASE_PER_USER}/conf/server.xml" ]; then
        echo "[ERROR] ---- server.xml Connector lines ----" >&2
        grep -n 'Connector' "${CATALINA_BASE_PER_USER}/conf/server.xml" >&2 || true
    fi
    if [ -f "${CATALINA_BASE_PER_USER}/logs/catalina.out" ]; then
        echo "[ERROR] ---- catalina.out tail ----" >&2
        tail -n 80 "${CATALINA_BASE_PER_USER}/logs/catalina.out" >&2
    fi
fi

RUNTIME_LABEL="docker"
if is_apptainer_runtime; then
    RUNTIME_LABEL="apptainer"
fi
echo "[INFO] Neurodesktop session ports (${RUNTIME_LABEL}):"
echo "[INFO]   Backend mode           : ${NEURODESKTOP_DESKTOP_BACKEND}"
echo "[INFO]   Tomcat/Guacamole HTTP : ${NEURODESKTOP_TOMCAT_PORT}"
echo "[INFO]   guacd                 : 127.0.0.1:${NEURODESKTOP_GUACD_PORT}"
if [ -n "${DISPLAY:-}" ]; then
    echo "[INFO]   VNC display / port    : ${DISPLAY} / ${VNC_PORT:-unset} (localhost-only, random password)"
else
    echo "[INFO]   VNC display / port    : unset"
fi
echo "[INFO]   RDP port              : ${NEURODESKTOP_RDP_PORT:-unset}"
echo "[INFO]   SFTP port             : ${NEURODESKTOP_SFTP_PORT:-unset}"
echo "[INFO]   Guacamole web login   : ${NEURODESKTOP_GUACAMOLE_USER:-jovyan} (password rotated, see ${GUACAMOLE_WEB_PASSWORD_FILE})"
if [ "${RUNTIME_LABEL}" = "apptainer" ] && [ -n "${NEURODESKTOP_RDP_PORT:-}" ]; then
    echo "[INFO] Apptainer note: xrdp uses a shared host service and the OS password,"
    echo "[INFO] so if RDP is required on a multi-user compute node, users should either"
    echo "[INFO] rely on the (random-password) VNC path instead or coordinate xrdp ports"
    echo "[INFO] with their HPC admin."
fi

_phase_end "guacamole-startup"
