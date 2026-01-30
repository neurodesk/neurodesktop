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

# SSH/SFTP
/usr/sbin/sshd -f ${HOME}/.ssh/sshd_config

# VNC - find available display by trying to start vncserver. 
# This is needed for running on HPC where multiple users may be running VNC sessions
DISPLAY_NUM=1
MAX_DISPLAY=42

while [ $DISPLAY_NUM -le $MAX_DISPLAY ]; do
    vncserver -kill :${DISPLAY_NUM} 2>/dev/null
    if vncserver -geometry 1280x720 -depth 24 -name "VNC" :${DISPLAY_NUM} 2>&1; then
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
