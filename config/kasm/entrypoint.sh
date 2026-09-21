#!/bin/bash
set -e

if [ -z "${VNC_PW:-}" ]; then
    echo 'Set VNC_PW to a session password. Kasm Workspaces supplies this automatically.' >&2
    exit 1
fi

/dockerstartup/kasm_default_profile.sh /bin/true
sudo -E /usr/local/bin/before-notebook.d/before_notebook.sh
source /opt/neurodesktop/environment_variables.sh
exec /dockerstartup/vnc_startup.sh "$@"
