#!/bin/bash
# Initialize per-user Guacamole config and credentials.
#
# Must run BEFORE JupyterLab loads its config (so the Basic-auth header reads
# the rotated Guacamole web password) and BEFORE guacamole.sh starts Tomcat
# (so Tomcat reads the user-writable mapping file from GUACAMOLE_HOME).
#
# Idempotent: safe to call from both jupyterlab_startup.sh (boot-time) and
# guacamole.sh (first proxy request). Secrets persist across restarts via
# $HOME/.neurodesk/secrets/.
#
# Invocation modes:
#   source /opt/neurodesktop/init_secrets.sh   # exported vars visible to caller
#   /opt/neurodesktop/init_secrets.sh           # standalone (tests)

# NOTE: we do NOT `set -u` at the top level. When this script is sourced from
# jupyterlab_startup.sh the option leaks into the caller and trips on unrelated
# unbound references further down the startup chain (the `$2` crash in the
# backgrounded ensure_codeserver_extensions subshell was an instance of this).
# The critical section below runs inside its own subshell where `set -u` is
# scoped and cannot escape.

_neurodesk_init_secrets() {
    # Intentionally no `set -u` here. Shell options are global, not function-
    # scoped, so enabling nounset persists into the sourcing shell and has
    # crashed jupyterlab_startup.sh on unrelated unbound references (the
    # `line 217: $2: unbound variable` symptom on HPC). All references in this
    # function use `${VAR:-default}` defensive expansion and every failure path
    # returns non-zero explicitly, so nounset was not load-bearing.
    local HOME_DIR="${HOME:-/home/jovyan}"
    local GUACAMOLE_HOME_LOCAL="${GUACAMOLE_HOME:-${HOME_DIR}/.neurodesk/guacamole}"
    local SECRETS_DIR="${HOME_DIR}/.neurodesk/secrets"
    local MAPPING_FILE="${GUACAMOLE_HOME_LOCAL}/user-mapping.xml"
    local PROPERTIES_FILE="${GUACAMOLE_HOME_LOCAL}/guacamole.properties"
    local WEB_USER_FILE="${SECRETS_DIR}/guacamole_web_user"
    local WEB_PASS_FILE="${SECRETS_DIR}/guacamole_web_password"
    local VNC_PASS_FILE="${SECRETS_DIR}/vnc_password"
    local MAPPING_TEMPLATE="/etc/guacamole/user-mapping-vnc-rdp.xml"

    mkdir -p "${GUACAMOLE_HOME_LOCAL}" "${SECRETS_DIR}" 2>/dev/null || true
    chmod 700 "${SECRETS_DIR}" 2>/dev/null || true

    _neurodesk_random_token() {
        local length="${1:-24}"
        if command -v openssl >/dev/null 2>&1; then
            openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "${length}"
        elif [ -r /dev/urandom ]; then
            LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "${length}"
        else
            date +%s%N | sha256sum | head -c "${length}"
        fi
    }

    _neurodesk_load_or_create_secret() {
        local path="$1"
        local length="${2:-24}"
        local secret
        if [ -s "${path}" ]; then
            secret="$(cat "${path}")"
            # Older container builds may have written a longer-than-requested
            # value (e.g. 24-char VNC token that got truncated inconsistently
            # between vncpasswd and libguac-client-vnc). Regenerate in that
            # case so the file always matches the current length contract.
            if [ "${#secret}" -ne "${length}" ]; then
                secret="$(_neurodesk_random_token "${length}")"
                umask 077
                printf '%s' "${secret}" > "${path}"
                chmod 600 "${path}" 2>/dev/null || true
            fi
        else
            secret="$(_neurodesk_random_token "${length}")"
            umask 077
            printf '%s' "${secret}" > "${path}"
            chmod 600 "${path}" 2>/dev/null || true
        fi
        printf '%s' "${secret}"
    }

    _neurodesk_xml_escape() {
        local raw="$1"
        raw="${raw//&/&amp;}"
        raw="${raw//</&lt;}"
        raw="${raw//>/&gt;}"
        raw="${raw//\"/&quot;}"
        printf '%s' "${raw}"
    }

    _neurodesk_update_authorize() {
        local username="$1" password="$2" tmp_mapping username_escaped password_escaped
        username_escaped="$(_neurodesk_xml_escape "${username}")"
        password_escaped="$(_neurodesk_xml_escape "${password}")"
        tmp_mapping="$(mktemp /tmp/guacamole-authorize.XXXXXX)" || return 1
        sed -E "s|<authorize username=\"[^\"]*\" password=\"[^\"]*\">|<authorize username=\"${username_escaped}\" password=\"${password_escaped}\">|" \
            "${MAPPING_FILE}" > "${tmp_mapping}" || { rm -f "${tmp_mapping}"; return 1; }
        cat "${tmp_mapping}" > "${MAPPING_FILE}"
        rm -f "${tmp_mapping}"
        grep -q "<authorize username=\"${username_escaped}\" password=\"${password_escaped}\">" "${MAPPING_FILE}"
    }

    # Re-seed user-mapping.xml from the build-time template on every run. Any
    # stale port / password / SSH key stamped by a prior guacamole.sh invocation
    # is discarded here; guacamole.sh re-stamps the live values further down.
    # This is critical because an earlier broken test run (which stamped its
    # test VNC port and then tore that vncserver down) could otherwise leave
    # the live mapping pointing at a dead backend - the browser would then see
    # "500 Internal Server Error" from Guacamole until the mapping was manually
    # removed.
    if [ ! -f "${MAPPING_TEMPLATE}" ]; then
        echo "[ERROR] Guacamole template ${MAPPING_TEMPLATE} not found." >&2
        return 1
    fi
    if ! cp "${MAPPING_TEMPLATE}" "${MAPPING_FILE}"; then
        echo "[ERROR] Failed to copy ${MAPPING_TEMPLATE} to ${MAPPING_FILE}" >&2
        return 1
    fi
    chmod 600 "${MAPPING_FILE}" 2>/dev/null || true

    # Seed properties pointing at the user-writable mapping file.
    if [ ! -s "${PROPERTIES_FILE}" ]; then
        {
            echo "user-mapping: ${MAPPING_FILE}"
            echo "guacd-hostname: 127.0.0.1"
            echo "guacd-port: 4822"
        } > "${PROPERTIES_FILE}" || {
            echo "[ERROR] Failed to write ${PROPERTIES_FILE}" >&2
            return 1
        }
        chmod 600 "${PROPERTIES_FILE}" 2>/dev/null || true
    fi

    # Generate / load persistent secrets.
    local web_user="${NB_USER:-jovyan}"
    if [ ! -s "${WEB_USER_FILE}" ]; then
        umask 077
        printf '%s' "${web_user}" > "${WEB_USER_FILE}"
        chmod 600 "${WEB_USER_FILE}" 2>/dev/null || true
    else
        web_user="$(cat "${WEB_USER_FILE}")"
    fi

    local web_password vnc_password
    web_password="$(_neurodesk_load_or_create_secret "${WEB_PASS_FILE}" 24)"
    # VNC's VncAuth is DES-based with a fixed 8-byte key: vncpasswd and every
    # libvnc client truncate past 8 anyway, so generating longer strings just
    # introduces a risk of mismatched truncation between the Xvnc stored value
    # and what Guacamole sends. Keep it at exactly 8 random alphanumerics.
    vnc_password="$(_neurodesk_load_or_create_secret "${VNC_PASS_FILE}" 8)"

    if ! _neurodesk_update_authorize "${web_user}" "${web_password}"; then
        echo "[ERROR] Failed to update Guacamole <authorize> in ${MAPPING_FILE}" >&2
        return 1
    fi

    # Stamp ${HOME}/.vnc/passwd from the rotated token immediately, not lazily
    # at first Neurodesktop click. restore_home_defaults.sh migrates the build-
    # time .vnc/passwd (hash of literal "password") into $HOME on every start
    # whenever the image default is newer, which creates a cross-user auth
    # window on shared HPC nodes between container boot and the user opening
    # the desktop. Keeping .vnc/passwd in sync with the rotated secret from
    # the first second of the session closes that window.
    local vnc_passwd_file="${HOME_DIR}/.vnc/passwd"
    mkdir -p "${HOME_DIR}/.vnc" 2>/dev/null || true
    if command -v vncpasswd >/dev/null 2>&1; then
        # `vncpasswd -f` reads plaintext on stdin and writes the DES-obfuscated
        # form Xvnc expects. We do NOT have an interactive fallback here on
        # purpose: plain `vncpasswd` (without -f) reads via getpass() from
        # /dev/tty and cannot be fed via a pipe, so piping would silently
        # write a garbage hash and break VNC auth with CLIENT_UNAUTHORIZED.
        umask 077
        local tmp_passwd="${vnc_passwd_file}.tmp.$$"
        if printf '%s\n' "${vnc_password}" | vncpasswd -f > "${tmp_passwd}" 2>/dev/null \
            && [ -s "${tmp_passwd}" ]; then
            mv -f "${tmp_passwd}" "${vnc_passwd_file}"
            chmod 600 "${vnc_passwd_file}" 2>/dev/null || true
        else
            rm -f "${tmp_passwd}" 2>/dev/null || true
            echo "[WARN] vncpasswd -f failed; guacamole.sh will retry at click time." >&2
        fi
    fi

    export GUACAMOLE_HOME="${GUACAMOLE_HOME_LOCAL}"
    export NEURODESKTOP_GUACAMOLE_USER="${web_user}"
    export NEURODESKTOP_GUACAMOLE_PASSWORD="${web_password}"
    export NEURODESKTOP_VNC_PASSWORD="${vnc_password}"
    return 0
}

_neurodesk_init_secrets
_neurodesk_init_secrets_status=$?

# Only `exit` when run as a standalone script; when sourced, let the caller
# decide what to do with the non-zero return.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    exit "${_neurodesk_init_secrets_status}"
fi
return "${_neurodesk_init_secrets_status}" 2>/dev/null || true
