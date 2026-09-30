#!/bin/bash
# Benchmark distinct CVMFS destinations, then validate real data transfers.
# The Python helper uses only the standard library and the image's curl.
# --force-probe bypasses the performance-validated, one-day selection cache.

TARGET_CONFIG="${NEURODESKTOP_CVMFS_TARGET_CONFIG:-/etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf}"
CACHE_FILE="${NEURODESKTOP_CVMFS_CACHE_FILE:-${HOME}/.cache/neurodesktop/cvmfs-selection.env}"
TTL_SECONDS="${NEURODESKTOP_CVMFS_SELECTION_TTL_SECONDS:-86400}"
DEFAULT_HOST_POOL="
http://cvmfs-geoproximity.neurodesk.org
http://cvmfs.neurodesk.org
http://cvmfs-brisbane.neurodesk.org
http://cvmfs-melbourne.neurodesk.org
http://cvmfs-sydney.neurodesk.org
http://cvmfs-perth.neurodesk.org
http://cvmfs-jetstream.neurodesk.org
http://cvmfs-frankfurt.neurodesk.org
http://cvmfs01.nikhef.nl:8000
http://cvmfs-s1bnl.opensciencegrid.org:8000
http://cvmfs-s1goc.opensciencegrid.org:8000
http://cvmfs-stratum-one.ihep.ac.cn:8000
http://sampacs01.if.usp.br:8000
http://s1brisbane-cvmfs.openhtc.io
http://s1melbourne-cvmfs.openhtc.io
http://s1nikhef-cvmfs.openhtc.io
http://s1osggoc-cvmfs.openhtc.io:8080
http://s1bnl-cvmfs.openhtc.io
http://s1fnal-cvmfs.openhtc.io:8080
http://s1sampa-cvmfs.openhtc.io:8080
"
HOST_POOL="${NEURODESKTOP_CVMFS_HOST_POOL:-${DEFAULT_HOST_POOL}}"

# Static fallback when nothing is measurable (e.g. captive portal lets DNS
# through but blocks HTTP): geo-DNS plus CDN entry points, GeoAPI ordering.
# Keep in sync with config/cvmfs/neurodesk.ardc.edu.au.conf.
FALLBACK_SERVER_URL="http://cvmfs-geoproximity.neurodesk.org/cvmfs/@fqrn@;http://cvmfs.neurodesk.org/cvmfs/@fqrn@;http://s1brisbane-cvmfs.openhtc.io/cvmfs/@fqrn@;http://s1nikhef-cvmfs.openhtc.io/cvmfs/@fqrn@"

log() { echo "[cvmfs-select] $*"; }

restore_home_cache_ownership() {
    local cache_path home_uid

    [ "$(id -u)" -eq 0 ] || return 0
    case "${NB_UID:-}" in ''|*[!0-9]*) return 0 ;; esac
    case "${NB_GID:-}" in ''|*[!0-9]*) return 0 ;; esac
    [ -d "${HOME}" ] || return 0
    home_uid=$(stat -c "%u" "${HOME}" 2>/dev/null || true)
    [ "${home_uid}" = "${NB_UID}" ] || return 0
    case "${CACHE_FILE}" in
        "${HOME}"/*) ;;
        *) return 0 ;;
    esac

    cache_path="${CACHE_FILE}"
    while [ "${cache_path}" != "${HOME}" ]; do
        if [ -e "${cache_path}" ] && ! chown "${NB_UID}:${NB_GID}" "$cache_path"; then
            log "WARNING: could not restore notebook-user ownership of ${cache_path}"
        fi
        cache_path=$(dirname "${cache_path}")
    done
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
python3 "${script_dir}/cvmfs_server_select.py" \
    --host-pool "$HOST_POOL" --target-config "$TARGET_CONFIG" \
    --cache-file "$CACHE_FILE" --ttl "$TTL_SECONDS" \
    --fallback "$FALLBACK_SERVER_URL" "$@"
result=$?
restore_home_cache_ownership
exit "$result"
