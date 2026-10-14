#!/bin/bash

# order: start_notebook.sh -> ### before_notebook.sh ### -> jupyterlab_startup.sh -> jupyter_notebook_config.py

# Phase timing helpers
_phase_start() { _PHASE_T0=$(date +%s%3N); echo "[TIMING] $1 started"; }
_phase_end()   { local elapsed=$(( $(date +%s%3N) - _PHASE_T0 )); echo "[TIMING] $1 completed in ${elapsed}ms"; }

_phase_start "critical-startup"

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
    else
        if [ -n "${SLURM_CONF:-}" ] && slurm_conf_is_local_neurodesktop "${SLURM_CONF}"; then
            unset SLURM_CONF
        fi
        echo "[WARN] Host Slurm mode selected but no readable slurm.conf was found."
        echo "[WARN] Bind your host Slurm config (for example: --bind /etc/slurm:/etc/slurm)."
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
    fi

    case "${host_auth_type}" in
        auth/munge)
            if [ -z "${host_munge_socket}" ]; then
                echo "[WARN] Host Slurm uses AuthType=auth/munge but no MUNGE socket was found."
                echo "[WARN] Bind /run/munge or /var/run/munge into the container."
            fi
            ;;
        auth/slurm)
            if [ -z "${host_sack_socket}" ]; then
                echo "[WARN] Host Slurm uses AuthType=auth/slurm but no sack.socket was found."
                echo "[WARN] Bind the host Slurm runtime directory (for example: --bind /run/slurm:/run/slurm)."
            fi
            ;;
        *)
            if [ -z "${host_munge_socket}" ] && [ -z "${host_sack_socket}" ]; then
                echo "[WARN] Host Slurm mode selected but neither MUNGE nor sack.socket was found."
                echo "[WARN] Bind your host auth socket paths (for example /run/munge or /run/slurm)."
            fi
            ;;
    esac
}

can_chown_path_with_runner() {
    local path="$1"
    shift
    local owner probe_file
    local -a chown_runner=("$@")

    owner="$(stat -c "%u:%g" "${path}" 2>/dev/null || true)"
    if [ -z "${owner}" ]; then
        owner="${NB_UID}:${NB_GID}"
    fi

    probe_file="${path}/.neurodesktop-chown-probe-$$"
    if touch "${probe_file}" >/dev/null 2>&1; then
        if ! "${chown_runner[@]}" "${NB_UID}:${NB_GID}" "${probe_file}" >/dev/null 2>&1; then
            rm -f "${probe_file}" >/dev/null 2>&1 || true
            return 1
        fi
        rm -f "${probe_file}" >/dev/null 2>&1 || true
        return 0
    fi

    "${chown_runner[@]}" "${owner}" "${path}" >/dev/null 2>&1
}

fix_home_ownership_if_needed() {
    # Use $HOME, not /home/$NB_USER. Under Apptainer on HPC the container user
    # (e.g. `sciget`) has HOME=/home/jovyan bind-mounted; /home/$NB_USER does
    # not exist and a literal touch against it aborts startup.
    local home_dir="${HOME:-/home/${NB_USER}}"
    local current_uid current_gid
    local -a chown_runner

    if is_apptainer_runtime; then
        return
    fi

    if [ ! -d "$home_dir" ]; then
        return
    fi

    current_uid=$(stat -c "%u" "$home_dir")
    current_gid=$(stat -c "%g" "$home_dir")

    if [ "$current_uid" = "$NB_UID" ] && [ "$current_gid" = "$NB_GID" ]; then
        return
    fi

    echo "Fixing ownership of $home_dir (was $current_uid:$current_gid, setting to $NB_UID:$NB_GID)"
    if [ "$EUID" -eq 0 ]; then
        chown_runner=(chown)
    elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        chown_runner=(sudo -n chown)
    else
        echo "[WARN] Unable to fix $home_dir ownership: requires root or passwordless sudo."
        return
    fi

    if ! can_chown_path_with_runner "$home_dir" "${chown_runner[@]}"; then
        echo "[WARN] Skipping ownership fix for $home_dir: chown unsupported in this runtime/filesystem."
        return
    fi

    if ! "${chown_runner[@]}" "$NB_UID:$NB_GID" "$home_dir"; then
        echo "[WARN] Failed to fix ownership of $home_dir."
    fi
}

