#!/bin/bash
# Persist the host CVMFS client cache between image test runs.
#
#   init ARCH  create the cache directory and print actions/cache keys
#   prepare    hand the directory to the CVMFS client as an alien cache
#   export     hand the directory back to the runner for actions/cache/save
#
# An alien cache has no quota database, so a restored snapshot cannot
# disagree with client bookkeeping. Keys roll over each ISO week, which
# bounds growth and makes the first run of each week fetch live from CVMFS.
set -euo pipefail

CACHE_DIR="${NEURODESKTOP_CVMFS_CI_CACHE_DIR:-/var/cache/neurodesk-cvmfs}"
CONFIG_DIR="${NEURODESKTOP_CVMFS_CONFIG_DIR:-/etc/cvmfs/config.d}"
CVMFS_USER="${NEURODESKTOP_CVMFS_USER:-cvmfs}"

case "${1:-}" in
    init)
        arch="${2:?usage: cvmfs_ci_cache.sh init ARCH}"
        sudo install -d -o "$(id -u)" -g "$(id -g)" "$CACHE_DIR"
        prefix="neurodesk-cvmfs-alien-v1-${arch}-$(date -u +%G-W%V)-"
        echo "path=${CACHE_DIR}"
        echo "restore-prefix=${prefix}"
        echo "key=${prefix}${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
        ;;
    prepare)
        sudo chown -R "${CVMFS_USER}:${CVMFS_USER}" "$CACHE_DIR"
        printf '%s\n' \
            "CVMFS_ALIEN_CACHE=${CACHE_DIR}" \
            "CVMFS_QUOTA_LIMIT=-1" \
            "CVMFS_SHARED_CACHE=no" |
            sudo tee "${CONFIG_DIR}/neurodesk.ardc.edu.au.local" >/dev/null
        echo "CVMFS alien cache: $(sudo du -sh "$CACHE_DIR" | cut -f1) restored in ${CACHE_DIR}"
        ;;
    export)
        sudo rm -rf "${CACHE_DIR}/txn"
        sudo chown -R "$(id -u):$(id -g)" "$CACHE_DIR"
        echo "CVMFS alien cache: $(du -sh "$CACHE_DIR" | cut -f1) to save from ${CACHE_DIR}"
        ;;
    *)
        echo "usage: cvmfs_ci_cache.sh init ARCH | prepare | export" >&2
        exit 2
        ;;
esac
