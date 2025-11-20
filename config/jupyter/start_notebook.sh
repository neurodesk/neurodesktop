#!/bin/bash
# order: #### start_notebook.sh #### -> before_notebook.sh -> jupyter_notebook_config.py -> jupyterlab_startup.sh

# if [ -z "$GRANT_SUDO" ]; then
# export GRANT_SUDO='yes'
# fi
if [ -z "$RESTARTABLE" ]; then
export RESTARTABLE='yes'
fi

# HOME_UID=$(stat -c "%u" ${HOME})
# HOME_GID=$(stat -c "%g" ${HOME})

# if [[ "${NB_UID}" != "${HOME_UID}" || "${NB_GID}" != "${HOME_GID}" ]]; then
#     if [ -z "$CHOWN_HOME" ]; then
#     export CHOWN_HOME='yes'
#     fi
#     if [ -z "$CHOWN_HOME_OPTS" ]; then
#     export CHOWN_HOME_OPTS='-R'
#     fi
# fi

# Function to check and apply chown if necessary
apply_chown_if_needed() {
    local dir=$1
    local recursive=$2
    if [ -d "$dir" ]; then
        current_uid=$(stat -c "%u" "$dir")
        current_gid=$(stat -c "%g" "$dir")
        if [ "$current_uid" != "$NB_UID" ] || [ "$current_gid" != "$NB_GID" ]; then
            export CHOWN_HOME='yes'
            if [ "$recursive" = true ]; then
                export CHOWN_HOME_OPTS='-R'
            fi
        fi
    fi
}

apply_chown_if_needed "${HOME}" true
# apply_chown_if_needed "${HOME}" false
# apply_chown_if_needed "${HOME}/.local" false
# apply_chown_if_needed "${HOME}/.local/share" false
# apply_chown_if_needed "${HOME}/.ssh" true
# apply_chown_if_needed "${HOME}/.local/share/jupyter" true

