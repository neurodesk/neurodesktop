#!/bin/bash

# order: start_notebook.sh -> ### before_notebook.sh ### -> jupyterlab_startup.sh -> jupyter_notebook_config.py

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
    local home_dir="/home/${NB_USER}"
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

# Read cgroup limits and set environment variables for jupyter-resource-usage
extract_first_uint() {
    local raw
    raw="$(printf '%s' "${1:-}" | tr -d '[:space:]')"
    if [[ "${raw}" =~ ^([0-9]+) ]]; then
        echo "${BASH_REMATCH[1]}"
        return 0
    fi
    return 1
}

parse_memory_to_mb() {
    local raw value unit
    raw="$(printf '%s' "${1:-}" | tr -d '[:space:]')"
    if [ -z "${raw}" ]; then
        return 1
    fi

    if [[ "${raw}" =~ ^([0-9]+)([KkMmGgTt]?)$ ]]; then
        value="${BASH_REMATCH[1]}"
        unit="${BASH_REMATCH[2]}"
    else
        return 1
    fi

    if [ "${value}" -le 0 ]; then
        return 1
    fi

    case "${unit}" in
        ""|[Mm]) echo "${value}" ;;
        [Kk]) echo $(((value + 1023) / 1024)) ;;
        [Gg]) echo $((value * 1024)) ;;
        [Tt]) echo $((value * 1024 * 1024)) ;;
        *) return 1 ;;
    esac
}

detect_slurm_cpu_limit() {
    local slurm_cpu_limit
    slurm_cpu_limit="$(extract_first_uint "${SLURM_CPUS_ON_NODE:-}" || true)"
    if [ -z "${slurm_cpu_limit}" ]; then
        slurm_cpu_limit="$(extract_first_uint "${SLURM_JOB_CPUS_PER_NODE:-}" || true)"
    fi
    if [ -z "${slurm_cpu_limit}" ]; then
        slurm_cpu_limit="$(extract_first_uint "${SLURM_CPUS_PER_TASK:-}" || true)"
    fi

    if [ -n "${slurm_cpu_limit}" ] && [ "${slurm_cpu_limit}" -gt 0 ]; then
        echo "${slurm_cpu_limit}"
    fi
}

detect_slurm_mem_limit_bytes() {
    local mem_per_node_mb mem_per_cpu_mb slurm_cpu_limit
    mem_per_node_mb="$(parse_memory_to_mb "${SLURM_MEM_PER_NODE:-}" || true)"
    if [ -n "${mem_per_node_mb}" ] && [ "${mem_per_node_mb}" -gt 0 ]; then
        echo $((mem_per_node_mb * 1024 * 1024))
        return 0
    fi

    mem_per_cpu_mb="$(parse_memory_to_mb "${SLURM_MEM_PER_CPU:-}" || true)"
    if [ -z "${mem_per_cpu_mb}" ] || [ "${mem_per_cpu_mb}" -le 0 ]; then
        return 1
    fi

    slurm_cpu_limit="$(detect_slurm_cpu_limit || true)"
    if [ -n "${slurm_cpu_limit}" ] && [ "${slurm_cpu_limit}" -gt 0 ]; then
        echo $((mem_per_cpu_mb * slurm_cpu_limit * 1024 * 1024))
        return 0
    fi

    return 1
}

echo "Detecting container resource limits from cgroups..."
if [ -f "/sys/fs/cgroup/memory.max" ]; then
    CGROUP_MEM_LIMIT="$(cat /sys/fs/cgroup/memory.max)"
    if [ "${CGROUP_MEM_LIMIT}" != "max" ]; then
        export MEM_LIMIT="${CGROUP_MEM_LIMIT}"
        echo "Memory limit detected (cgroup v2): $(numfmt --to=iec "${CGROUP_MEM_LIMIT}")"
    else
        echo "Memory limit: unlimited"
    fi
elif [ -f "/sys/fs/cgroup/memory/memory.limit_in_bytes" ] || [ -f "/sys/fs/cgroup/memory.limit_in_bytes" ]; then
    CGROUP_MEM_LIMIT_FILE="/sys/fs/cgroup/memory/memory.limit_in_bytes"
    if [ ! -f "${CGROUP_MEM_LIMIT_FILE}" ]; then
        CGROUP_MEM_LIMIT_FILE="/sys/fs/cgroup/memory.limit_in_bytes"
    fi
    CGROUP_MEM_LIMIT="$(cat "${CGROUP_MEM_LIMIT_FILE}")"
    if [[ "${CGROUP_MEM_LIMIT}" =~ ^[0-9]+$ ]] && [ "${CGROUP_MEM_LIMIT}" -gt 0 ] && [ "${CGROUP_MEM_LIMIT}" -lt 9000000000000000000 ]; then
        export MEM_LIMIT="${CGROUP_MEM_LIMIT}"
        echo "Memory limit detected (cgroup v1): $(numfmt --to=iec "${CGROUP_MEM_LIMIT}")"
    else
        echo "Memory limit: unlimited"
    fi
