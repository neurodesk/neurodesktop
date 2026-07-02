#!/bin/bash
# deferred_startup.sh
# Background worker that starts CVMFS and Slurm after Jupyter is listening.
# Launched from before_notebook.sh when lazy startup mode is active.
# Logs to /tmp/neurodesktop-deferred-startup.log

set -o pipefail

DEFERRED_LOG="/tmp/neurodesktop-deferred-startup.log"
DEFERRED_LOCK="/tmp/neurodesktop-deferred-startup.lock"
DEFERRED_DONE="/tmp/neurodesktop-deferred-startup.done"

exec >> "$DEFERRED_LOG" 2>&1

# Prevent duplicate execution
if ! mkdir "$DEFERRED_LOCK" 2>/dev/null; then
    echo "[deferred] Already running. Exiting."
    exit 0
fi
cleanup_lock() { rmdir "$DEFERRED_LOCK" 2>/dev/null || true; }
trap cleanup_lock EXIT

# Phase timing helpers
_phase_start() { _PHASE_T0=$(date +%s%3N); echo "[TIMING] $1 started"; }
_phase_end()   { local elapsed=$(( $(date +%s%3N) - _PHASE_T0 )); echo "[TIMING] $1 completed in ${elapsed}ms"; }

# ── Wait for Jupyter ─────────────────────────────────────────────────────────
_phase_start "wait-for-jupyter"
MAX_WAIT=120
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if ss -tln 2>/dev/null | grep -q ':8888 '; then
        break
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done
if [ $WAITED -ge $MAX_WAIT ]; then
    echo "[deferred] Jupyter did not become available within ${MAX_WAIT}s. Proceeding anyway."
fi
_phase_end "wait-for-jupyter"

# ── CVMFS ────────────────────────────────────────────────────────────────────
start_cvmfs() {
    local cvmfs_startup_mode="${NEURODESKTOP_CVMFS_STARTUP_MODE:-lazy}"
    if [ "$cvmfs_startup_mode" != "lazy" ]; then
        echo "[deferred] CVMFS startup mode is '$cvmfs_startup_mode', skipping deferred CVMFS."
        return 0
    fi

    if [ "${CVMFS_DISABLE:-false}" = "true" ]; then
        echo "[deferred] CVMFS is disabled. Skipping."
        return 0
    fi

    if [ -d "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/" ]; then
        echo "[deferred] CVMFS already mounted. Skipping."
        return 0
    fi

    _phase_start "cvmfs-mount"

    # Check internet connectivity
    if ! timeout 3 nslookup neurodesk.org >/dev/null 2>&1; then
        echo "[deferred] No internet connection. Disabling CVMFS."
        export CVMFS_DISABLE=true
        _phase_end "cvmfs-mount"
        return 0
    fi

    # Needs to be kept in sync with config/cvmfs/default.local
    CACHE_DIR="${HOME}/cvmfs_cache"
    if [ ! -d "$CACHE_DIR" ]; then
        echo "[deferred] Creating CVMFS cache directory at $CACHE_DIR"
        mkdir -p "$CACHE_DIR"
    fi
    chmod 755 "${HOME}"
    if sudo -n true 2>/dev/null; then
        chown -R cvmfs:root "$CACHE_DIR"
    fi

    # Try autofs or external mount first
    ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ 2>/dev/null && {
        echo "[deferred] CVMFS is ready (external mount)."
        _phase_end "cvmfs-mount"
        return 0
    }

    # ── Server selection ──────────────────────────────────────────────────
    # Rank the CVMFS servers by measured download throughput and write the
    # repository config with the fastest server first. The script reuses a
    # cached ranking (with health check and TTL) when one is available; see
    # cvmfs_server_select.sh for details.
    if [ -x /opt/neurodesktop/cvmfs_server_select.sh ]; then
        /opt/neurodesktop/cvmfs_server_select.sh || echo "[deferred] [WARN] No CVMFS server measurable; wrote static fallback config."
    else
        echo "[deferred] [WARN] cvmfs_server_select.sh not found. Using existing CVMFS config."
    fi

    mount_cvmfs() {
        if [ -x /etc/init.d/autofs ] && service autofs status >/dev/null 2>&1; then
            echo "[deferred] autofs is running - not attempting to mount manually."
        else
            mkdir -p /cvmfs/neurodesk.ardc.edu.au
            mount -t cvmfs neurodesk.ardc.edu.au /cvmfs/neurodesk.ardc.edu.au 2>/dev/null
        fi
        ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ >/dev/null 2>&1
    }

    echo "[deferred] Mounting CVMFS"
    if mount_cvmfs; then
        echo "[deferred] CVMFS is ready."
    else
        # A cached selection can go stale (network change, server gone):
        # probe from scratch and try once more.
        echo "[deferred] CVMFS mount failed. Re-probing servers and retrying."
        umount /cvmfs/neurodesk.ardc.edu.au 2>/dev/null || true
        if [ -x /opt/neurodesktop/cvmfs_server_select.sh ]; then
            /opt/neurodesktop/cvmfs_server_select.sh --force-probe || true
        fi
        if mount_cvmfs; then
            echo "[deferred] CVMFS is ready after re-probe."
        else
            echo "[deferred] Manual CVMFS mount not successful."
        fi
    fi

    # Note: no `host probe` here - it reorders the host chain by round-trip
    # time, which would undo the throughput ranking.
    cvmfs_talk -i neurodesk.ardc.edu.au host info 2>/dev/null || true

    # Re-source environment variables so MODULEPATH picks up CVMFS.
    # Unset the guard so the script re-evaluates paths with CVMFS now mounted.
    if [ -f /opt/neurodesktop/environment_variables.sh ]; then
        unset NEURODESKTOP_ENV_SOURCED
        source /opt/neurodesktop/environment_variables.sh > /dev/null 2>&1
    fi

    _phase_end "cvmfs-mount"
}

