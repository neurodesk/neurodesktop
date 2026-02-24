#!/bin/bash

# This file is sourced once in jupyterlab_startup.sh and once in ~/.bashrc so we get the same environment variables in the jupyter and in the desktop environment
if [ -z "$NEURODESKTOP_ENV_SOURCED" ]; then
    export NEURODESKTOP_ENV_SOURCED=1

    if [[ -z "${NB_USER}" ]]; then
        export NB_USER=${USER}
    fi

if [[ -z "${USER}" ]]; then
    export USER=${NB_USER}
fi

export MODULEPATH=/neurodesktop-storage/containers/modules/:/cvmfs/neurodesk.ardc.edu.au/containers/modules/

# Only setup MODULEPATH if a module system is installed
if [ -f '/usr/share/module.sh' ]; then
        export OFFLINE_MODULES=/neurodesktop-storage/containers/modules/
        export CVMFS_MODULES=/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/

        if [ ! -d $CVMFS_MODULES ]; then
                MODULEPATH=${OFFLINE_MODULES}
                export CVMFS_DISABLE=true
        else
                MODULEPATH=${CVMFS_MODULES}*
                export MODULEPATH=`echo $MODULEPATH | sed 's/ /:/g'`

                # if the offline modules directory exists, we can use it and will prefer it over cvmfs
                if [ -d ${OFFLINE_MODULES} ]; then
                        export MODULEPATH=${OFFLINE_MODULES}:$MODULEPATH
                fi
        fi
fi
fi

# Show informational messages in interactive terminals (outside the NEURODESKTOP_ENV_SOURCED guard so they show on each new terminal)
# Use a separate guard to prevent duplicate messages when sourced from both /etc/bash.bashrc and ~/.bashrc
if [ -z "$NEURODESKTOP_MSG_SHOWN" ] && [ -f '/usr/share/module.sh' ]; then
        if [[ $- == *i* || -t 1 ]]; then
                export NEURODESKTOP_MSG_SHOWN=1
                # Check for local containers
                if [ -d "${OFFLINE_MODULES}" ] && [ -d "${CVMFS_MODULES}" ]; then
                        echo "Found local container installations in $OFFLINE_MODULES. Using installed containers with a higher priority over CVMFS."
                fi

                echo 'Neuroimaging tools are accessible via the Neurodesktop Applications menu and running them through the menu will provide help and setup instructions. If you are familiar with the tools and you want to combine multiple tools in one script, you can run "ml av" to see which tools are available and then use "ml <tool>/<version>" to load them. '

                # check if $CVMFS_DISABLE is set to true
                if [[ "$CVMFS_DISABLE" == "true" ]]; then
                        echo "CVMFS is disabled. Using local containers stored in $MODULEPATH"
                        if [ ! -d $MODULEPATH ]; then
                                echo 'Neurodesk tools not yet downloaded. Choose tools to install from the Neurodesktop Application menu.'
                        fi
                fi
        fi
fi

# This also needs to be set in the Dockerfile, so it is available in a jupyter notebook
export APPTAINER_BINDPATH=/data,/mnt,/neurodesktop-storage,/tmp,/cvmfs
# This also needs to be set in the Dockerfile, so it is available in a jupyter notebook

export APPTAINERENV_SUBJECTS_DIR=${HOME}/freesurfer-subjects-dir
export MPLCONFIGDIR=${HOME}/.config/matplotlib-mpldir

# Keep agent wrappers in /usr/local/sbin ahead of user-level installs in ~/.local/bin.
path_prepend() {
        local dir="$1"
        PATH=":${PATH}:"
        PATH="${PATH//:${dir}:/:}"
        PATH="${PATH#:}"
        PATH="${PATH%:}"
        PATH="${dir}${PATH:+:${PATH}}"
}

path_append_if_missing() {
        local dir="$1"
        case ":${PATH}:" in
                *":${dir}:"*) ;;
                *) PATH="${PATH}${PATH:+:}${dir}" ;;
        esac
}