else
    echo "No cgroup memory limit file found."
fi

if [ -f "/sys/fs/cgroup/cpu.max" ]; then
    CPU_MAX_LINE="$(cat /sys/fs/cgroup/cpu.max)"
    CPU_QUOTA="$(echo "${CPU_MAX_LINE}" | awk '{print $1}')"
    CPU_PERIOD="$(echo "${CPU_MAX_LINE}" | awk '{print $2}')"
    if [ "${CPU_QUOTA}" != "max" ] && [[ "${CPU_PERIOD}" =~ ^[0-9]+$ ]] && [ "${CPU_PERIOD}" -gt 0 ]; then
        if [[ "${CPU_QUOTA}" =~ ^[0-9]+$ ]]; then
            CPU_LIMIT="$(awk "BEGIN {printf \"%.2f\", ${CPU_QUOTA}/${CPU_PERIOD}}")"
            export CPU_LIMIT="${CPU_LIMIT}"
            echo "CPU limit detected (cgroup v2): ${CPU_LIMIT} CPUs"
        fi
    else
        echo "CPU limit: unlimited"
    fi
else
    CGROUP_CPU_QUOTA_FILE=""
    for quota_file in /sys/fs/cgroup/cpu/cpu.cfs_quota_us /sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us /sys/fs/cgroup/cpu.cfs_quota_us; do
        if [ -f "${quota_file}" ]; then
            CGROUP_CPU_QUOTA_FILE="${quota_file}"
            break
        fi
    done

    if [ -n "${CGROUP_CPU_QUOTA_FILE}" ]; then
        CPU_QUOTA="$(cat "${CGROUP_CPU_QUOTA_FILE}")"
        CPU_PERIOD_FILE="${CGROUP_CPU_QUOTA_FILE%cpu.cfs_quota_us}cpu.cfs_period_us"
        CPU_PERIOD="$(cat "${CPU_PERIOD_FILE}" 2>/dev/null || echo 0)"
        if [ "${CPU_QUOTA}" -gt 0 ] && [ "${CPU_PERIOD}" -gt 0 ]; then
            CPU_LIMIT="$(awk "BEGIN {printf \"%.2f\", ${CPU_QUOTA}/${CPU_PERIOD}}")"
            export CPU_LIMIT="${CPU_LIMIT}"
            echo "CPU limit detected (cgroup v1): ${CPU_LIMIT} CPUs"
        else
            echo "CPU limit: unlimited"
        fi
    else
        echo "No cgroup cpu limit file found."
    fi
fi

# SLURM limit detection (overrides cgroup limits if present)
if [ -n "${SLURM_JOB_ID:-}" ]; then
    echo "Running inside a SLURM job (Job ID: ${SLURM_JOB_ID}). Detecting SLURM limits..."

    SLURM_MEM_LIMIT_BYTES="$(detect_slurm_mem_limit_bytes || true)"
    if [ -n "${SLURM_MEM_LIMIT_BYTES}" ] && [ "${SLURM_MEM_LIMIT_BYTES}" -gt 0 ]; then
        export MEM_LIMIT="${SLURM_MEM_LIMIT_BYTES}"
        echo "Memory limit set from SLURM: $(numfmt --to=iec "${MEM_LIMIT}")"
    fi

    SLURM_CPU_LIMIT="$(detect_slurm_cpu_limit || true)"
    if [ -n "${SLURM_CPU_LIMIT}" ] && [ "${SLURM_CPU_LIMIT}" -gt 0 ]; then
        export CPU_LIMIT="${SLURM_CPU_LIMIT}"
        echo "CPU limit set from SLURM: ${CPU_LIMIT}"
    fi
fi

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

# Start a local single-node Slurm queue inside the container.
# In auto mode, Slurm defaults to non-cgroup compatibility settings unless explicitly enabled.
if [ "${NEURODESKTOP_SLURM_MODE}" = "host" ]; then
    configure_host_slurm_environment
    export NEURODESKTOP_SLURM_ENABLE=0
    echo "[INFO] NEURODESKTOP_SLURM_MODE=host: skipping local Slurm startup."
elif [ "$EUID" -eq 0 ]; then
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
        /opt/neurodesktop/jupyterlab_startup.sh
else
    /opt/neurodesktop/jupyterlab_startup.sh
fi
