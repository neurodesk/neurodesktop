#!/bin/bash

# -XX:UseSVE=0 only exists on aarch64 so only add it if it exists. Check using `uname -m`
if [ "$(uname -m)" == "aarch64" ]; then
    export JAVA_TOOL_OPTIONS="-XX:UseSVE=0"
fi

# # Tomcat
# if sudo -n true 2>/dev/null; then
#     sudo --preserve-env=JAVA_TOOL_OPTIONS /usr/local/tomcat/bin/startup.sh
# fi
/usr/local/tomcat/bin/startup.sh

# RDP
if sudo -n true 2>/dev/null; then
    sudo service xrdp start
fi

# SSH/SFTP - ensure config exists
if [ ! -f "${HOME}/.ssh/sshd_config" ]; then
    echo "[WARN] SSH config not found, copying from defaults..."
    mkdir -p "${HOME}/.ssh"
    if [ -f "/opt/jovyan_defaults/.ssh/sshd_config" ]; then
        cp /opt/jovyan_defaults/.ssh/sshd_config "${HOME}/.ssh/sshd_config"
        sed -i "s|/home/jovyan|${HOME}|g" "${HOME}/.ssh/sshd_config"
    else
        echo "[ERROR] Default sshd_config not found!"
    fi
fi
if [ -f "${HOME}/.ssh/sshd_config" ]; then
    /usr/sbin/sshd -f "${HOME}/.ssh/sshd_config"
else
    echo "[ERROR] Cannot start SSH - config file missing"
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
fi

export DISPLAY=:${DISPLAY_NUM}
xset -display :${DISPLAY_NUM} s off

# Guacamole
# sudo service guacd start
guacd -b 127.0.0.1
echo "    Running guacamole"
