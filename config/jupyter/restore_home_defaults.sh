#!/bin/bash
# restore_home_defaults.sh
# Restores default home directory files if they don't exist
# and migrates them when image defaults are newer.
# Called from jupyterlab_startup.sh

DEFAULTS_DIR="/opt/jovyan_defaults"
HOME_DIR="${HOME:-/home/jovyan}"

log_info() {
    echo "[restore_home_defaults] $1"
}

log_warn() {
    echo "[restore_home_defaults] WARN: $1" >&2
}

# Copy file with sudo fallback for permission-constrained homes.
copy_file_with_fallback() {
    local src="$1"
    local dest="$2"

    if cp -p "$src" "$dest" 2>/dev/null; then
        return 0
    fi

    # Fallback path for permission-constrained homes.
    if sudo -n true 2>/dev/null; then
        if sudo cp -p "$src" "$dest" 2>/dev/null; then
            if [ -n "$NB_UID" ] && [ -n "$NB_GID" ]; then
                sudo chown "$NB_UID:$NB_GID" "$dest" 2>/dev/null || true
            fi
            return 0
        fi
    fi

    return 1
}

# Ensure a destination file exists and is updated when defaults are newer.
sync_file_from_defaults() {
    local src="$1"
    local dest="$2"
    local dest_dir
    local action

    dest_dir=$(dirname "$dest")

    # Create parent directory if needed
    if [ ! -d "$dest_dir" ]; then
        mkdir -p "$dest_dir" 2>/dev/null || true
    fi

    # If the directory still doesn't exist, try with sudo (home mounts can have strict ownership)
    if [ ! -d "$dest_dir" ] && sudo -n true 2>/dev/null; then
        sudo mkdir -p "$dest_dir" 2>/dev/null || true
        if [ -n "$NB_UID" ] && [ -n "$NB_GID" ]; then
            sudo chown "$NB_UID:$NB_GID" "$dest_dir" 2>/dev/null || true
        fi
    fi

    action=""
    if [ ! -e "$dest" ]; then
        action="Restoring missing file"
    elif [ "$src" -nt "$dest" ]; then
        action="Migrating updated default"
    fi

    if [ -z "$action" ]; then
        return 0
    fi

    log_info "${action}: $dest"

    if copy_file_with_fallback "$src" "$dest"; then
        return 0
    fi

    log_warn "Failed to update $dest from $src"
    return 1
}

# Handle .bashrc append (special case - append content with marker detection)
handle_bashrc_append() {
    local append_file="${DEFAULTS_DIR}/.bashrc_append"
    local bashrc="${HOME_DIR}/.bashrc"
    local marker="# Neurodesk bashrc additions"

    if [ ! -f "$append_file" ]; then
        return 0
    fi

    # Check if marker already exists in .bashrc
    if [ -f "$bashrc" ] && grep -q "$marker" "$bashrc"; then
        return 0
    fi

    log_info "Appending Neurodesk additions to .bashrc"

    # Create .bashrc if it doesn't exist
    if [ ! -f "$bashrc" ]; then
        touch "$bashrc"
    fi

    # Append with marker
    echo "" >> "$bashrc"
    echo "$marker" >> "$bashrc"
    cat "$append_file" >> "$bashrc"
}

# Create empty directories if they don't exist
create_directories() {
    local dirs=(
        "${HOME_DIR}/.config/matplotlib-mpldir"
        "${HOME_DIR}/Desktop"
    )

    for dir in "${dirs[@]}"; do
        if [ ! -d "$dir" ]; then
            log_info "Creating directory: $dir"
            mkdir -p "$dir"
        fi
    done

    # Set specific permissions for matplotlib dir
    chmod -R 700 "${HOME_DIR}/.config/matplotlib-mpldir" 2>/dev/null || true
}

# Setup SSH directory with proper permissions and ACLs
setup_ssh_directory() {
    local ssh_dir="${HOME_DIR}/.ssh"
    if [ -d "$ssh_dir" ]; then
        chmod 700 "$ssh_dir"
        # Set default ACLs for new files in .ssh directory
        setfacl -dRm u::rwx,g::0,o::0 "$ssh_dir" 2>/dev/null || true
    fi
}

# Setup git config if not already configured
setup_git_config() {
    if ! git config --global user.email > /dev/null 2>&1; then
        log_info "Setting default git config"
        git config --global user.email "user@neurodesk.org"
        git config --global user.name "Neurodesk User"
    fi

    # Enable GitHub authentication flow via GitHub CLI for git operations.
    # This gives code-server a working auth path for clone/push against github.com.
    if command -v gh >/dev/null 2>&1; then
        current_helper="$(git config --global --get credential.\"https://github.com\".helper 2>/dev/null || true)"
        if [ "$current_helper" != "!gh auth git-credential" ]; then
            log_info "Configuring git credential helper for github.com via gh"
            git config --global credential."https://github.com".helper '!gh auth git-credential'
        fi
    fi
}

# Main restoration logic
restore_defaults() {
    # Check if defaults directory exists
    if [ ! -d "$DEFAULTS_DIR" ]; then
        log_info "Defaults directory not found: $DEFAULTS_DIR"
        return 1
    fi

    # log_info "Starting restoration from $DEFAULTS_DIR"
    # log_info "Contents of defaults directory:"
    # find "$DEFAULTS_DIR" -type f 2>&1 | head -20

    # Ensure home directory exists
    mkdir -p "$HOME_DIR"

    # Iterate through all files in defaults directory
    while IFS= read -r -d '' src_file; do
        # Skip the special .bashrc_append file
        if [[ "$src_file" == *".bashrc_append" ]]; then
            continue
        fi

        # Calculate relative path and destination
        rel_path="${src_file#${DEFAULTS_DIR}/}"
        dest_file="${HOME_DIR}/${rel_path}"

        log_info "Processing: $rel_path"
        sync_file_from_defaults "$src_file" "$dest_file"
    done < <(find "$DEFAULTS_DIR" -type f -print0)

    # Handle special cases
    handle_bashrc_append
    create_directories
    setup_ssh_directory
    setup_git_config

    log_info "Home directory defaults restoration complete"
}

# Run the restoration
restore_defaults
