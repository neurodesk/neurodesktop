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

# VNC
vncserver -kill :1
vncserver -geometry 1280x720 -depth 24 -name "VNC" :1
xset -display :1 s off

# Guacamole
# sudo service guacd start
guacd -b 127.0.0.1
echo "    Running guacamole"
