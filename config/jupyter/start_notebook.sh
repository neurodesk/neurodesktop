#!/bin/bash
# order: #### start_notebook.sh #### -> before_notebook.sh -> jupyterlab_startup.sh -> jupyter_notebook_config.py

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

is_apptainer_runtime() {
    [ -n "${SINGULARITY_NAME:-}" ] || \
    [ -n "${APPTAINER_NAME:-}" ] || \
    [ -n "${APPTAINER_CONTAINER:-}" ] || \
    [ -n "${SINGULARITY_CONTAINER:-}" ] || \
    [ -n "${APPTAINER_COMMAND:-}" ] || \
    [ -n "${SINGULARITY_COMMAND:-}" ] || \
    [ -d "/.apptainer.d" ] || \
    [ -d "/.singularity.d" ]
}

can_chown_dir() {
    local dir="$1"
    local owner probe_file
    owner="$(stat -c "%u:%g" "${dir}" 2>/dev/null || true)"
    if [ -z "${owner}" ]; then
        owner="${NB_UID}:${NB_GID}"
    fi
    probe_file="${dir}/.neurodesktop-chown-probe-$$"

    if touch "${probe_file}" >/dev/null 2>&1; then
        if ! chown "${NB_UID}:${NB_GID}" "${probe_file}" >/dev/null 2>&1; then
            rm -f "${probe_file}" >/dev/null 2>&1 || true
            return 1
        fi
        rm -f "${probe_file}" >/dev/null 2>&1 || true
        return 0
    fi

    chown "${owner}" "${dir}" >/dev/null 2>&1
}

# Function to check and apply chown if necessary
apply_chown_if_needed() {
    local dir="$1"
    local recursive="$2"
    local current_uid current_gid

    # If running in Apptainer/Singularity, we don't want to chown
    if is_apptainer_runtime; then
        return
    fi

    if [ ! -d "${dir}" ]; then
        return
    fi

    current_uid=$(stat -c "%u" "${dir}")
    current_gid=$(stat -c "%g" "${dir}")
    if [ "${current_uid}" != "${NB_UID}" ] || [ "${current_gid}" != "${NB_GID}" ]; then
        if ! can_chown_dir "${dir}"; then
            echo "[WARN] Skipping CHOWN_HOME for ${dir}: chown unsupported in this runtime/filesystem."
            return
        fi
        export CHOWN_HOME='yes'
        if [ "${recursive}" = true ]; then
            export CHOWN_HOME_OPTS='-R'
        fi
    fi
}

apply_chown_if_needed "${HOME}" true
# apply_chown_if_needed "${HOME}" false
# apply_chown_if_needed "${HOME}/.local" false
# apply_chown_if_needed "${HOME}/.local/share" false
# apply_chown_if_needed "${HOME}/.ssh" true
# apply_chown_if_needed "${HOME}/.local/share/jupyter" true
