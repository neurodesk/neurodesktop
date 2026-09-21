#!/bin/bash
set -e
pgrep -x 'Xvnc|Xkasmvnc' >/dev/null
pgrep -x lxsession >/dev/null
curl --fail --insecure --silent --output /dev/null --max-time 3 \
    --user "kasm_user:${VNC_PW}" "https://127.0.0.1:${NO_VNC_PORT:-6901}/"
curl --fail --silent --output /dev/null --max-time 3 http://127.0.0.1:8888/login