path_prepend "/usr/local/sbin"
path_append_if_missing "${HOME}/.local/bin"
path_append_if_missing "/opt/conda/bin"
path_append_if_missing "/opt/conda/condabin"
export PATH

# Default to host Ollama from inside Docker unless explicitly overridden.
# Local Ollama mode (START_LOCAL_LLMS=1) overrides this in before_notebook.sh.
if [ -z "${OLLAMA_HOST}" ]; then
        export OLLAMA_HOST="http://host.docker.internal:11434"
fi

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

slurm_conf_is_local_neurodesktop() {
        local conf_file="$1"
        [ -r "${conf_file}" ] && grep -Eq '^ClusterName=neurodesktop([[:space:]]|$)' "${conf_file}" 2>/dev/null
}

resolve_host_slurm_conf() {
        local conf_file
        if [ -n "${SLURM_CONF:-}" ] && [ -r "${SLURM_CONF}" ]; then
                if ! slurm_conf_is_local_neurodesktop "${SLURM_CONF}"; then
                        echo "${SLURM_CONF}"
                        return 0
                fi
        fi

        for conf_file in \
                /etc/slurm/slurm.conf \
                /etc/slurm-llnl/slurm.conf \
                /run/host/etc/slurm/slurm.conf \
                /host/etc/slurm/slurm.conf
        do
                if [ ! -r "${conf_file}" ]; then
                        continue
                fi
                if slurm_conf_is_local_neurodesktop "${conf_file}"; then
                        continue
                fi
                echo "${conf_file}"
                return 0
        done
        return 1
}

