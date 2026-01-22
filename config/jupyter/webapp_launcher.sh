#!/bin/bash
# Generic webapp launcher for JupyterLab
# Usage: webapp_launcher.sh <app_name>
#
# This script is called by JupyterLab ServerProxy to start a webapp.
# It delegates to the Python wrapper which handles splash pages, container
# startup, and proxying.

APP_NAME="$1"

if [ -z "$APP_NAME" ]; then
    echo "Usage: webapp_launcher.sh <app_name>"
    echo ""
    echo "Starts a webapp wrapper server for the specified application."
    echo "Configuration is read from /opt/neurodesktop/webapps.json"
    exit 1
fi

exec python3 /opt/neurodesktop/webapp_wrapper/webapp_wrapper.py "$APP_NAME"
