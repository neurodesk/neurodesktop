#!/bin/bash
set -e

source /opt/neurodesktop/environment_variables.sh
jupyter lab --ip=127.0.0.1 --port=8888 --no-browser > "$HOME/.jupyter/kasm-jupyter.log" 2>&1 &
jupyter_pid=$!
startlxde &
desktop_pid=$!
trap 'kill "$jupyter_pid" "$desktop_pid" 2>/dev/null || true' EXIT
trap 'exit 0' TERM INT
wait -n "$jupyter_pid" "$desktop_pid"