link_data_dir_if_present() {
    local source_dir="/data"
    local home_dir="${HOME:-/home/${NB_USER}}"
    local target_link="${home_dir}/data"

    if [ ! -d "$source_dir" ] || [ ! -d "$home_dir" ]; then
        return
    fi

    if [ -L "$target_link" ]; then
        if [ "$(readlink "$target_link")" = "$source_dir" ]; then
            return
        fi
        echo "[WARN] ${target_link} already exists as a symlink to a different location. Skipping."
        return
    fi

    if [ -e "$target_link" ]; then
        echo "[WARN] ${target_link} already exists and is not a symlink. Skipping."
        return
    fi

    ln -s "$source_dir" "$target_link"
}

fix_home_ownership_if_needed
link_data_dir_if_present

if [ "$EUID" -eq 0 ]; then
    /usr/bin/python3 -I /opt/neurodesktop/startup_security.py || exit 1
    /bin/bash /opt/neurodesktop/prepare_cpuinfo.sh
    if [ "$(getent passwd "${NB_USER}" | cut -d: -f7)" != "/bin/bash" ]; then
        usermod --shell /bin/bash "${NB_USER}"
    fi

    # Make sure binfmt_misc is mounted in the place apptainer expects it. Some
    # runtimes (for example Kubernetes/containerd) do not allow mounting here,
    # so this must remain best-effort rather than aborting startup.
    if [ -d "/proc/sys/fs/binfmt_misc" ]; then
        # Check if binfmt_misc is already mounted
        if ! mountpoint -q /proc/sys/fs/binfmt_misc; then
            echo "binfmt_misc directory exists but is not mounted. Mounting now..."
            if ! mount -t binfmt_misc binfmt /proc/sys/fs/binfmt_misc >/dev/null 2>&1; then
                echo "[WARN] Unable to mount /proc/sys/fs/binfmt_misc in this runtime. Continuing without it."
            fi
        else
            echo "binfmt_misc is already mounted."
        fi
    else
        echo "binfmt_misc directory does not exist in /proc/sys/fs."
    fi

    # CVMFS startup mode: lazy (default) defers to background worker; eager preserves synchronous behavior.
    CVMFS_STARTUP_MODE="${NEURODESKTOP_CVMFS_STARTUP_MODE:-lazy}"

    if [ "$CVMFS_STARTUP_MODE" = "eager" ] && [ ! -d "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/" ]; then
        # the cvmfs directory is not yet mounted

        # check if we have internet connectivity:
        if timeout 3 nslookup neurodesk.org >/dev/null 2>&1; then
            echo "Internet is up"
        else
            export CVMFS_DISABLE=true
            echo "No internet connection. Disabling CVMFS."
        fi

        # This is to capture legacy use. If CVMFS_DISABLE is not set, we assume it is false, which was the legacy behaviour.
        if [ -z "$CVMFS_DISABLE" ]; then
            export CVMFS_DISABLE="false"
        fi

        if [[ "$CVMFS_DISABLE" == "false" ]]; then
            # CVMFS_DISABLE is false and CVMFS should be enabled.

            # needs to be kept in sync with config/cvmfs/default.local
            CACHE_DIR="${HOME}/cvmfs_cache"

            # Create the cache directory if it doesn't exist
            if [ ! -d "$CACHE_DIR" ]; then
                echo "Creating CVMFS cache directory at $CACHE_DIR"
                mkdir -p "$CACHE_DIR"
            else
                echo "CVMFS cache directory already exists at $CACHE_DIR"
            fi

            # Make sure the CVMFS user can access the cache directory
            chmod 755 "${HOME}"
            # The cache directory needs to be owned by cvmfs user and group
            if sudo -n true 2>/dev/null; then
                chown -R cvmfs:root "$CACHE_DIR"
            fi

            # try to list the directory in case it's autofs mounted outside
            ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ 2>/dev/null && echo "CVMFS is ready" || echo "CVMFS directory not there. Trying internal fuse mount next."

            if [ ! -d "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/" ]; then
                # it is not available outside, so try mounting with fuse inside container

                # Rank the CVMFS servers by measured request latency and download
                # speed and write the repository config with the best server first
                # (see cvmfs_server_select.sh for the selection strategy).
                if [ -x /opt/neurodesktop/cvmfs_server_select.sh ]; then
                    /opt/neurodesktop/cvmfs_server_select.sh || echo "Warning: no CVMFS server measurable; wrote static fallback config."
                else
                    echo "Warning: /opt/neurodesktop/cvmfs_server_select.sh not found. Using existing CVMFS config."
                fi

                echo "\
                ==================================================================
                Mounting CVMFS"
                if [ -x /etc/init.d/autofs ] && service autofs status >/dev/null 2>&1; then
                    echo "autofs is running - not attempting to mount manually:"
                    ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ 2>/dev/null && echo "CVMFS is ready after autofs mount" || echo "AutoFS not working!"
                else
                    if [ -x /etc/init.d/autofs ]; then
                        echo "autofs is NOT running - attempting to mount manually:"
                    else
                        echo "autofs service is unavailable in this container - attempting to mount manually:"
                    fi
                    mkdir -p /cvmfs/neurodesk.ardc.edu.au
                    mount -t cvmfs neurodesk.ardc.edu.au /cvmfs/neurodesk.ardc.edu.au

                    ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ 2>/dev/null && echo "CVMFS is ready after manual mount" || echo "Manual CVMFS mount not successful"

                    echo "\
                    ==================================================================
                    CVMFS servers:"
                    # Note: no `host probe` here - it reorders the host chain by
                    # round-trip time, which would undo the measured ranking.
                    cvmfs_talk -i neurodesk.ardc.edu.au host info
                fi
            fi
        fi
    fi


    if [ "$CVMFS_STARTUP_MODE" != "eager" ]; then
        echo "[INFO] NEURODESKTOP_CVMFS_STARTUP_MODE=lazy: deferring CVMFS mount to background worker."
    fi
