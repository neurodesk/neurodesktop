#!/bin/bash

# -XX:UseSVE=0 only exists on aarch64 so only add it if it exists. Check using `uname -m`
if [ "$(uname -m)" == "aarch64" ]; then
    export JAVA_TOOL_OPTIONS="-XX:UseSVE=0"
fi

GUACAMOLE_MAPPING_FILE="/etc/guacamole/user-mapping.xml"

update_guacamole_vnc_port() {
    local vnc_port="$1"
    local tmp_mapping

    if [ ! -f "${GUACAMOLE_MAPPING_FILE}" ]; then
        echo "[WARN] Guacamole mapping file not found at ${GUACAMOLE_MAPPING_FILE}"
        return 1
    fi

    tmp_mapping="$(mktemp /tmp/guacamole-user-mapping.XXXXXX)" || {
        echo "[WARN] Failed to create temporary mapping file."
        return 1
    }

    if awk -v vnc_port="${vnc_port}" '
        BEGIN { in_connection=0; is_vnc=0; updated=0 }
        {
            line=$0
            if ($0 ~ /<connection[[:space:]>]/) {
                in_connection=1
                is_vnc=0
            }
            if (in_connection && $0 ~ /<protocol>[[:space:]]*vnc[[:space:]]*<\/protocol>/) {
                is_vnc=1
            }
            if (in_connection && is_vnc && $0 ~ /<param name="port">[0-9]+<\/param>/ && !updated) {
                sub(/<param name="port">[0-9]+<\/param>/, "<param name=\"port\">" vnc_port "</param>", line)
                updated=1
            }
            print line
            if ($0 ~ /<\/connection>/) {
                in_connection=0
                is_vnc=0
            }
        }
        END { exit(updated ? 0 : 1) }
    ' "${GUACAMOLE_MAPPING_FILE}" > "${tmp_mapping}"; then
        cat "${tmp_mapping}" > "${GUACAMOLE_MAPPING_FILE}"
        rm -f "${tmp_mapping}"
        echo "[INFO] Updated Guacamole VNC port to ${vnc_port} in ${GUACAMOLE_MAPPING_FILE}"
        return 0
    fi

    rm -f "${tmp_mapping}"
    echo "[WARN] Failed to update VNC port in ${GUACAMOLE_MAPPING_FILE}; Guacamole may still point to 5901."
    return 1
}

# # Tomcat
# if sudo -n true 2>/dev/null; then
#     sudo --preserve-env=JAVA_TOOL_OPTIONS /usr/local/tomcat/bin/startup.sh
# fi
/usr/local/tomcat/bin/startup.sh

# RDP
if sudo -n true 2>/dev/null; then
    sudo service xrdp start
fi

# SSH/SFTP
if [ -x /opt/neurodesktop/ensure_sftp_sshd.sh ]; then
    /opt/neurodesktop/ensure_sftp_sshd.sh || \
        echo "[WARN] Failed to initialize SSH/SFTP service for Guacamole."
else
    echo "[WARN] /opt/neurodesktop/ensure_sftp_sshd.sh not found."
fi

# VNC - find available display by trying to start vncserver.
# This is needed for running on HPC where multiple users may be running VNC sessions
DISPLAY_NUM=1
MAX_DISPLAY=42

echo "[DEBUG] VNC setup - checking prerequisites..."
echo "[DEBUG] HOME=${HOME}"
echo "[DEBUG] Contents of ${HOME}/.vnc:"
ls -la "${HOME}/.vnc/" 2>&1 || echo "[DEBUG] .vnc directory does not exist!"

if [ ! -f "${HOME}/.vnc/passwd" ]; then
    echo "[ERROR] VNC password file not found at ${HOME}/.vnc/passwd"
    echo "[DEBUG] Attempting to generate VNC password..."
    mkdir -p "${HOME}/.vnc"
    /usr/bin/printf '%s\n%s\n%s\n' 'password' 'password' 'n' | vncpasswd
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
    VNC_OUTPUT=$(vncserver -geometry 1280x720 -depth 24 -name "VNC" :${DISPLAY_NUM} 2>&1)
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
update_guacamole_vnc_port "${VNC_PORT}" || true
xset -display :${DISPLAY_NUM} s off || true

# Guacamole
# sudo service guacd start
guacd -b 127.0.0.1
echo "    Running guacamole"
