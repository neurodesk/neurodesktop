#!/bin/bash

# Find the Xpra display by looking for the socket file directly
# This Avoids parsing issues with 'xpra list' output (e.g. confusing UID :1000 with display :1000 aka :10)

USER_ID=$(id -u)
XPRA_SOCK_DIR="/run/user/$USER_ID/xpra"

# Check in the runtime directory
if [ -d "$XPRA_SOCK_DIR" ]; then
    # Find the first socket file (e.g., :100)
    DISP_SOCK=$(ls "$XPRA_SOCK_DIR" | grep -E '^:[0-9]+$' | head -n 1)
fi

# Fallback: Check ~/.xpra if not found above
if [ -z "$DISP_SOCK" ] && [ -d "$HOME/.xpra" ]; then
    DISP_SOCK=$(ls "$HOME/.xpra" | grep -E '^:[0-9]+$' | head -n 1)
fi

# Fallback: Parse xpra list if manual sockets failed, but be careful with regex
if [ -z "$DISP_SOCK" ]; then
    # Look for a colon followed by digits, possibly followed by space or end of line, 
    # but ensure it's not part of a path (preceded by /)
    # This is complex, so allow the previous socket methods to be primary.
    # We ignore the user id just in case it appears as :UID
    DISP_SOCK=$(xpra list 2>/dev/null | grep -oE ':[0-9]+' | grep -v ":$USER_ID" | head -n 1)
fi

if [ -n "$DISP_SOCK" ]; then
    export DISPLAY=$DISP_SOCK
    echo "Environment configured for Xpra display: $DISP_SOCK"
    echo "GUI applications started in this terminal will now appear in your Xpra Desktop tab."
else
    echo "No active Xpra session found."
    echo "Please open 'Xpra Desktop' from the Jupyter Launcher first, then run this command again."
    
    # Debug info
    echo "DEBUG: Checked $XPRA_SOCK_DIR and $HOME/.xpra"
fi