# ── Slurm ────────────────────────────────────────────────────────────────────
start_slurm() {
    local slurm_startup_mode="${NEURODESKTOP_SLURM_STARTUP_MODE:-lazy}"
    if [ "$slurm_startup_mode" != "lazy" ]; then
        echo "[deferred] Slurm startup mode is '$slurm_startup_mode', skipping deferred Slurm."
        return 0
    fi

    if [ "${NEURODESKTOP_SLURM_ENABLE:-1}" = "0" ]; then
        echo "[deferred] Slurm is disabled. Skipping."
        return 0
    fi

    if [ "${NEURODESKTOP_SLURM_MODE:-local}" = "host" ]; then
        echo "[deferred] Slurm mode is 'host'. Skipping local Slurm startup."
        return 0
    fi

    _phase_start "slurm-startup"

    if [ "$EUID" -eq 0 ]; then
        if ! /opt/neurodesktop/setup_and_start_slurm.sh; then
            echo "[deferred] [WARN] Failed to configure/start local Slurm queue."
        fi
    elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        if ! sudo -n env \
            NB_USER="${NB_USER:-jovyan}" \
            NEURODESKTOP_SLURM_ENABLE="${NEURODESKTOP_SLURM_ENABLE:-1}" \
            NEURODESKTOP_SLURM_MEMORY_RESERVE_MB="${NEURODESKTOP_SLURM_MEMORY_RESERVE_MB:-256}" \
            NEURODESKTOP_SLURM_PARTITION="${NEURODESKTOP_SLURM_PARTITION:-neurodesktop}" \
            NEURODESKTOP_MUNGE_NUM_THREADS="${NEURODESKTOP_MUNGE_NUM_THREADS:-10}" \
            NEURODESKTOP_SLURM_USE_CGROUP="${NEURODESKTOP_SLURM_USE_CGROUP:-auto}" \
            NEURODESKTOP_SLURM_CGROUP_PLUGIN="${NEURODESKTOP_SLURM_CGROUP_PLUGIN:-autodetect}" \
            NEURODESKTOP_SLURM_CGROUP_MOUNTPOINT="${NEURODESKTOP_SLURM_CGROUP_MOUNTPOINT:-/sys/fs/cgroup}" \
            /opt/neurodesktop/setup_and_start_slurm.sh; then
            echo "[deferred] [WARN] Failed to configure/start local Slurm queue via passwordless sudo."
        fi
    else
        echo "[deferred] [WARN] Not running as root and passwordless sudo is unavailable; skipping local Slurm startup."
    fi

    _phase_end "slurm-startup"
}

# ── Run deferred components ──────────────────────────────────────────────────
echo "[deferred] Starting deferred initialization..."
start_cvmfs
start_slurm
echo "[deferred] Deferred initialization complete."
touch "$DEFERRED_DONE"
