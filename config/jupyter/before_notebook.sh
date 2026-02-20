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

                # Probe a server with multiple requests and return the median latency.
                # Using multiple probes reduces noise from transient network conditions.
                # Uses --no-keepalive so each probe opens a fresh connection,
                # matching real CVMFS client behaviour.
                get_latency() {
                    local url="$1"
                    local server_name="$2"
                    local num_probes=3
                    echo "Testing $url ($num_probes probes)" >&2
                    local resolved_dns
                    resolved_dns=$(dig +short "$server_name" 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]*$//')
                    echo "[DEBUG]: Resolved DNS for $server_name: $resolved_dns" >&2
                    local latencies=()
                    local i
                    for i in $(seq 1 "$num_probes"); do
                        local output exit_code
                        output=$(curl --no-keepalive --connect-timeout 3 -s -w "%{time_total} %{http_code}" -o /dev/null "$url")
                        exit_code=$?
                        if [ $exit_code -eq 0 ]; then
                            local time status
                            time=$(echo "$output" | awk '{print $1}')
                            status=$(echo "$output" | awk '{print $2}')
                            if [ "$status" -eq 200 ]; then
                                latencies+=("$time")
                                echo "  Probe $i: ${time}s" >&2
                            else
                                echo "  Probe $i: HTTP $status (failed)" >&2
                                latencies+=("999")
                            fi
                        else
                            echo "  Probe $i: curl error $exit_code" >&2
                            latencies+=("999")
                        fi
                    done
                    # Return the median (middle value of sorted list)
                    printf '%s\n' "${latencies[@]}" | sort -n | sed -n "$((( num_probes + 1 ) / 2))p"
                }

                # Throughput tiebreaker: download the root catalog from a CVMFS
                # server and return the transfer time.  The catalog is typically
                # hundreds of KB to several MB, so this captures sustained
                # throughput rather than just connection-setup latency.
                get_throughput_time() {
                    local base_url="$1"   # e.g. http://server/cvmfs/neurodesk.ardc.edu.au
                    echo "Throughput test: $base_url" >&2
                    # Fetch .cvmfspublished and extract the root catalog hash (C line)
                    local published catalog_hash
                    published=$(curl --connect-timeout 5 -s "${base_url}/.cvmfspublished")
                    catalog_hash=$(echo "$published" | awk '/^C/{print substr($0,2); exit}')
                    if [ -z "$catalog_hash" ]; then
                        echo "  Could not parse catalog hash" >&2
                        echo "999"
                        return
                    fi
                    local prefix="${catalog_hash:0:2}"
                    local suffix="${catalog_hash:2}"
                    local catalog_url="${base_url}/data/${prefix}/${suffix}C"
                    echo "  Downloading catalog: $catalog_url" >&2
                    local output exit_code
                    output=$(curl --no-keepalive --connect-timeout 10 --max-time 30 -s \
                        -w "%{time_total} %{size_download} %{speed_download} %{http_code}" \
                        -o /dev/null "$catalog_url")
                    exit_code=$?
                    if [ $exit_code -eq 0 ]; then
                        local time size speed status
                        time=$(echo "$output" | awk '{print $1}')
                        size=$(echo "$output" | awk '{print $2}')
                        speed=$(echo "$output" | awk '{print $3}')
                        status=$(echo "$output" | awk '{print $4}')
                        if [ "$status" -eq 200 ] && [ "$size" != "0" ]; then
                            echo "  Throughput: $(awk "BEGIN {printf \"%.1f\", $speed/1024}") KB/s (${size} bytes in ${time}s)" >&2
                            echo "$time"
                            return
                        fi
                    fi
                    echo "  Catalog download failed (exit $exit_code)" >&2
                    echo "999"
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

                # Probe all regions in parallel so no region gets a timing
                # advantage from being tested first or last.
                _probe_tmpdir=$(mktemp -d)

                (
                    _lat=$(get_latency "$EUROPE_url" "$EUROPE_HOST")
                    if [ "$_lat" == "999" ]; then
                        echo "Primary Europe server failed, trying backup..." >&2
                        _lat=$(get_latency "$EUROPE_url_backup" "$EUROPE_HOST_BACKUP")
                    fi
                    echo "$_lat" > "$_probe_tmpdir/europe"
                ) &

                (
                    _lat=$(get_latency "$AMERICA_url" "$AMERICA_HOST")
                    if [ "$_lat" == "999" ]; then
                        echo "Primary America server failed, trying backup..." >&2
                        _lat=$(get_latency "$AMERICA_url_backup" "$AMERICA_HOST_BACKUP")
                    fi
                    echo "$_lat" > "$_probe_tmpdir/america"
                ) &

                (
                    _lat=$(get_latency "$ASIA_url" "$ASIA_HOST")
                    if [ "$_lat" == "999" ]; then
                        echo "Primary Asia server failed, trying backup..." >&2
                        _lat=$(get_latency "$ASIA_url_backup" "$ASIA_HOST_BACKUP")
                    fi
                    echo "$_lat" > "$_probe_tmpdir/asia"
                ) &

                wait

                EUROPE_latency=$(cat "$_probe_tmpdir/europe")
                AMERICA_latency=$(cat "$_probe_tmpdir/america")
                ASIA_latency=$(cat "$_probe_tmpdir/asia")
                rm -rf "$_probe_tmpdir"

                echo "Europe Latency (median of 3): ${EUROPE_latency}s"
                echo "America Latency (median of 3): ${AMERICA_latency}s"
                echo "Asia Latency (median of 3): ${ASIA_latency}s"

                # Rank regions by latency
                echo "Regional latencies (s): europe=${EUROPE_latency}, america=${AMERICA_latency}, asia=${ASIA_latency}"
                SORTED_REGIONS=$(printf "%s europe\n%s america\n%s asia\n" \
                    "$EUROPE_latency" "$AMERICA_latency" "$ASIA_latency" | sort -n)
                FASTEST_REGION=$(echo "$SORTED_REGIONS" | awk 'NR==1{print $2}')
                FASTEST_LATENCY=$(echo "$SORTED_REGIONS" | awk 'NR==1{print $1}')
                SECOND_REGION=$(echo "$SORTED_REGIONS" | awk 'NR==2{print $2}')
                SECOND_LATENCY=$(echo "$SORTED_REGIONS" | awk 'NR==2{print $1}')

                echo "Fastest region by latency: $FASTEST_REGION (${FASTEST_LATENCY}s), runner-up: $SECOND_REGION (${SECOND_LATENCY}s)"

                # When the margin is thin, latency alone is unreliable —
                # a far-away server can appear fast on a tiny probe but be
                # slower for actual bulk transfers. Run a throughput
                # tiebreaker by downloading the real CVMFS root catalog
                # (hundreds of KB – several MB) from each finalist.
                if [ "$FASTEST_LATENCY" != "999" ] && [ "$SECOND_LATENCY" != "999" ]; then
                    MARGIN=$(awk "BEGIN {if ($FASTEST_LATENCY > 0) printf \"%.2f\", ($SECOND_LATENCY - $FASTEST_LATENCY) / $FASTEST_LATENCY; else print 999}")
                    echo "Latency margin: $(awk "BEGIN {printf \"%.0f\", $MARGIN * 100}")%"

                    if [ "$(awk "BEGIN {print ($MARGIN < 0.40) ? 1 : 0}")" -eq 1 ]; then
                        echo "Margin is <40% — running throughput tiebreaker between $FASTEST_REGION and $SECOND_REGION..."

                        # Map region name back to its base URL
                        _region_to_base_url() {
                            case "$1" in
                                europe)  echo "http://${EUROPE_HOST}/cvmfs/neurodesk.ardc.edu.au" ;;
                                america) echo "http://${AMERICA_HOST}/cvmfs/neurodesk.ardc.edu.au" ;;
                                asia)    echo "http://${ASIA_HOST}/cvmfs/neurodesk.ardc.edu.au" ;;
                            esac
                        }

                        _tp_tmpdir=$(mktemp -d)
                        (get_throughput_time "$(_region_to_base_url "$FASTEST_REGION")" > "$_tp_tmpdir/first") &
                        (get_throughput_time "$(_region_to_base_url "$SECOND_REGION")" > "$_tp_tmpdir/second") &
                        wait

                        FIRST_TP=$(cat "$_tp_tmpdir/first")
                        SECOND_TP=$(cat "$_tp_tmpdir/second")
                        rm -rf "$_tp_tmpdir"

                        echo "Throughput time for $FASTEST_REGION: ${FIRST_TP}s"
                        echo "Throughput time for $SECOND_REGION: ${SECOND_TP}s"

                        if [ "$FIRST_TP" != "999" ] && [ "$SECOND_TP" != "999" ]; then
                            if [ "$(awk "BEGIN {print ($SECOND_TP < $FIRST_TP) ? 1 : 0}")" -eq 1 ]; then
                                echo "Throughput tiebreaker: switching from $FASTEST_REGION to $SECOND_REGION (faster bulk transfer)"
                                FASTEST_REGION="$SECOND_REGION"
                            else
                                echo "Throughput tiebreaker confirms $FASTEST_REGION"
                            fi
                        else
                            echo "Throughput tiebreaker inconclusive — keeping $FASTEST_REGION"
                        fi
                    fi
                fi

                # Probe Direct vs CDN modes in parallel
                echo "Probing connection modes (Direct vs CDN)..."
                DIRECT_HOST=cvmfs-geoproximity.neurodesk.org
                CDN_HOST=cvmfs.neurodesk.org

                DIRECT_url="http://${DIRECT_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
                CDN_url="http://${CDN_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"

                _mode_tmpdir=$(mktemp -d)
                (get_latency "$DIRECT_url" "$DIRECT_HOST" > "$_mode_tmpdir/direct") &
                (get_latency "$CDN_url" "$CDN_HOST" > "$_mode_tmpdir/cdn") &
                wait

                DIRECT_latency=$(cat "$_mode_tmpdir/direct")
                CDN_latency=$(cat "$_mode_tmpdir/cdn")
                rm -rf "$_mode_tmpdir"

                echo "Direct Latency (median of 3): ${DIRECT_latency}s"
                echo "CDN Latency (median of 3): ${CDN_latency}s"

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
