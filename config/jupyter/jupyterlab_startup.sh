#!/bin/bash
# order: start_notebook.sh -> before_notebook.sh -> jupyter_notebook_config.py -> #### jupyterlab_startup.sh ####

# Copy homedirectory files if they don't exist yet
# Check for missing conda-readme.md in persisting homedir
if [ ! -f "${HOME}/conda-readme.md" ] 
then
    mkdir -p ${HOME}
    if sudo -n true 2>/dev/null; then
        sudo cp -rpn /tmp/${NB_USER} "$(dirname "${HOME}")"
    fi
fi

# Function to check and apply chown if necessary
apply_chown_if_needed() {
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

chmod -R 700 ${HOME}/.ssh

# # Set .ssh directory permissions
# chmod -R 700 /home/${NB_USER}/.ssh
# chown -R ${NB_UID}:${NB_GID} /home/${NB_USER}/.ssh
# setfacl -dRm u::rwx,g::0,o::0 /home/${NB_USER}/.ssh

# Generate SSH keys
if [ ! -f "/home/${NB_USER}/.ssh/guacamole_rsa" ]; then
    ssh-keygen -t rsa -f /home/${NB_USER}/.ssh/guacamole_rsa -b 4096 -m PEM -N '' -C guacamole@sftp-server <<< n
fi
if [ ! -f "/home/${NB_USER}/.ssh/id_rsa" ]; then
    ssh-keygen -t rsa -f /home/${NB_USER}/.ssh/id_rsa -b 4096 -m PEM -N '' <<< n
fi
if [ ! -f "/home/${NB_USER}/.ssh/ssh_host_rsa_key" ]; then
    ssh-keygen -t rsa -f /home/${NB_USER}/.ssh/ssh_host_rsa_key -N '' <<< n
fi
if ! grep "guacamole@sftp-server" /home/${NB_USER}/.ssh/authorized_keys
then
    cat /home/${NB_USER}/.ssh/guacamole_rsa.pub >> /home/${NB_USER}/.ssh/authorized_keys
fi
if ! grep "${NB_USER}@${HOSTNAME}" /home/${NB_USER}/.ssh/authorized_keys
then
    cat /home/${NB_USER}/.ssh/id_rsa.pub >> /home/${NB_USER}/.ssh/authorized_keys
fi

if sudo -n true 2>/dev/null; then
    ln -sf /etc/guacamole/user-mapping-vnc-rdp.xml /etc/guacamole/user-mapping.xml
fi

# Insert guacamole private key into user-mapping for ssh/sftp support
if ! grep 'BEGIN RSA PRIVATE KEY' /etc/guacamole/user-mapping.xml; then
    sed -i "/private-key/ r /home/${NB_USER}/.ssh/guacamole_rsa" /etc/guacamole/user-mapping.xml
fi

# Create a symlink in home if /data is mounted
if mountpoint -q /data; then
    if [ ! -L "/home/${NB_USER}/data" ]; then
        ln -s /data /home/${NB_USER}/
    fi
fi

# Create a symlink to /neurodesktop-storage in home if it is mounted
if mountpoint -q /neurodesktop-storage/; then
    if [ ! -L "/home/${NB_USER}/neurodesktop-storage" ]; then
        ln -s /neurodesktop-storage/ /home/${NB_USER}/
    fi
else
    if [ ! -L "/neurodesktop-storage" ]; then
        if [ ! -d "/home/${NB_USER}/neurodesktop-storage/" ]; then
            mkdir -p /home/${NB_USER}/neurodesktop-storage/containers
        fi
        if [ ! -L "/neurodesktop-storage" ]; then
            if sudo -n true 2>/dev/null; then
                sudo ln -s /home/${NB_USER}/neurodesktop-storage/ /neurodesktop-storage
            fi
        fi
    fi
fi

# Create a symlink to the neurodesktop-storage directory if it doesn't exist yet:
if [ ! -L "/neurocommand/local/containers" ]; then
  ln -s "/home/${NB_USER}/neurodesktop-storage/containers" "/neurocommand/local/containers"
fi

# Create a cpufino file with a valid CPU Mhz entry for ARM cpus
echo "[INFO] Checking for ARM CPU and adding a CPU Mhz entry in /proc/cpuinfo to work around a bug in Matlab that expects this value to be present."
if ! grep -iq 'cpu.*hz' /proc/cpuinfo; then
    mkdir -p /home/${NB_USER}/.local
    cpuinfo_file=/home/${NB_USER}/.local/cpuinfo_with_ARM_MHz_fix
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

# Start and stop SSH server to initialize host
if sudo -n true 2>/dev/null; then
    sudo service ssh restart
    sudo service ssh stop
fi

conda init bash
mamba init bash

# Create and setup .vnc dir if not existing
if [ ! -d "/home/${NB_USER}/.vnc" ]; then
    mkdir /home/${NB_USER}/.vnc \
        && chown ${NB_USER} /home/${NB_USER}/.vnc \
        && /usr/bin/printf '%s\n%s\n%s\n' 'password' 'password' 'n' | vncpasswd

    printf '%s\n' '#!/bin/sh' '/usr/bin/startlxde' 'vncconfig -nowin -noiconic &' > "/home/${NB_USER}/.vnc/xstartup"
    chmod +x "/home/${NB_USER}/.vnc/xstartup"
fi
