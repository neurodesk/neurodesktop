#!/bin/bash

# order: start_notebook.sh -> ### before_notebook.sh ### -> jupyterlab_startup.sh -> jupyter_notebook_config.py

fix_home_ownership_if_needed() {
    local home_dir="/home/${NB_USER}"

    if [ ! -d "$home_dir" ]; then
        return
    fi

    local current_uid
    local current_gid
    current_uid=$(stat -c "%u" "$home_dir")
    current_gid=$(stat -c "%g" "$home_dir")

    if [ "$current_uid" = "$NB_UID" ] && [ "$current_gid" = "$NB_GID" ]; then
        return
    fi

    echo "Fixing ownership of $home_dir (was $current_uid:$current_gid, setting to $NB_UID:$NB_GID)"
    if [ "$EUID" -eq 0 ]; then
        chown "$NB_UID:$NB_GID" "$home_dir"
    elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        sudo -n chown "$NB_UID:$NB_GID" "$home_dir"
    else
        echo "[WARN] Unable to fix $home_dir ownership: requires root or passwordless sudo."
    fi
}

link_data_dir_if_present() {
    local source_dir="/data"
    local home_dir="/home/${NB_USER}"
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
    # # Overrides Dockerfile changes to NB_USER
    # Keep startup non-interactive and avoid passwd prompt noise in logs.
    echo "${NB_USER}:password" | chpasswd
    if [ "$(getent passwd "${NB_USER}" | cut -d: -f7)" != "/bin/bash" ]; then
        usermod --shell /bin/bash "${NB_USER}"
    fi

    # Make sure binfmt_misc is mounted in the place apptainer expects it. This is most likely a bug in apptainer and is a workaround for now on apple silicon when CVMFS is disabled.
    if [ -d "/proc/sys/fs/binfmt_misc" ]; then
        # Check if binfmt_misc is already mounted
        if ! mountpoint -q /proc/sys/fs/binfmt_misc; then
            echo "binfmt_misc directory exists but is not mounted. Mounting now..."
            sudo mount -t binfmt_misc binfmt /proc/sys/fs/binfmt_misc
        else
            echo "binfmt_misc is already mounted."
        fi
    else
        echo "binfmt_misc directory does not exist in /proc/sys/fs."
    fi

    if [ ! -d "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/" ]; then
        # the cvmfs directory is not yet mounted

        # check if we have internet connectivity:
        if nslookup neurodesk.org >/dev/null; then
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
            chmod 755 ${HOME}
            # The cache directory needs to be owned by cvmfs user and group
            if sudo -n true 2>/dev/null; then
                chown -R cvmfs:root "$CACHE_DIR"
            fi

            # try to list the directory in case it's autofs mounted outside
            ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ 2>/dev/null && echo "CVMFS is ready" || echo "CVMFS directory not there. Trying internal fuse mount next."

            if [ ! -d "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/" ]; then
                # it is not available outside, so try mounting with fuse inside container
                # Function to get latency, returns 999 on failure
                get_latency() {
                    local url="$1"
                    local server_name="$2"
                    # Redirect informational output to stderr
                    echo "Testing $url" >&2
                    echo "Resolving DNS name for $server_name" >&2
                    local resolved_dns
                    resolved_dns=$(dig +short "$server_name" | tr '\n' ' ' | sed 's/[[:space:]]*$//')
                    # Redirect debug output to stderr
                    echo "[DEBUG]: Resolved DNS for $server_name: $resolved_dns" >&2
                    local output
                    local exit_code
                    # Curl output format captures time and status code
                    output=$(curl --connect-timeout 5 -s -w "%{time_total} %{http_code}" -o /dev/null "$url")
                    exit_code=$?
                    if [ $exit_code -eq 0 ]; then
                        local time
                        local status
                        time=$(echo "$output" | awk '{print $1}')
                        status=$(echo "$output" | awk '{print $2}')
                        if [ "$status" -eq 200 ]; then
                            # Echo latency to stdout (captured by command substitution)
                            echo "$time"
                        else
                            # Redirect error message to stderr
                            echo "Curl request to $url failed with HTTP status $status" >&2
                            # Echo fallback value to stdout (captured by command substitution)
                            echo "999"
                        fi
                    else
                        # Handle curl specific errors (e.g., timeout, DNS resolution failure)
                        # Redirect error message to stderr
                        echo "Curl command failed for $url with exit code $exit_code" >&2
                        # Check for timeout error (exit code 28)
                        if [ $exit_code -eq 28 ]; then
                            # Redirect error message to stderr
                            echo "Curl request timed out for $url" >&2
                        fi
                        # Echo fallback value to stdout (captured by command substitution)
                        echo "999"
                    fi
                }

                echo "Probing regional servers (Europe, America, Asia)..."
                EUROPE_HOST=cvmfs-frankfurt.neurodesk.org
                EUROPE_HOST_BACKUP=cvmfs01.nikhef.nl
                AMERICA_HOST=cvmfs-jetstream.neurodesk.org
                AMERICA_HOST_BACKUP=cvmfs-s1bnl.opensciencegrid.org
                ASIA_HOST=cvmfs-brisbane.neurodesk.org
                ASIA_HOST_BACKUP=cvmfs-perth.neurodesk.org

                EUROPE_url="http://${EUROPE_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
                AMERICA_url="http://${AMERICA_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
                ASIA_url="http://${ASIA_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"

                EUROPE_url_backup="http://${EUROPE_HOST_BACKUP}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
                AMERICA_url_backup="http://${AMERICA_HOST_BACKUP}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
                ASIA_url_backup="http://${ASIA_HOST_BACKUP}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"

                EUROPE_latency=$(get_latency "$EUROPE_url" "$EUROPE_HOST")
                # check if latency is 999, if so, try the backup server
                if [ "$EUROPE_latency" == "999" ]; then
                    echo "Primary Europe server failed, trying backup..."
                    EUROPE_latency=$(get_latency "$EUROPE_url_backup" "$EUROPE_HOST_BACKUP")
                fi
                echo "Europe Latency: $EUROPE_latency"


                AMERICA_latency=$(get_latency "$AMERICA_url" "$AMERICA_HOST")
                # check if latency is 999, if so, try the backup server
                if [ "$AMERICA_latency" == "999" ]; then
                    echo "Primary America server failed, trying backup..."
                    AMERICA_latency=$(get_latency "$AMERICA_url_backup" "$AMERICA_HOST_BACKUP")
                fi
                echo "America Latency: $AMERICA_latency"
                
                
                ASIA_latency=$(get_latency "$ASIA_url" "$ASIA_HOST")
                # check if latency is 999, if so, try the backup server
                if [ "$ASIA_latency" == "999" ]; then
                    echo "Primary Asia server failed, trying backup..."
                    ASIA_latency=$(get_latency "$ASIA_url_backup" "$ASIA_HOST_BACKUP")
                fi
                echo "Asia Latency: $ASIA_latency"

                # Find the fastest region
                echo "Regional latencies (s): europe=${EUROPE_latency}, america=${AMERICA_latency}, asia=${ASIA_latency}"
                FASTEST_REGION=$(printf "%s europe\n%s america\n%s asia\n" "$EUROPE_latency" "$AMERICA_latency" "$ASIA_latency" | sort -n | head -n 1 | awk '{print $2}')

                echo "Probing connection modes (Direct vs CDN)..."
                DIRECT_HOST=cvmfs-geoproximity.neurodesk.org
                CDN_HOST=cvmfs.neurodesk.org

                DIRECT_url="http://${DIRECT_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
                CDN_url="http://${CDN_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"

                DIRECT_latency=$(get_latency "$DIRECT_url" "$DIRECT_HOST")
                echo "Direct Latency: $DIRECT_latency"
                CDN_latency=$(get_latency "$CDN_url" "$CDN_HOST")
                echo "CDN Latency: $CDN_latency"

                # Determine the fastest mode
                FASTEST_MODE=$(printf "%s direct\n%s cdn\n" "$DIRECT_latency" "$CDN_latency" | sort -n | head -n 1 | awk '{print $2}')

                echo "Fastest region determined: $FASTEST_REGION"
                echo "Fastest mode determined: $FASTEST_MODE"

                # copying the selected config file
                config_file_suffix="${FASTEST_MODE}.${FASTEST_REGION}"
                source_config="/etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.${config_file_suffix}"
                target_config="/etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf"

                if [ -f "$source_config" ]; then
                    echo "Selected config file: $source_config"
                    cp "$source_config" "$target_config"
                    # else
                    #     echo "Warning: Config file $source_config not found. Using default."
                    # cp /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.default $target_config
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
                    if [ "$FASTEST_MODE" = "direct" ]; then
                        cvmfs_talk -i neurodesk.ardc.edu.au host probe
                    fi
                    cvmfs_talk -i neurodesk.ardc.edu.au host info
                fi
            fi
        fi
    fi
fi

# Source custom scripts in .bashrc if they are not already there
BASHRC_FILE="/home/${NB_USER}/.bashrc"
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

# Read cgroup v2 limits and set environment variables for jupyter-resource-usage
echo "Detecting container resource limits from cgroup v2..."
if [ -f "/sys/fs/cgroup/memory.max" ]; then
    CGROUP_MEM_LIMIT=$(cat /sys/fs/cgroup/memory.max)
    # Check if it's not "max" (unlimited)
    if [ "$CGROUP_MEM_LIMIT" != "max" ]; then
        export MEM_LIMIT="$CGROUP_MEM_LIMIT"
        echo "Memory limit detected: $(numfmt --to=iec "$CGROUP_MEM_LIMIT")"
    else
        echo "Memory limit: unlimited"
    fi
else
    echo "cgroup v2 memory.max not found (may be running on older kernel or non-Linux system)"
fi

if [ -f "/sys/fs/cgroup/cpu.max" ]; then
    # cpu.max format: "$MAX $PERIOD" (e.g., "200000 100000" = 2 CPUs)
    CPU_MAX_LINE=$(cat /sys/fs/cgroup/cpu.max)
    if [ "$CPU_MAX_LINE" != "max 100000" ]; then
        CPU_QUOTA=$(echo "$CPU_MAX_LINE" | awk '{print $1}')
        CPU_PERIOD=$(echo "$CPU_MAX_LINE" | awk '{print $2}')
        # Calculate CPU limit as a decimal (e.g., 2.0 for 2 CPUs)
        CPU_LIMIT=$(awk "BEGIN {printf \"%.2f\", $CPU_QUOTA/$CPU_PERIOD}")
        export CPU_LIMIT="$CPU_LIMIT"
        echo "CPU limit detected: $CPU_LIMIT CPUs"
    else
        echo "CPU limit: unlimited"
    fi
else
    echo "cgroup v2 cpu.max not found (may be running on older kernel or non-Linux system)"
fi

# SLURM limit detection (overrides cgroup limits if present)
if [ -n "$SLURM_JOB_ID" ]; then
    echo "Running inside a SLURM job (Job ID: $SLURM_JOB_ID). Detecting SLURM limits..."
    
    # Memory Limit
    if [ -n "$SLURM_MEM_PER_NODE" ]; then
        # SLURM_MEM_PER_NODE is in MB
        echo "SLURM_MEM_PER_NODE detected: ${SLURM_MEM_PER_NODE} MB"
        export MEM_LIMIT=$(($SLURM_MEM_PER_NODE * 1024 * 1024))
    elif [ -n "$SLURM_MEM_PER_CPU" ] && [ -n "$SLURM_JOB_CPUS_PER_NODE" ]; then
        echo "SLURM_MEM_PER_CPU detected: ${SLURM_MEM_PER_CPU} MB"
        # Extract the number of CPUs on the first node (simplification)
        CPU_ALLOC=$(echo "$SLURM_JOB_CPUS_PER_NODE" | sed 's/(.*//') 
        if [[ "$CPU_ALLOC" =~ ^[0-9]+$ ]]; then
            export MEM_LIMIT=$(($SLURM_MEM_PER_CPU * $CPU_ALLOC * 1024 * 1024))
        fi
    fi
    if [ -n "$MEM_LIMIT" ]; then
        echo "Memory limit set from SLURM: $(numfmt --to=iec "$MEM_LIMIT")"
    fi

    # CPU Limit
    if [ -n "$SLURM_CPUS_PER_TASK" ]; then
        export CPU_LIMIT="$SLURM_CPUS_PER_TASK"
        echo "SLURM_CPUS_PER_TASK detected: $CPU_LIMIT"
    elif [ -n "$SLURM_JOB_CPUS_PER_NODE" ]; then
        # Extract the number of CPUs on the first node (simplification)
        CPU_ALLOC=$(echo "$SLURM_JOB_CPUS_PER_NODE" | sed 's/(.*//')
        if [[ "$CPU_ALLOC" =~ ^[0-9]+$ ]]; then
            export CPU_LIMIT="$CPU_ALLOC"
            echo "SLURM_JOB_CPUS_PER_NODE detected: $CPU_LIMIT"
        fi
    fi
fi

# Start a local single-node Slurm queue inside the container.
# In auto mode, Slurm defaults to non-cgroup compatibility settings unless explicitly enabled.
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

source /opt/neurodesktop/environment_variables.sh > /dev/null 2>&1

# Set default value for START_LOCAL_LLMS
if [ -v START_LOCAL_LLMS ] && [ "$START_LOCAL_LLMS" -eq 1 ]; then
    # Local LLM mode must target the in-container Ollama daemon.
    export OLLAMA_HOST="http://127.0.0.1:11434"

    # Check if Ollama is installed
    if ! command -v ollama &>/dev/null; then
        echo "Ollama is not installed. Installing Ollama..."
        wget -qO- https://ollama.com/install.sh | bash
    fi

    # Start the Ollama server in the background
    if ! pgrep -x "ollama" >/dev/null; then
        ollama serve &
        echo "Waiting for Ollama server to start..."
        sleep 20
    fi

    # Download the neurodesk.gguf file if it doesn't exist
    if [ ! -f "neurodesk.gguf" ]; then
        wget -O neurodesk.gguf \
            "https://huggingface.co/jnikhilreddy/neurodesk-gguf/resolve/main/neurodesk.gguf?download=true"

        # Create the Modelfile
        cat <<'EOL' >Modelfile
FROM ./neurodesk.gguf
EOL
    fi

    # Create the neurodesk model to serve using ollama
    ollama create neurodesk -f Modelfile

    ollama run neurodesk &
    ollama run codellama:7b-code &

    echo "================================="
    echo "LLM Setup Complete:"
    echo "Ollama server running on port 11434"
    echo "================================="
fi
# Ensure the VNC password file has the correct permissions
if [ -f "/home/${NB_USER}/.vnc/passwd" ] && [ "$(stat -c %a /home/${NB_USER}/.vnc/passwd)" != "600" ]; then
    chmod 600 "/home/${NB_USER}/.vnc/passwd"
fi

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
            chown -R "${NB_UID}:${NB_GID}" "$dir"
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
        /opt/neurodesktop/jupyterlab_startup.sh
else
    /opt/neurodesktop/jupyterlab_startup.sh
fi
