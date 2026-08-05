#!/bin/bash
# OpenCode runs agent commands with non-interactive ``bash -c`` shells. Bash
# does not read ~/.bashrc for those shells, so use BASH_ENV to refresh the
# lazily-mounted Neurodesk module catalogue and define Lmod's ``module`` shell
# function before every command. Keep this file quiet: its output would be
# prepended to every OpenCode Bash tool result.

if [ -r /opt/neurodesktop/environment_variables.sh ]; then
    . /opt/neurodesktop/environment_variables.sh >/dev/null 2>&1
fi

if [ -r /usr/share/module.sh ]; then
    . /usr/share/module.sh
elif [ -r /usr/share/lmod/lmod/init/bash ]; then
    . /usr/share/lmod/lmod/init/bash
fi
