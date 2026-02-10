#!/bin/bash
# order: start_notebook.sh -> before_notebook.sh -> #### jupyterlab_startup.sh #### -> jupyter_notebook_config.py

# Prevent duplicate execution during the same container lifetime.
STARTUP_LOCK_DIR="/tmp/neurodesktop-jupyterlab-startup.lock"
STARTUP_DONE_FILE="/tmp/neurodesktop-jupyterlab-startup.done"

if [ -f "$STARTUP_DONE_FILE" ]; then
    echo "[INFO] jupyterlab_startup already completed. Skipping."
    exit 0
fi

if ! mkdir "$STARTUP_LOCK_DIR" 2>/dev/null; then
    echo "[INFO] jupyterlab_startup is already running. Skipping duplicate invocation."
    exit 0
fi

cleanup_startup_lock() {
    rmdir "$STARTUP_LOCK_DIR" 2>/dev/null || true
}
trap cleanup_startup_lock EXIT

# Restore default home directory files (per-file, not bulk copy)
# Each file is only copied if it doesn't already exist
source /opt/neurodesktop/restore_home_defaults.sh

# Function to check and apply chown if necessary
apply_chown_if_needed() {
    # If running in Apptainer/Singularity, we likely don't want to mess with chown
    if [ -n "$SINGULARITY_NAME" ] || [ -n "$APPTAINER_NAME" ] || [ -n "$APPTAINER_CONTAINER" ] || [ -n "$SINGULARITY_CONTAINER" ]; then
        return
    fi

    local dir=$1
    local recursive=$2
    if [ -d "$dir" ]; then
        current_uid=$(stat -c "%u" "$dir")
        current_gid=$(stat -c "%g" "$dir")
        if [ "$current_uid" != "$NB_UID" ] || [ "$current_gid" != "$NB_GID" ]; then
            if [ "$recursive" = true ]; then
                chown -R ${NB_UID}:${NB_GID} "$dir"
            else
                chown ${NB_UID}:${NB_GID} "$dir"
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

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"

# # Set .ssh directory permissions
# chmod -R 700 /home/${NB_USER}/.ssh
# chown -R ${NB_UID}:${NB_GID} /home/${NB_USER}/.ssh
# setfacl -dRm u::rwx,g::0,o::0 /home/${NB_USER}/.ssh

# Generate SSH keys
if [ ! -f "${HOME}/.ssh/guacamole_rsa" ]; then
    ssh-keygen -q -t rsa -f "${HOME}/.ssh/guacamole_rsa" -b 4096 -m PEM -N '' -C "guacamole@sftp-server"
fi
if [ ! -f "${HOME}/.ssh/id_rsa" ]; then
    ssh-keygen -q -t rsa -f "${HOME}/.ssh/id_rsa" -b 4096 -m PEM -N ''
fi
if [ ! -f "${HOME}/.ssh/ssh_host_rsa_key" ]; then
    ssh-keygen -q -t rsa -f "${HOME}/.ssh/ssh_host_rsa_key" -N ''
fi

AUTHORIZED_KEYS_FILE="${HOME}/.ssh/authorized_keys"
touch "$AUTHORIZED_KEYS_FILE"
chmod 600 "$AUTHORIZED_KEYS_FILE"

if ! grep -qF "guacamole@sftp-server" "$AUTHORIZED_KEYS_FILE"; then
    cat "${HOME}/.ssh/guacamole_rsa.pub" >> "$AUTHORIZED_KEYS_FILE"
fi
if ! grep -qF "${NB_USER}@${HOSTNAME}" "$AUTHORIZED_KEYS_FILE"; then
    cat "${HOME}/.ssh/id_rsa.pub" >> "$AUTHORIZED_KEYS_FILE"
fi

# Fix sshd_config paths
if [ -f "${HOME}/.ssh/sshd_config" ]; then
    sed -i "s|/home/jovyan|${HOME}|g" "${HOME}/.ssh/sshd_config"
fi

if sudo -n true 2>/dev/null; then
    ln -sf /etc/guacamole/user-mapping-vnc-rdp.xml /etc/guacamole/user-mapping.xml
fi

# Insert guacamole private key into user-mapping for ssh/sftp support
if ! grep 'BEGIN RSA PRIVATE KEY' /etc/guacamole/user-mapping.xml; then
    sed -i "/private-key/ r ${HOME}/.ssh/guacamole_rsa" /etc/guacamole/user-mapping.xml
fi

# Create a symlink in home if /data is mounted
if mountpoint -q /data; then
    if [ ! -L "${HOME}/data" ]; then
        ln -s /data ${HOME}/
    fi
fi

# Returns success if directory exists and has no entries.
dir_is_empty() {
    local dir="$1"
    [ -d "$dir" ] && [ -z "$(find "$dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]
}

NEURODESKTOP_HOME_STORAGE="${HOME}/neurodesktop-storage"
NEURODESKTOP_ROOT_STORAGE="/neurodesktop-storage"

# Create/repair neurodesktop-storage links.
if mountpoint -q "${NEURODESKTOP_ROOT_STORAGE}"; then
    if [ -L "${NEURODESKTOP_HOME_STORAGE}" ]; then
        :
    elif [ ! -e "${NEURODESKTOP_HOME_STORAGE}" ]; then
        ln -s "${NEURODESKTOP_ROOT_STORAGE}/" "${NEURODESKTOP_HOME_STORAGE}"
    elif dir_is_empty "${NEURODESKTOP_HOME_STORAGE}"; then
        rmdir "${NEURODESKTOP_HOME_STORAGE}" \
            && ln -s "${NEURODESKTOP_ROOT_STORAGE}/" "${NEURODESKTOP_HOME_STORAGE}"
    else
        echo "[WARN] ${NEURODESKTOP_HOME_STORAGE} exists and is not a symlink; leaving it unchanged."
    fi
else
    if [ ! -d "${NEURODESKTOP_HOME_STORAGE}" ]; then
        mkdir -p "${NEURODESKTOP_HOME_STORAGE}/containers"
    fi

    if [ ! -L "${NEURODESKTOP_ROOT_STORAGE}" ] && sudo -n true 2>/dev/null; then
        if [ -d "${NEURODESKTOP_ROOT_STORAGE}" ]; then
            nested_link="${NEURODESKTOP_ROOT_STORAGE}/neurodesktop-storage"
            nested_target=$(sudo readlink "${nested_link}" 2>/dev/null || true)

            # Repair previous broken state: /neurodesktop-storage/neurodesktop-storage -> $HOME/neurodesktop-storage
            if [ -L "${nested_link}" ] \
                && [ -z "$(sudo find "${NEURODESKTOP_ROOT_STORAGE}" -mindepth 1 -maxdepth 1 ! -name neurodesktop-storage -print -quit 2>/dev/null)" ] \
                && { [ "${nested_target}" = "${NEURODESKTOP_HOME_STORAGE}/" ] || [ "${nested_target}" = "${NEURODESKTOP_HOME_STORAGE}" ]; }; then
                sudo rm -f "${nested_link}"
            fi

            if [ -z "$(sudo find "${NEURODESKTOP_ROOT_STORAGE}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
                sudo rmdir "${NEURODESKTOP_ROOT_STORAGE}" \
                    && sudo ln -s "${NEURODESKTOP_HOME_STORAGE}/" "${NEURODESKTOP_ROOT_STORAGE}"
            else
                echo "[WARN] ${NEURODESKTOP_ROOT_STORAGE} exists as non-empty directory; leaving it unchanged."
            fi
        elif [ ! -e "${NEURODESKTOP_ROOT_STORAGE}" ]; then
            sudo ln -s "${NEURODESKTOP_HOME_STORAGE}/" "${NEURODESKTOP_ROOT_STORAGE}"
        else
            echo "[WARN] ${NEURODESKTOP_ROOT_STORAGE} exists and is not a symlink; leaving it unchanged."
        fi
    fi
fi

# Create a symlink to the neurodesktop-storage directory if it doesn't exist yet:
if [ ! -L "/neurocommand/local/containers" ]; then
  ln -s "${HOME}/neurodesktop-storage/containers" "/neurocommand/local/containers"
fi

# Create a cpufino file with a valid CPU Mhz entry for ARM cpus
echo "[INFO] Checking for ARM CPU and adding a CPU Mhz entry in /proc/cpuinfo to work around a bug in Matlab that expects this value to be present."
if ! grep -iq 'cpu.*hz' /proc/cpuinfo; then
    mkdir -p ${HOME}/.local
    cpuinfo_file=${HOME}/.local/cpuinfo_with_ARM_MHz_fix
    cp /proc/cpuinfo $cpuinfo_file
    chmod u+rw $cpuinfo_file
    sed -i '/^$/c\cpu MHz         : 2245.778\n' $cpuinfo_file
    # add vendor and model name as well:
    sed -i '/^$/c\vendor_id       : ARM\nmodel name      : Apple-M\n' $cpuinfo_file
    if sudo -n true 2>/dev/null; then
        sudo mount --bind $cpuinfo_file /proc/cpuinfo
    fi
    echo "[INFO] Added CPU Mhz entry in /proc/cpuinfo to work around a bug in Matlab that expects this value to be present."
fi

# ensure overlay directory exists
mkdir -p /tmp/apptainer_overlay

# ensure goose config directory exists
mkdir -p ${HOME}/.config/goose

# ensure opencode config directory exists
mkdir -p ${HOME}/.config/opencode

# Validate SSH config only; guacamole.sh starts sshd when needed.
if sudo -n true 2>/dev/null && [ -f "${HOME}/.ssh/sshd_config" ]; then
    # OpenSSH expects this runtime directory to exist for privilege separation.
    sudo mkdir -p /run/sshd
    sudo chmod 755 /run/sshd
    sudo /usr/sbin/sshd -t -f "${HOME}/.ssh/sshd_config" || \
        echo "[WARN] sshd_config validation failed: ${HOME}/.ssh/sshd_config"
fi

# Conda shell hooks are already provided by the base image/defaults.
# Avoid mutating shell config on each startup.

# Setup VNC directory and ensure files exist (should be restored from defaults)
echo "[INFO] Setting up VNC..."
mkdir -p "${HOME}/.vnc"
if sudo -n true 2>/dev/null; then
    chown "${NB_USER}" "${HOME}/.vnc"
fi

# Generate VNC password if not existing (fallback if restore failed)
if [ ! -f "${HOME}/.vnc/passwd" ]; then
    echo "[INFO] Generating VNC password (not found in restored defaults)..."
    /usr/bin/printf '%s\n%s\n%s\n' 'password' 'password' 'n' | vncpasswd
fi

# Create xstartup if not existing (fallback if restore failed)
if [ ! -f "${HOME}/.vnc/xstartup" ]; then
    echo "[INFO] Creating VNC xstartup (not found in restored defaults)..."
    printf '%s\n' '#!/bin/sh' '/usr/bin/startlxde' 'vncconfig -nowin -noiconic &' > "${HOME}/.vnc/xstartup"
fi

# Ensure correct permissions
chmod 600 "${HOME}/.vnc/passwd" 2>/dev/null || true
chmod +x "${HOME}/.vnc/xstartup"

# echo "[INFO] VNC setup complete. Contents of ${HOME}/.vnc:"
# ls -la "${HOME}/.vnc/"

touch "$STARTUP_DONE_FILE"
