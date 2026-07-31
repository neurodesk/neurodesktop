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

# Generate one keypair and publish it atomically.
#
# flock is only an optimisation here: it stops two callers repeating the same
# ~3s RSA-4096 work. It cannot be the correctness mechanism, because it is
# absent on some platforms and this script must still be safe on the fallback
# path below. ssh-keygen refuses to write over an existing file, so two
# unserialised callers would otherwise leave the loser exiting non-zero --
# which guacamole.sh reports as a failed pre-generation.
generate_pair() {
    local target="$1"
    shift

    [ -f "${target}" ] && return 0

    local staging
    staging="$(mktemp -d "${SSH_DIR}/.keygen.XXXXXX")" || return 1

    if ! ssh-keygen -q -t rsa -f "${staging}/key" -b 4096 -m PEM -N '' "$@"; then
        rm -rf "${staging}"
        return 1
    fi

    # ln fails when the target already exists, so claiming the private half is
    # an atomic gate: exactly one generation wins and goes on to publish its
    # own matching public half. A loser discards its pair rather than
    # overwriting a key another process may already have handed out, which is
    # what would otherwise strand a private key next to a foreign .pub.
    if ln "${staging}/key" "${target}" 2>/dev/null; then
        mv -f "${staging}/key.pub" "${target}.pub"
    fi

    rm -rf "${staging}"
    return 0
}

generate_keys() {
    generate_pair "${SSH_DIR}/guacamole_rsa" -C "guacamole@sftp-server" || return 1
    generate_pair "${SSH_DIR}/id_rsa" || return 1
}

if command -v flock >/dev/null 2>&1; then
    (
        flock -w 120 9 || exit 1
        generate_keys
    ) 9>"${LOCK_FILE}"
else
    generate_keys
fi
