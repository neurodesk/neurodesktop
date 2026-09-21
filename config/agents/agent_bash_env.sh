#!/bin/bash
# Also sourced explicitly by retained Slurm scripts, including under set -u.
neurodesk_initialize_tool_shell() {
    local restore_nounset=0 status=0
    local bash_env_set="${BASH_ENV+x}" bash_env="${BASH_ENV-}"
    case "$-" in *u*) restore_nounset=1 ;; esac
    set +u

    if [ -n "${NEURODESKTOP_PREVIOUS_BASH_ENV:-}" ] &&
       [ "${NEURODESKTOP_PREVIOUS_BASH_ENV}" != "${BASH_SOURCE[0]}" ] &&
       [ "${_NEURODESKTOP_PREVIOUS_BASH_ENV_PID:-}" != "$BASHPID" ]; then
        _NEURODESKTOP_PREVIOUS_BASH_ENV_PID=$BASHPID
        . "${NEURODESKTOP_PREVIOUS_BASH_ENV}" || status=$?
    fi

    if [ "$status" -eq 0 ]; then
        . /opt/neurodesktop/environment_variables.sh >/dev/null 2>&1 || status=$?
    fi
    if [ "$status" -eq 0 ]; then
        if [ -r /etc/profile.d/lmod.sh ]; then
            . /etc/profile.d/lmod.sh || status=$?
        elif [ -r /usr/share/module.sh ]; then
            . /usr/share/module.sh || status=$?
        else
            . /usr/share/lmod/lmod/init/bash || status=$?
        fi
    fi

    # The distribution profile sets BASH_ENV to its raw Lmod initializer.
    # Keep the shared initializer active for subsequent child tool shells.
    if [ "$bash_env_set" = x ]; then
        export BASH_ENV="$bash_env"
    else
        unset BASH_ENV
    fi
    if [ "$restore_nounset" -eq 1 ]; then set -u; fi
    return "$status"
}

neurodesk_initialize_tool_shell || exit $?