fi

# Source custom scripts in .bashrc if they are not already there
BASHRC_FILE="${HOME:-/home/${NB_USER}}/.bashrc"
INIT_MODULES="if [ -f '/usr/share/module.sh' ]; then source /usr/share/module.sh; fi"
PERSISTENT_HISTORY_MARKER="# Neurodesk persistent bash history"

# if [ -f "$BASHRC_FILE" ]; then
touch "$BASHRC_FILE"
# Add module.sh if not already in .bashrc
if ! grep -qF "$INIT_MODULES" "$BASHRC_FILE"; then
    echo "$INIT_MODULES" >> "$BASHRC_FILE"
fi

# Ensure bash history is durable across terminal and session restarts.
if ! grep -qF "$PERSISTENT_HISTORY_MARKER" "$BASHRC_FILE"; then
    cat >> "$BASHRC_FILE" <<'EOF'

# Neurodesk persistent bash history
if [[ $- == *i* ]]; then
    shopt -s histappend
    if [ -d "${HOME}/neurodesktop-storage" ] && [ -w "${HOME}/neurodesktop-storage" ]; then
        export HISTFILE="${HOME}/neurodesktop-storage/.bash_history"
    elif [ -d "/neurodesktop-storage" ] && [ -w "/neurodesktop-storage" ]; then
        export HISTFILE="/neurodesktop-storage/.bash_history"
    else
        export HISTFILE="${HISTFILE:-$HOME/.bash_history}"
    fi
    export HISTSIZE=100000
    export HISTFILESIZE=200000
    export HISTCONTROL=ignoredups:erasedups

    # Persist history continuously so abrupt terminal/session closes do not lose commands.
    if [[ "${PROMPT_COMMAND:-}" != *"history -a"* ]]; then
        if [ -n "${PROMPT_COMMAND:-}" ]; then
            export PROMPT_COMMAND="history -a; history -n; ${PROMPT_COMMAND}"
        else
            export PROMPT_COMMAND="history -a; history -n"
        fi
    fi
fi
EOF
fi
# fi

# Note: environment_variables.sh is sourced via /etc/bash.bashrc (set in Dockerfile)

