#!/bin/bash

# Minimal startup for neurodesktop-base (headless mode)
# order: start_notebook.sh -> ### before_notebook_base.sh ###

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
    # Set shell to bash
    if [ "$(getent passwd "${NB_USER}" | cut -d: -f7)" != "/bin/bash" ]; then
        usermod --shell /bin/bash "${NB_USER}"
    fi

    # Make sure binfmt_misc is mounted for Apptainer on Apple Silicon
    if [ -d "/proc/sys/fs/binfmt_misc" ]; then
        if ! mountpoint -q /proc/sys/fs/binfmt_misc; then
            echo "binfmt_misc directory exists but is not mounted. Mounting now..."
            sudo mount -t binfmt_misc binfmt /proc/sys/fs/binfmt_misc 2>/dev/null || true
        fi
    fi

    # Mount CVMFS if needed
    if [ ! -d "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/" ]; then
        # Check internet connectivity
        if nslookup neurodesk.org >/dev/null 2>&1; then
            echo "Internet is up"
        else
            export CVMFS_DISABLE=true
            echo "No internet connection. Disabling CVMFS."
        fi

        if [ -z "$CVMFS_DISABLE" ]; then
            export CVMFS_DISABLE="false"
        fi

        if [[ "$CVMFS_DISABLE" == "false" ]]; then
            CACHE_DIR="${HOME}/cvmfs_cache"

            if [ ! -d "$CACHE_DIR" ]; then
                echo "Creating CVMFS cache directory at $CACHE_DIR"
                mkdir -p "$CACHE_DIR"
            fi

            chmod 755 ${HOME}
            if sudo -n true 2>/dev/null; then
                chown -R cvmfs:root "$CACHE_DIR"
            fi

            # Try autofs first
            ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ 2>/dev/null && echo "CVMFS is ready" || echo "CVMFS directory not there. Trying internal fuse mount."

            if [ ! -d "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/" ]; then
                # Function to get latency
                get_latency() {
                    local url="$1"
                    local server_name="$2"
                    echo "Testing $url" >&2
                    local output
                    local exit_code
                    output=$(curl --connect-timeout 5 -s -w "%{time_total} %{http_code}" -o /dev/null "$url")
                    exit_code=$?
                    if [ $exit_code -eq 0 ]; then
                        local time status
                        time=$(echo "$output" | awk '{print $1}')
                        status=$(echo "$output" | awk '{print $2}')
                        if [ "$status" -eq 200 ]; then
                            echo "$time"
                        else
                            echo "999"
                        fi
                    else
                        echo "999"
                    fi
                }

                echo "Probing regional servers..."
                EUROPE_HOST=cvmfs-frankfurt.neurodesk.org
                AMERICA_HOST=cvmfs-jetstream.neurodesk.org
                ASIA_HOST=cvmfs-brisbane.neurodesk.org

                EUROPE_url="http://${EUROPE_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
                AMERICA_url="http://${AMERICA_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
                ASIA_url="http://${ASIA_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"

                EUROPE_latency=$(get_latency "$EUROPE_url" "$EUROPE_HOST")
                AMERICA_latency=$(get_latency "$AMERICA_url" "$AMERICA_HOST")
                ASIA_latency=$(get_latency "$ASIA_url" "$ASIA_HOST")

                FASTEST_REGION=$(printf "%s europe\n%s america\n%s asia\n" "$EUROPE_latency" "$AMERICA_latency" "$ASIA_latency" | sort -n | head -n 1 | awk '{print $2}')

                DIRECT_HOST=cvmfs-geoproximity.neurodesk.org
                CDN_HOST=cvmfs.neurodesk.org
                DIRECT_latency=$(get_latency "http://${DIRECT_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished" "$DIRECT_HOST")
                CDN_latency=$(get_latency "http://${CDN_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished" "$CDN_HOST")
                FASTEST_MODE=$(printf "%s direct\n%s cdn\n" "$DIRECT_latency" "$CDN_latency" | sort -n | head -n 1 | awk '{print $2}')

                echo "Fastest: region=$FASTEST_REGION, mode=$FASTEST_MODE"

                config_file_suffix="${FASTEST_MODE}.${FASTEST_REGION}"
                source_config="/etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.${config_file_suffix}"
                target_config="/etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf"

                if [ -f "$source_config" ]; then
                    cp "$source_config" "$target_config"
                fi

                echo "Mounting CVMFS..."
                mkdir -p /cvmfs/neurodesk.ardc.edu.au
                mount -t cvmfs neurodesk.ardc.edu.au /cvmfs/neurodesk.ardc.edu.au

                ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ 2>/dev/null && echo "CVMFS is ready" || echo "CVMFS mount failed"
            fi
        fi
    fi
fi

# Source lmod in bashrc
BASHRC_FILE="/home/${NB_USER}/.bashrc"
INIT_MODULES="if [ -f '/usr/share/module.sh' ]; then source /usr/share/module.sh; fi"

touch "$BASHRC_FILE"
if ! grep -qF "$INIT_MODULES" "$BASHRC_FILE"; then
    echo "$INIT_MODULES" >> "$BASHRC_FILE"
fi

# Setup neurodesktop-storage directory
NEURODESKTOP_HOME_STORAGE="${HOME}/neurodesktop-storage"
NEURODESKTOP_ROOT_STORAGE="/neurodesktop-storage"

if mountpoint -q "${NEURODESKTOP_ROOT_STORAGE}" 2>/dev/null; then
    if [ ! -e "${NEURODESKTOP_HOME_STORAGE}" ]; then
        ln -s "${NEURODESKTOP_ROOT_STORAGE}/" "${NEURODESKTOP_HOME_STORAGE}"
    fi
else
    if [ ! -d "${NEURODESKTOP_HOME_STORAGE}" ]; then
        mkdir -p "${NEURODESKTOP_HOME_STORAGE}/containers"
    fi
fi

# Create neurocommand containers symlink
if [ ! -L "/neurocommand/local/containers" ]; then
    ln -s "${HOME}/neurodesktop-storage/containers" "/neurocommand/local/containers" 2>/dev/null || true
fi

# Ensure overlay directory exists for Apptainer
mkdir -p /tmp/apptainer_overlay

source /opt/neurodesktop/environment_variables.sh > /dev/null 2>&1
