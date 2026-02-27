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

# Function to check and apply chown if necessary
apply_chown_if_needed() {
    local dir="$1"
    local recursive="$2"
    local current_uid current_gid
    local -a chown_runner

    # If running in Apptainer/Singularity, we likely don't want to mess with chown
    if is_apptainer_runtime; then
        return
    fi

    if [ -d "${dir}" ]; then
        current_uid=$(stat -c "%u" "${dir}")
        current_gid=$(stat -c "%g" "${dir}")
        if [ "${current_uid}" != "${NB_UID}" ] || [ "${current_gid}" != "${NB_GID}" ]; then
            chown_runner=(chown)
            if [ "$EUID" -ne 0 ]; then
                if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
                    chown_runner=(sudo -n chown)
                else
                    echo "[WARN] Unable to fix ownership of ${dir}: requires root or passwordless sudo."
                    return
                fi
            fi

            if ! can_chown_path_with_runner "${dir}" "${chown_runner[@]}"; then
                echo "[WARN] Skipping ownership fix for ${dir}: chown unsupported in this runtime/filesystem."
                return
            fi

            if [ "${recursive}" = true ]; then
                "${chown_runner[@]}" -R "${NB_UID}:${NB_GID}" "${dir}" || \
                    echo "[WARN] Failed to fix ownership of ${dir} recursively."
            else
                "${chown_runner[@]}" "${NB_UID}:${NB_GID}" "${dir}" || \
                    echo "[WARN] Failed to fix ownership of ${dir}."
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

ensure_jupyterlab_disabled_extensions() {
    local labconfig_dir="${HOME}/.jupyter/labconfig"
    local page_config="${labconfig_dir}/page_config.json"

    mkdir -p "${labconfig_dir}" 2>/dev/null || true

    if ! python3 - "${page_config}" <<'PY' >/dev/null 2>&1
import json
import sys
from pathlib import Path

page_config_path = Path(sys.argv[1])
payload = {}

if page_config_path.exists():
    try:
        loaded = json.loads(page_config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = loaded
    except Exception:
        payload = {}

disabled_extensions = payload.get("disabledExtensions")
if not isinstance(disabled_extensions, dict):
    disabled_extensions = {}

disabled_extensions["@jupyterhub/jupyter-server-proxy"] = True
disabled_extensions["@jupyterlab/apputils-extension:announcements"] = True
payload["disabledExtensions"] = disabled_extensions

page_config_path.write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8"
)
PY
    then
        echo "[WARN] Failed to ensure JupyterLab disabledExtensions in ${page_config}."
    fi
}

ensure_jupyterlab_disabled_extensions

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

AUTHORIZED_KEYS_FILE="${HOME}/.ssh/authorized_keys"
touch "$AUTHORIZED_KEYS_FILE"
chmod 600 "$AUTHORIZED_KEYS_FILE"

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

# Start SSH/SFTP endpoint for Guacamole and sync authorized_keys from sftp-private-key.
if [ -x /opt/neurodesktop/ensure_sftp_sshd.sh ]; then
    /opt/neurodesktop/ensure_sftp_sshd.sh || \
        echo "[WARN] Failed to initialize SSH/SFTP service for Guacamole."
else
    echo "[WARN] /opt/neurodesktop/ensure_sftp_sshd.sh not found."
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
  ln -s "${NEURODESKTOP_LOCAL_CONTAINERS:-/neurodesktop-storage/containers}" "/neurocommand/local/containers"
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

# Best-effort: install a small set of useful code-server extensions.
ensure_codeserver_extension() {
    local ext_name="$1"
    local ext_pattern="$2"
    shift 2

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

# SSH/SFTP startup is handled above by /opt/neurodesktop/ensure_sftp_sshd.sh.

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
    printf '%s\n' '#!/bin/sh' '/usr/bin/startlxde' 'vncconfig -nowin -noiconic &' > "${HOME}/.vnc/xstartup"
fi

# Ensure correct permissions
chmod 600 "${HOME}/.vnc/passwd" 2>/dev/null || true
chmod +x "${HOME}/.vnc/xstartup"

# echo "[INFO] VNC setup complete. Contents of ${HOME}/.vnc:"
# ls -la "${HOME}/.vnc/"

touch "$STARTUP_DONE_FILE"
