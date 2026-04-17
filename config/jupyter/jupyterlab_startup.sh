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
# Each file is copied if missing, or migrated when image defaults are newer.
source /opt/neurodesktop/restore_home_defaults.sh

# Initialize per-user Guacamole config and random credentials BEFORE Jupyter
# reads jupyter_notebook_config.py - the config's Basic-auth header for the
# /neurodesktop proxy is derived from ${HOME}/.neurodesk/secrets files written
# by this script. Skipping this step would cause Jupyter to send stale
# jovyan/password credentials and result in a 401 from the rotated Guacamole.
if [ -x /opt/neurodesktop/init_secrets.sh ]; then
    # shellcheck disable=SC1091
    source /opt/neurodesktop/init_secrets.sh || \
        echo "[WARN] init_secrets.sh failed; Guacamole web auth may fall back to the static default."
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

# Home ownership is handled by before_notebook.sh (fix_home_ownership_if_needed).

sanitize_jupyterlab_workspaces() {
    local workspace_dir="${HOME}/.jupyter/lab/workspaces"
    local workspace_file
    local backup_file

    if [ ! -d "${workspace_dir}" ]; then
        return
    fi

    while IFS= read -r -d '' workspace_file; do
        if python3 - "${workspace_file}" <<'PY' >/dev/null 2>&1
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as workspace_fp:
    json.load(workspace_fp)
PY
        then
            continue
        fi

        backup_file="${workspace_file}.invalid-$(date +%Y%m%d%H%M%S)-$$"
        if mv "${workspace_file}" "${backup_file}" 2>/dev/null; then
            echo "[WARN] Invalid JupyterLab workspace JSON detected. Moved ${workspace_file} to ${backup_file}."
        else
            rm -f "${workspace_file}" 2>/dev/null || true
            echo "[WARN] Invalid JupyterLab workspace JSON detected. Removed ${workspace_file}."
        fi
    done < <(find "${workspace_dir}" -maxdepth 1 -type f -name '*.jupyterlab-workspace' -print0 2>/dev/null)
}

sanitize_jupyterlab_workspaces

ensure_jupyterlab_page_config() {
    local labconfig_dir="${HOME}/.jupyter/labconfig"
    local page_config="${labconfig_dir}/page_config.json"
    local supporter_flag="${HOME}/.config/neurodesk_supporter"
    local updater_script="/opt/neurodesktop/update_page_config.py"

    mkdir -p "${labconfig_dir}" 2>/dev/null || true

    if [ ! -f "${updater_script}" ]; then
        echo "[WARN] ${updater_script} not found."
        return
    fi

    if ! python3 "${updater_script}" "${page_config}" "${supporter_flag}" >/dev/null 2>&1
    then
        echo "[WARN] Failed to ensure JupyterLab page config in ${page_config}."
    fi
}

ensure_jupyterlab_page_config

# SSH key generation, guacamole mapping injection, and SSH/SFTP daemon startup
# are handled on-demand by guacamole.sh when the desktop is opened.
mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"

# Fix jupyter-sshd-proxy host key permissions (generated on first use without explicit chmod)
if [ -f "${HOME}/.ssh/jupyter_sshd_hostkey" ]; then
    chmod 600 "${HOME}/.ssh/jupyter_sshd_hostkey"
fi
if [ -f "${HOME}/.ssh/jupyter_sshd_hostkey.pub" ]; then
    chmod 644 "${HOME}/.ssh/jupyter_sshd_hostkey.pub"
fi
# Default ACLs ensure future keys created in .ssh get owner-only permissions
setfacl -dRm u::rw,g::0,o::0 "${HOME}/.ssh" 2>/dev/null || true

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
  ln -s "${NEURODESKTOP_LOCAL_CONTAINERS:-/neurodesktop-storage/containers}" "/neurocommand/local/containers"
fi