# Read cgroup and Slurm allocation limits and export MEM_LIMIT/CPU_LIMIT for
# jupyter-resource-usage. Sourced so the exports reach the Jupyter server.
# shellcheck source=/dev/null
source /opt/neurodesktop/detect_resource_limits.sh
detect_resource_limits

# Slurm mode:
# - local: start an in-container single-node Slurm queue.
# - host: do not start local Slurm; rely on host cluster Slurm via bound config/socket.
if [ -z "${NEURODESKTOP_SLURM_MODE:-}" ] && is_apptainer_runtime; then
    SLURM_MODE_RAW="host"
    echo "[INFO] Detected Apptainer/Singularity runtime; defaulting NEURODESKTOP_SLURM_MODE=host."
else
    SLURM_MODE_RAW="${NEURODESKTOP_SLURM_MODE:-local}"
fi
SLURM_MODE="$(printf '%s' "${SLURM_MODE_RAW}" | tr '[:upper:]' '[:lower:]')"
case "${SLURM_MODE}" in
    local|host) ;;
    *)
        echo "[WARN] Unknown NEURODESKTOP_SLURM_MODE='${SLURM_MODE_RAW}'. Falling back to 'local'."
        SLURM_MODE=local
        ;;
esac
export NEURODESKTOP_SLURM_MODE="${SLURM_MODE}"

# Slurm startup mode: lazy (default) defers to background worker; eager preserves synchronous behavior.
SLURM_STARTUP_MODE="${NEURODESKTOP_SLURM_STARTUP_MODE:-lazy}"

# Start a local single-node Slurm queue inside the container.
# In auto mode, Slurm defaults to non-cgroup compatibility settings unless explicitly enabled.
if [ "${NEURODESKTOP_SLURM_MODE}" = "host" ]; then
    configure_host_slurm_environment
    export NEURODESKTOP_SLURM_ENABLE=0
    echo "[INFO] NEURODESKTOP_SLURM_MODE=host: skipping local Slurm startup."
elif [ "$SLURM_STARTUP_MODE" = "eager" ]; then
    if [ "$EUID" -eq 0 ]; then
        if ! /opt/neurodesktop/setup_and_start_slurm.sh; then
            echo "[WARN] Failed to configure/start local Slurm queue."
        fi
    elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        if ! sudo -n env \
            NB_USER="${NB_USER:-jovyan}" \
            NEURODESKTOP_SLURM_ENABLE="${NEURODESKTOP_SLURM_ENABLE:-1}" \
            NEURODESKTOP_SLURM_MEMORY_RESERVE_MB="${NEURODESKTOP_SLURM_MEMORY_RESERVE_MB:-256}" \
            NEURODESKTOP_SLURM_PARTITION="${NEURODESKTOP_SLURM_PARTITION:-neurodesktop}" \
            NEURODESKTOP_MUNGE_NUM_THREADS="${NEURODESKTOP_MUNGE_NUM_THREADS:-10}" \
            NEURODESKTOP_SLURM_USE_CGROUP="${NEURODESKTOP_SLURM_USE_CGROUP:-auto}" \
            NEURODESKTOP_SLURM_CGROUP_PLUGIN="${NEURODESKTOP_SLURM_CGROUP_PLUGIN:-autodetect}" \
            NEURODESKTOP_SLURM_CGROUP_MOUNTPOINT="${NEURODESKTOP_SLURM_CGROUP_MOUNTPOINT:-/sys/fs/cgroup}" \
            /opt/neurodesktop/setup_and_start_slurm.sh; then
            echo "[WARN] Failed to configure/start local Slurm queue via passwordless sudo."
        fi
    else
        echo "[WARN] Not running as root and passwordless sudo is unavailable; skipping local Slurm startup."
    fi
else
    echo "[INFO] NEURODESKTOP_SLURM_STARTUP_MODE=lazy: deferring Slurm startup to background worker."
fi

# Save the user-provided CVMFS_DISABLE before environment_variables.sh may
# auto-set it to true (because CVMFS isn't mounted yet in lazy mode).
_ORIG_CVMFS_DISABLE="${CVMFS_DISABLE:-false}"

# Image-installed or user-selected dependency is unavailable in the checkout.
# shellcheck source=/dev/null
source /opt/neurodesktop/environment_variables.sh > /dev/null 2>&1

