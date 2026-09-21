#!/bin/bash
# Called by root startup; no notebook-user sudo access is needed.
set -euo pipefail

cpuinfo=/proc/cpuinfo
runtime=/run/neurodesktop/cpuinfo

if grep -iq 'cpu.*hz' "$cpuinfo"; then
    exit 0
fi

prepare_cpuinfo() {
    install -d -m 0700 "$runtime" || return 1
    local prepared
    prepared=$(mktemp "$runtime/cpuinfo.XXXXXX") || return 1
    if ! awk '
        /^$/ { print "cpu MHz         : 2245.778"; print "vendor_id       : ARM"; print "model name      : Apple-M" }
        { print }
    ' "$cpuinfo" > "$prepared"; then
        rm -f "$prepared"
        return 1
    fi
    if ! chmod 0644 "$prepared"; then
        rm -f "$prepared"
        return 1
    fi
    if /usr/bin/mount --bind "$prepared" "$cpuinfo"; then
        rm -f "$prepared"
        echo "[INFO] Added CPU MHz information for Matlab."
    else
        rm -f "$prepared"
        return 1
    fi
}

if ! prepare_cpuinfo; then
    echo "[WARN] Unable to prepare CPU MHz information in this runtime; continuing without the Matlab workaround." >&2
fi