# Create a cpuinfo file with a valid CPU MHz entry for ARM CPUs.
echo "[INFO] Checking for ARM CPU and adding a CPU Mhz entry in /proc/cpuinfo to work around a bug in Matlab that expects this value to be present."
if ! grep -iq 'cpu.*hz' /proc/cpuinfo; then
    mkdir -p "${HOME}/.local"
    cpuinfo_file="${HOME}/.local/cpuinfo_with_ARM_MHz_fix"
    cp /proc/cpuinfo "${cpuinfo_file}"
    chmod u+rw "${cpuinfo_file}"
    sed -i '/^$/c\cpu MHz         : 2245.778\n' "${cpuinfo_file}"
    # add vendor and model name as well:
    sed -i '/^$/c\vendor_id       : ARM\nmodel name      : Apple-M\n' "${cpuinfo_file}"
    if sudo -n true 2>/dev/null; then
        if sudo mount --bind "${cpuinfo_file}" /proc/cpuinfo >/dev/null 2>&1; then
            echo "[INFO] Added CPU Mhz entry in /proc/cpuinfo to work around a bug in Matlab that expects this value to be present."
        else
            echo "[WARN] Unable to bind-mount ${cpuinfo_file} over /proc/cpuinfo in this runtime. Continuing without the Matlab CPU Mhz workaround."
        fi
    else
        echo "[WARN] Passwordless sudo is unavailable; skipping the Matlab CPU Mhz workaround."
    fi
fi

# ensure overlay directory exists
mkdir -p /tmp/apptainer_overlay

# ensure goose config directory exists
mkdir -p ${HOME}/.config/goose

# ensure opencode config directory exists
mkdir -p ${HOME}/.config/opencode

# Best-effort: install a small set of useful code-server extensions.
ensure_codeserver_extension() {
    local ext_name="$1"
    shift
    local ext_pattern="${1:-${ext_name}-*}"
    if [ "$#" -gt 0 ]; then
        shift
    fi
    if [ "$#" -eq 0 ]; then
        set -- "${ext_name}"
    fi

    local ext_dir="${HOME}/.local/share/code-server/extensions"
    local log_file="/tmp/code-server-${ext_name,,}-extension-install.log"

    mkdir -p "${ext_dir}"

    if find "${ext_dir}" -maxdepth 1 -type d -iname "${ext_pattern}" -print -quit | grep -q .; then
        return
    fi

    local ext_id
    for ext_id in "$@"; do
        echo "[INFO] Installing code-server ${ext_name} extension (${ext_id})..."
        if code-server --install-extension "${ext_id}" >"${log_file}" 2>&1; then
            return
        fi
    done

    echo "[WARN] Could not install ${ext_name} extension. Check ${log_file} for details."
}

ensure_codeserver_extensions() {
    if ! command -v code-server >/dev/null 2>&1; then
        return
    fi

    # Anthropic Claude assistant
    ensure_codeserver_extension "anthropic.claude-code"

    # OpenAI Codex / ChatGPT assistant
    ensure_codeserver_extension "openai.chatgpt"

    # GitHub auth and PR integration
    ensure_codeserver_extension "github.vscode-pull-request-github"

    # NIfTI/medical image viewer
    ensure_codeserver_extension "korbinianeckstein.niivue"
    
    # slurm extension
    ensure_codeserver_extension "xy-sss.slurm--extension"
}

ensure_codeserver_extensions &

# Conda shell hooks are already provided by the base image/defaults.
# Avoid mutating shell config on each startup.

# Setup VNC directory and ensure files exist (should be restored from defaults)
echo "[INFO] Setting up VNC..."
mkdir -p "${HOME}/.vnc"
if sudo -n true 2>/dev/null; then
    if ! is_apptainer_runtime; then
        sudo -n chown "${NB_USER}" "${HOME}/.vnc" 2>/dev/null || true
    fi
fi

# Generate VNC password if not existing (fallback if restore failed)
if [ ! -f "${HOME}/.vnc/passwd" ]; then
    echo "[INFO] Generating VNC password (not found in restored defaults)..."
    /usr/bin/printf '%s\n%s\n%s\n' 'password' 'password' 'n' | vncpasswd
fi

# Create xstartup if not existing (fallback if restore failed)
if [ ! -f "${HOME}/.vnc/xstartup" ]; then
    echo "[INFO] Creating VNC xstartup (not found in restored defaults)..."
    printf '%s\n' '#!/bin/sh' 'eval "$(dbus-launch --sh-syntax)"' 'export DBUS_SESSION_BUS_ADDRESS' '/usr/bin/startlxde' 'vncconfig -nowin -noiconic &' > "${HOME}/.vnc/xstartup"
fi

# Ensure correct permissions
chmod 600 "${HOME}/.vnc/passwd" 2>/dev/null || true
chmod +x "${HOME}/.vnc/xstartup"

# echo "[INFO] VNC setup complete. Contents of ${HOME}/.vnc:"
# ls -la "${HOME}/.vnc/"

touch "$STARTUP_DONE_FILE"
