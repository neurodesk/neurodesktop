#!/bin/bash
# Pre-build the notebook user's Lmod spider cache once CVMFS is mounted.
# A cold `ml av` reads about 2,000 modulefiles with one sequential CVMFS request
# each, which takes about a minute from a distant mirror. Afterwards `ml av`
# reads one cache file. Launched in the background; never fails startup.

MODULES_DIR="${NEURODESKTOP_WARM_MODULES_DIR:-/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/}"
LMOD_INIT="${NEURODESKTOP_WARM_LMOD_INIT:-/usr/share/lmod/lmod/init/bash}"
ENV_SCRIPT="${NEURODESKTOP_WARM_ENV_SCRIPT:-/opt/neurodesktop/environment_variables.sh}"
TIMEOUT_SECONDS="${NEURODESKTOP_WARM_TIMEOUT_SECONDS:-900}"

log() { echo "[module-cache] $*"; }

build_cache() {
    local cache_dir
    unset NEURODESKTOP_ENV_SOURCED
    # shellcheck source=/dev/null
    source "$ENV_SCRIPT" >/dev/null 2>&1
    # shellcheck source=/dev/null
    source "$LMOD_INIT"
    cache_dir=$(module --config-json 2>&1 | python3 -c \
        'import json, sys; print(json.load(sys.stdin)["configT"]["usrCacheDir"])') || return 1
    umask 077
    mkdir -p "$cache_dir" || return 1
    # Lmod reuses a user cache for a day. Invalidate it so every start lists
    # tools published since; Lmod removes the marker after it rebuilds.
    : > "${cache_dir}/invalidated" || return 1
    # A short build would otherwise not be written to the user cache.
    LMOD_SHORT_TIME=0 module --quiet avail >/dev/null 2>&1
}

if [ "${1:-}" = "--build" ]; then
    build_cache
    exit $?
fi

if [ "${NEURODESKTOP_LMOD_CACHE_WARMUP:-1}" = "0" ]; then
    log "Disabled by NEURODESKTOP_LMOD_CACHE_WARMUP=0."
    exit 0
fi
[ -d "$MODULES_DIR" ] || { log "CVMFS modules are unavailable; skipping."; exit 0; }
[ -r "$LMOD_INIT" ] || { log "Lmod is unavailable; skipping."; exit 0; }

run=(timeout "$TIMEOUT_SECONDS" nice -n 10 "${BASH_SOURCE[0]}" --build)
if [ "$(id -u)" -eq 0 ]; then
    # Build as the notebook user so the cache belongs to them.
    user="${NB_USER:-jovyan}"
    home=$(getent passwd "$user" | cut -d: -f6)
    if [ -z "$home" ] || [ ! -d "$home" ]; then
        log "Notebook user ${user} has no home directory; skipping."
        exit 0
    fi
    # Start from a clean environment rather than root's startup variables.
    run=(runuser -u "$user" -- env -i PATH="$PATH" LANG="${LANG:-C.UTF-8}"
         HOME="$home" USER="$user" NB_USER="$user"
         NEURODESKTOP_WARM_LMOD_INIT="$LMOD_INIT" NEURODESKTOP_WARM_ENV_SCRIPT="$ENV_SCRIPT"
         "${run[@]}")
fi

start=$(date +%s)
if "${run[@]}"; then
    log "Lmod module cache built in $(( $(date +%s) - start ))s."
else
    log "[WARN] Lmod module cache build did not finish; ml av builds it on demand."
fi
exit 0
