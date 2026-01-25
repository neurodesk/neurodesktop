#!/bin/bash

echo "==================================================="
echo "    XPRA DIAGNOSTICS"
echo "==================================================="

# 1. Identify the running Xpra process
echo "[1] Process Information:"
XPRA_PID=$(pgrep -a xpra)
if [ -z "$XPRA_PID" ]; then
    echo "CRITICAL: Xpra process is NOT running."
    exit 1
else
    echo "$XPRA_PID"
fi
echo "---------------------------------------------------"

# 2. Identify Active Sessions via xpra list
echo "[2] Xpra Session List:"
xpra list
echo "---------------------------------------------------"

# 3. Check X11 Sockets
echo "[3] X11 Socket Check (/tmp/.X11-unix):"
ls -la /tmp/.X11-unix/ 2>/dev/null || echo "Directory /tmp/.X11-unix not found!"
echo "---------------------------------------------------"

# 4. Check Current Environment
echo "[4] Current Terminal Environment:"
echo "DISPLAY=$DISPLAY"
echo "XAUTHORITY=$XAUTHORITY"
echo "USER=$(whoami)"
echo "HOME=$HOME"
echo "---------------------------------------------------"

# 5. Connectivity Test
if [ -n "$DISPLAY" ]; then
    echo "[5] Connectivity Test for $DISPLAY:"
    
    # Extract just the number (e.g., :99 -> 99)
    DISP_NUM=$(echo $DISPLAY | sed 's/://')
    SOCKET_PATH="/tmp/.X11-unix/X$DISP_NUM"
    
    if [ -S "$SOCKET_PATH" ]; then
        echo "SUCCESS: X11 socket $SOCKET_PATH exists."
    else
        echo "FAILURE: X11 socket $SOCKET_PATH MISSING. Apps will not work."
    fi
    
    # Check if xpra info can connect
    echo "Running 'xpra info'..."
    xpra info 2>&1 | head -n 5
else
    echo "[5] Connectivity Test: SKIPPED (No DISPLAY set)"
fi
echo "==================================================="