# Keep extension startup bounded when the configured Ollama endpoint is unreachable.
# shellcheck source=/dev/null
source /opt/neurodesktop/guard_ollama_host.sh

_phase_end "critical-startup"

# RDP backend is started on-demand by guacamole.sh when the desktop is opened.

# Launch deferred startup worker for lazy CVMFS and/or Slurm.
# Pass the original CVMFS_DISABLE so the worker ignores the auto-set value.
# MODULEPATH in the Jupyter server's env will be local-only in lazy mode,
# but kernels re-source environment_variables.sh on spawn via the patched
# kernel.json (see Dockerfile), so they pick up the CVMFS MODULEPATH once
# the deferred worker has mounted CVMFS.
CVMFS_STARTUP_MODE="${NEURODESKTOP_CVMFS_STARTUP_MODE:-lazy}"
if [ "$CVMFS_STARTUP_MODE" = "lazy" ] || [ "$SLURM_STARTUP_MODE" = "lazy" ]; then
    if [ -x /opt/neurodesktop/deferred_startup.sh ]; then
        echo "[INFO] Launching deferred startup worker (CVMFS=$CVMFS_STARTUP_MODE, Slurm=$SLURM_STARTUP_MODE)..."
        CVMFS_DISABLE="${_ORIG_CVMFS_DISABLE}" /opt/neurodesktop/deferred_startup.sh &
    else
        echo "[WARN] /opt/neurodesktop/deferred_startup.sh not found. Deferred startup skipped."
    fi
fi

# Reprint the Jupyter access URL once the server actually answers: the
# ServerApp's own token banner scrolls away behind extension startup logs.
if [ "${NEURODESKTOP_RUNTIME:-container}" != glass ] && [ -x /opt/neurodesktop/print_access_url.sh ]; then
    /opt/neurodesktop/print_access_url.sh &
fi

# Ensure the VNC password file has the correct permissions
_vnc_passwd_path="${HOME:-/home/${NB_USER}}/.vnc/passwd"
if [ -f "${_vnc_passwd_path}" ] && [ "$(stat -c %a "${_vnc_passwd_path}")" != "600" ]; then
    chmod 600 "${_vnc_passwd_path}"
fi
unset _vnc_passwd_path

apply_chown_if_needed() {
    local dir="$1"
    local current_uid current_gid
    local -a chown_runner

    # If running in Apptainer/Singularity, we likely don't want to mess with chown
    if is_apptainer_runtime; then
        return
    fi

    if [ "$EUID" -eq 0 ]; then
        chown_runner=(chown)
    elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        chown_runner=(sudo -n chown)
    else
        echo "[WARN] Unable to fix ownership of ${dir}: requires root or passwordless sudo."
        return
    fi

    if [ -d "${dir}" ]; then
        current_uid=$(stat -c "%u" "${dir}")
        current_gid=$(stat -c "%g" "${dir}")
        if [ "${current_uid}" != "${NB_UID}" ] || [ "${current_gid}" != "${NB_GID}" ]; then
            if ! can_chown_path_with_runner "${dir}" "${chown_runner[@]}"; then
                echo "[WARN] Skipping ownership fix for ${dir}: chown unsupported in this runtime/filesystem."
                return
            fi
            if ! "${chown_runner[@]}" -R "${NB_UID}:${NB_GID}" "${dir}"; then
                echo "[WARN] Failed to fix ownership of ${dir}."
            fi
        fi
    fi
}
apply_chown_if_needed "/etc/guacamole"
apply_chown_if_needed "/usr/local/tomcat"

# Run user-level startup tasks once before Jupyter server initialization.
if [ "$EUID" -eq 0 ]; then
    sudo -H -u "${NB_USER}" \
        NB_USER="${NB_USER}" \
        NB_UID="${NB_UID}" \
        NB_GID="${NB_GID}" \
        HOSTNAME="${HOSTNAME}" \
        NEURODESKTOP_RUNTIME="${NEURODESKTOP_RUNTIME:-container}" \
        /opt/neurodesktop/jupyterlab_startup.sh
else
    /opt/neurodesktop/jupyterlab_startup.sh
fi
