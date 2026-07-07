#!/bin/bash
# Generate the per-user SSH keypairs used by the Guacamole SFTP side-channel
# (guacamole_rsa, embedded as private-key in user-mapping.xml) and by the
# in-container sshd loopback login (id_rsa).
#
# Called in the background from jupyterlab_startup.sh so both RSA-4096
# generations (~3s combined) are usually done before the user first opens the
# desktop, and synchronously from guacamole.sh as a fallback. flock serialises
# concurrent callers so the click-time run cannot race the boot-time run and
# read a half-written key.

SSH_DIR="${HOME}/.ssh"
LOCK_FILE="${SSH_DIR}/.neurodesk_keygen.lock"

mkdir -p "${SSH_DIR}"
chmod 700 "${SSH_DIR}" 2>/dev/null || true

generate_keys() {
    if [ ! -f "${SSH_DIR}/guacamole_rsa" ]; then
        ssh-keygen -q -t rsa -f "${SSH_DIR}/guacamole_rsa" -b 4096 -m PEM -N '' -C "guacamole@sftp-server"
    fi
    if [ ! -f "${SSH_DIR}/id_rsa" ]; then
        ssh-keygen -q -t rsa -f "${SSH_DIR}/id_rsa" -b 4096 -m PEM -N ''
    fi
}

if command -v flock >/dev/null 2>&1; then
    (
        flock -w 120 9 || exit 1
        generate_keys
    ) 9>"${LOCK_FILE}"
else
    generate_keys
fi
