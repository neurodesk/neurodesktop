#!/bin/bash
# OpenCode runs tool commands through non-interactive Bash. Those shells do
# not read ~/.bashrc, and the long-lived Jupyter process may still carry the
# local-only MODULEPATH it inherited before lazy CVMFS startup completed.
# BASH_ENV makes every OpenCode Bash tool refresh the current Neurodesktop
# environment and initialize Lmod before running the requested command.

if [ -r /opt/neurodesktop/environment_variables.sh ]; then
    source /opt/neurodesktop/environment_variables.sh >/dev/null 2>&1
fi

if [ -r /etc/profile.d/lmod.sh ]; then
    source /etc/profile.d/lmod.sh >/dev/null 2>&1
elif [ -r /usr/share/lmod/lmod/init/bash ]; then
    source /usr/share/lmod/lmod/init/bash >/dev/null 2>&1
fi
