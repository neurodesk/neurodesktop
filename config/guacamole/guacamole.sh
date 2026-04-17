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

# Ensure per-user Guacamole config and credentials exist. init_secrets.sh is
# idempotent - guacamole.sh invokes it defensively in case the container was
# started in a path that skipped the jupyterlab_startup hook.
export GUACAMOLE_HOME="${GUACAMOLE_HOME:-${HOME}/.neurodesk/guacamole}"
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

# Per-user CATALINA_BASE. server.xml gets the Tomcat port stamped in directly -
# property-substitution via -Dport.http=... has proven unreliable across builds.
CATALINA_BASE_PER_USER="${HOME}/.neurodesk/tomcat"
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

NEURODESKTOP_RUNTIME_DIR="${HOME}/.neurodesk/runtime"
mkdir -p "${NEURODESKTOP_RUNTIME_DIR}" 2>/dev/null || true
printf '%s\n' "${NEURODESKTOP_TOMCAT_PORT}" > "${NEURODESKTOP_RUNTIME_DIR}/tomcat_port" 2>/dev/null || true
printf '%s\n' "${NEURODESKTOP_GUACD_PORT}" > "${NEURODESKTOP_RUNTIME_DIR}/guacd_port" 2>/dev/null || true

# --------------------------------------------------------------------------
# SSH keys & authorized_keys (needed for the Guacamole SSH/SFTP connection
# private-key block embedded in user-mapping.xml).
# --------------------------------------------------------------------------

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"
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

# RDP. ensure_rdp_backend.sh writes the chosen port to runtime/rdp_port.
_rdp_backend_ok=0
if [ -x /opt/neurodesktop/ensure_rdp_backend.sh ]; then
    if /opt/neurodesktop/ensure_rdp_backend.sh; then
        _rdp_backend_ok=1
    else
        echo "[WARN] Failed to initialize RDP backend for Guacamole."
    fi
else
    echo "[WARN] /opt/neurodesktop/ensure_rdp_backend.sh not found."
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

# SSH/SFTP.
if [ -x /opt/neurodesktop/ensure_sftp_sshd.sh ]; then
    /opt/neurodesktop/ensure_sftp_sshd.sh || \
        echo "[WARN] Failed to initialize SSH/SFTP service for Guacamole."
else
    echo "[WARN] /opt/neurodesktop/ensure_sftp_sshd.sh not found."
fi
if [ -f "${NEURODESKTOP_RUNTIME_DIR}/sftp_port" ]; then
    NEURODESKTOP_SFTP_PORT="$(cat "${NEURODESKTOP_RUNTIME_DIR}/sftp_port" 2>/dev/null || true)"
fi
if [ -n "${NEURODESKTOP_SFTP_PORT:-}" ]; then
    update_mapping_param "vnc" "sftp-port" "${NEURODESKTOP_SFTP_PORT}" || \
        echo "[WARN] Could not stamp SFTP port ${NEURODESKTOP_SFTP_PORT} into mapping."
fi

# VNC.
DISPLAY_NUM=1
MAX_DISPLAY=42

echo "[DEBUG] VNC setup - checking prerequisites..."
echo "[DEBUG] HOME=${HOME}"
echo "[DEBUG] Contents of ${HOME}/.vnc:"
ls -la "${HOME}/.vnc/" 2>&1 || echo "[DEBUG] .vnc directory does not exist!"

mkdir -p "${HOME}/.vnc"
if [ -n "${NEURODESKTOP_VNC_PASSWORD:-}" ]; then
    if vncpasswd -f < /dev/null >/dev/null 2>&1; then
        /usr/bin/printf '%s\n' "${NEURODESKTOP_VNC_PASSWORD}" | vncpasswd -f > "${HOME}/.vnc/passwd"
    else
        /usr/bin/printf '%s\n%s\n%s\n' "${NEURODESKTOP_VNC_PASSWORD}" "${NEURODESKTOP_VNC_PASSWORD}" 'n' | vncpasswd
    fi
    chmod 600 "${HOME}/.vnc/passwd" 2>/dev/null || true
else
    echo "[ERROR] NEURODESKTOP_VNC_PASSWORD is empty - init_secrets.sh did not run?" >&2
    exit 1
fi

if [ ! -f "${HOME}/.vnc/xstartup" ]; then
    echo "[ERROR] VNC xstartup not found at ${HOME}/.vnc/xstartup"
    echo "[DEBUG] Creating xstartup..."
    printf '%s\n' '#!/bin/sh' '/usr/bin/startlxde' 'vncconfig -nowin -noiconic &' > "${HOME}/.vnc/xstartup"
    chmod +x "${HOME}/.vnc/xstartup"
fi

while [ $DISPLAY_NUM -le $MAX_DISPLAY ]; do
    vncserver -kill :${DISPLAY_NUM} 2>/dev/null
    echo "[DEBUG] Attempting to start VNC on display :${DISPLAY_NUM}..."
    # Note: we intentionally do NOT pass -SecurityTypes here. TigerVNC's default
    # list all require a password; restricting to VncAuth only has broken
    # compatibility with some libguac-client-vnc builds that negotiate a
    # TLS-wrapped variant first. -localhost yes is what closes off off-box access.
    VNC_OUTPUT=$(vncserver -geometry 1280x720 -depth 24 -name "VNC" \
        -localhost yes :${DISPLAY_NUM} 2>&1)
    VNC_EXIT=$?
    echo "[DEBUG] vncserver exit code: ${VNC_EXIT}"
    echo "[DEBUG] vncserver output: ${VNC_OUTPUT}"
    if [ $VNC_EXIT -eq 0 ]; then
        echo "VNC server started on display :${DISPLAY_NUM}"
        break
    fi
    echo "Display :${DISPLAY_NUM} unavailable, trying next..."
    DISPLAY_NUM=$((DISPLAY_NUM + 1))
done

if [ $DISPLAY_NUM -gt $MAX_DISPLAY ]; then
    echo "ERROR: Could not find available display (tried :1 to :${MAX_DISPLAY})"
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

# --------------------------------------------------------------------------
# Tomcat. Now that every backend port + password is in the mapping, start the
# Guacamole webapp. It reads user-mapping.xml on init and from here on can
# route client connections to real listeners.
# --------------------------------------------------------------------------

/usr/local/tomcat/bin/startup.sh

_tomcat_ready=0
for _ in $(seq 1 30); do
    if ss -lnt 2>/dev/null | awk 'NR>1 {print $4}' | grep -Eq "(^|:)${NEURODESKTOP_TOMCAT_PORT}$"; then
        _tomcat_ready=1
        break
    fi
    sleep 1
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

# Guacamole daemon. -b 127.0.0.1 keeps guacd unreachable off-host; -l picks a
# per-user port so two users on a shared Apptainer netns do not fight over 4822.
guacd -b 127.0.0.1 -l "${NEURODESKTOP_GUACD_PORT}"
echo "    Running guacamole"

RUNTIME_LABEL="docker"
if is_apptainer_runtime; then
    RUNTIME_LABEL="apptainer"
fi
echo "[INFO] Neurodesktop session ports (${RUNTIME_LABEL}):"
echo "[INFO]   Tomcat/Guacamole HTTP : ${NEURODESKTOP_TOMCAT_PORT}"
echo "[INFO]   guacd                 : 127.0.0.1:${NEURODESKTOP_GUACD_PORT}"
echo "[INFO]   VNC display / port    : :${DISPLAY_NUM} / ${VNC_PORT} (localhost-only, random password)"
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