read_slurm_conf_value() {
        local conf_file="$1"
        local key="$2"
        [ -r "${conf_file}" ] || return 1

        awk -F= -v key="${key}" '
                /^[[:space:]]*#/ { next }
                {
                        line=$0
                        sub(/^[[:space:]]+/, "", line)
                        if (line ~ ("^" key "[[:space:]]*=")) {
                                sub(/^[^=]*=/, "", line)
                                sub(/[[:space:]]+#.*$/, "", line)
                                gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
                                print line
                                exit
                        }
                }
        ' "${conf_file}"
}

resolve_slurm_auth_type() {
        local conf_file="$1"
        local auth_type

        auth_type="$(read_slurm_conf_value "${conf_file}" "AuthType" || true)"
        if [ -n "${auth_type}" ]; then
                printf '%s\n' "${auth_type}" | tr '[:upper:]' '[:lower:]'
                return 0
        fi
        return 1
}

resolve_slurm_cluster_name() {
        local conf_file="$1"
        read_slurm_conf_value "${conf_file}" "ClusterName"
}

resolve_munge_socket() {
        local socket_path
        for socket_path in \
                "${MUNGE_SOCKET:-}" \
                /run/munge/munge.socket.2 \
                /var/run/munge/munge.socket.2
        do
                if [ -n "${socket_path}" ] && [ -S "${socket_path}" ]; then
                        echo "${socket_path}"
                        return 0
                fi
        done
        return 1
}

resolve_sack_socket() {
        local cluster_name="$1"
        local cluster_socket=""
        local socket_path

        if [ -n "${cluster_name}" ]; then
                cluster_socket="/run/slurm-${cluster_name}/sack.socket"
        fi

        for socket_path in \
                "${SLURM_SACK_SOCKET:-}" \
                "${cluster_socket}" \
                /run/slurm/sack.socket \
                /run/slurmctld/sack.socket \
                /run/slurmdbd/sack.socket \
                /var/run/slurm/sack.socket \
                /var/run/slurmctld/sack.socket \
                /var/run/slurmdbd/sack.socket
        do
                if [ -n "${socket_path}" ] && [ -S "${socket_path}" ]; then
                        echo "${socket_path}"
                        return 0
                fi
        done
        return 1
}

configure_host_slurm_environment() {
        local host_slurm_conf host_munge_socket host_sack_socket host_auth_type host_cluster_name
        host_slurm_conf="$(resolve_host_slurm_conf || true)"
        if [ -n "${host_slurm_conf}" ]; then
                export SLURM_CONF="${host_slurm_conf}"
        elif [ -n "${SLURM_CONF:-}" ] && slurm_conf_is_local_neurodesktop "${SLURM_CONF}"; then
                unset SLURM_CONF
        fi

        host_auth_type=""
        host_cluster_name=""
        if [ -n "${host_slurm_conf}" ]; then
                host_auth_type="$(resolve_slurm_auth_type "${host_slurm_conf}" || true)"
                host_cluster_name="$(resolve_slurm_cluster_name "${host_slurm_conf}" || true)"
        fi

        host_munge_socket="$(resolve_munge_socket || true)"
        if [ -n "${host_munge_socket}" ]; then
                export MUNGE_SOCKET="${host_munge_socket}"
        fi

        host_sack_socket="$(resolve_sack_socket "${host_cluster_name}" || true)"
        if [ -n "${host_sack_socket}" ]; then
                export SLURM_SACK_SOCKET="${host_sack_socket}"
        elif [ "${host_auth_type}" = "auth/slurm" ]; then
                unset SLURM_SACK_SOCKET
        fi
}

# Slurm mode:
# - local: use the in-container single-node Slurm queue.
# - host: rely on host cluster Slurm configuration provided from outside.
if [[ -z "${NEURODESKTOP_SLURM_MODE}" ]]; then
        if is_apptainer_runtime; then
                export NEURODESKTOP_SLURM_MODE=host
        else
                export NEURODESKTOP_SLURM_MODE=local
        fi
else
        export NEURODESKTOP_SLURM_MODE="$(printf '%s' "${NEURODESKTOP_SLURM_MODE}" | tr '[:upper:]' '[:lower:]')"
fi

case "${NEURODESKTOP_SLURM_MODE}" in
        host)
                # In host mode we keep host-provided SLURM_CONF and account defaults.
                configure_host_slurm_environment
                ;;
        *)
                # Local Slurm configuration used by the in-container single-node queue.
                export NEURODESKTOP_SLURM_MODE=local
                export SLURM_CONF=/etc/slurm/slurm.conf
                if [[ -z "${NEURODESKTOP_SLURM_PARTITION}" ]]; then
                        export NEURODESKTOP_SLURM_PARTITION=neurodesktop
                fi
                # Clear inherited account defaults from host environments so sbatch jobs inside the container
                # use the local slurmdbd's "default" account rather than a host-cluster account name.
                unset SBATCH_ACCOUNT
                unset SLURM_ACCOUNT
                ;;
esac

# This is needed to make containers writable as a workaround for macos with Apple Silicon. We need to do it here for the desktop
# and in the dockerfile for the jupyter notebook
export neurodesk_singularity_opts=" --overlay /tmp/apptainer_overlay "
# export neurodesk_singularity_opts=" -w " THIS DOES NOT WORK FOR SIMG FILES IN OFFLINE MODE
# There is a small delay in using --overlay in comparison to -w - maybe it would be faster to use a fixed size overlay file instead?

# !echo $neurodesk_singularity_opts
# test if the workaround is still needed: ml fsl; fslmaths or 
# import lmod
# await lmod.load('fsl/6.0.4')
# await lmod.list()
# !fslmaths

# # this adds --nv to the singularity calls -> but only if a GPU is present
# if [ "$(lspci | grep -i nvidia)" ]
# then
#         export neurodesk_singularity_opts="${neurodesk_singularity_opts} --nv "
# fi
# THIS IS CURRENTLY DISABLED BECAUSE IT CAUSES PROBLEMS ON UBUNTU 24.04 HOSTS WHERE THIS LEADS TO A GLIBC VERSION ERROR

export PS1='\u@neurodesktop-$NEURODESKTOP_VERSION:\w$ '

alias ll='ls -la'
