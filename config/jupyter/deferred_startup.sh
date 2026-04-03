#!/bin/bash
# deferred_startup.sh
# Background worker that starts CVMFS and Slurm after Jupyter is listening.
# Launched from before_notebook.sh when lazy startup mode is active.
# Logs to /tmp/neurodesktop-deferred-startup.log

set -o pipefail

DEFERRED_LOG="/tmp/neurodesktop-deferred-startup.log"
DEFERRED_LOCK="/tmp/neurodesktop-deferred-startup.lock"
DEFERRED_DONE="/tmp/neurodesktop-deferred-startup.done"
CVMFS_CACHE_DIR="${HOME}/.cache/neurodesktop"
CVMFS_CACHE_FILE="${CVMFS_CACHE_DIR}/cvmfs-selection.env"

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

    # ── Cache-first CVMFS selection ───────────────────────────────────────
    local FASTEST_REGION=""
    local FASTEST_MODE=""
    local used_cache=false

    if [ -f "$CVMFS_CACHE_FILE" ]; then
        # shellcheck source=/dev/null
        source "$CVMFS_CACHE_FILE"
        FASTEST_REGION="${CACHED_REGION:-}"
        FASTEST_MODE="${CACHED_MODE:-}"
        if [ -n "$FASTEST_REGION" ] && [ -n "$FASTEST_MODE" ]; then
            echo "[deferred] Trying cached CVMFS selection: region=$FASTEST_REGION mode=$FASTEST_MODE"
            local config_file_suffix="${FASTEST_MODE}.${FASTEST_REGION}"
            local source_config="/etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.${config_file_suffix}"
            local target_config="/etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf"
            if [ -f "$source_config" ]; then
                cp "$source_config" "$target_config"
                mkdir -p /cvmfs/neurodesk.ardc.edu.au
                if mount -t cvmfs neurodesk.ardc.edu.au /cvmfs/neurodesk.ardc.edu.au 2>/dev/null; then
                    if ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ >/dev/null 2>&1; then
                        echo "[deferred] CVMFS mounted successfully using cached selection."
                        used_cache=true
                    else
                        echo "[deferred] Cached selection mounted but modules not available. Re-probing."
                        umount /cvmfs/neurodesk.ardc.edu.au 2>/dev/null || true
                    fi
                else
                    echo "[deferred] Cached selection failed to mount. Re-probing."
                fi
            else
                echo "[deferred] Cached config file not found: $source_config. Re-probing."
            fi
        fi
    fi

    if [ "$used_cache" = "false" ]; then
        # ── Full probe logic ──────────────────────────────────────────────

        # Probe a server with multiple requests and return the median latency.
        get_latency() {
            local url="$1"
            local server_name="$2"
            local num_probes=3
            echo "Testing $url ($num_probes probes)" >&2
            local resolved_dns
            resolved_dns=$(dig +short "$server_name" 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]*$//')
            echo "[DEBUG]: Resolved DNS for $server_name: $resolved_dns" >&2
            local latencies=()
            local i
            for i in $(seq 1 "$num_probes"); do
                local output exit_code
                output=$(curl --no-keepalive --connect-timeout 3 -s -w "%{time_total} %{http_code}" -o /dev/null "$url")
                exit_code=$?
                if [ $exit_code -eq 0 ]; then
                    local time status
                    time=$(echo "$output" | awk '{print $1}')
                    status=$(echo "$output" | awk '{print $2}')
                    if [ "$status" -eq 200 ]; then
                        latencies+=("$time")
                        echo "  Probe $i: ${time}s" >&2
                    else
                        echo "  Probe $i: HTTP $status (failed)" >&2
                        latencies+=("999")
                    fi
                else
                    echo "  Probe $i: curl error $exit_code" >&2
                    latencies+=("999")
                fi
            done
            printf '%s\n' "${latencies[@]}" | sort -n | sed -n "$((( num_probes + 1 ) / 2))p"
        }

        get_throughput_time() {
            local base_url="$1"
            echo "Throughput test: $base_url" >&2
            local published catalog_hash
            published=$(curl --connect-timeout 5 -s "${base_url}/.cvmfspublished")
            catalog_hash=$(echo "$published" | awk '/^C/{print substr($0,2); exit}')
            if [ -z "$catalog_hash" ]; then
                echo "  Could not parse catalog hash" >&2
                echo "999"
                return
            fi
            local prefix="${catalog_hash:0:2}"
            local suffix="${catalog_hash:2}"
            local catalog_url="${base_url}/data/${prefix}/${suffix}C"
            echo "  Downloading catalog: $catalog_url" >&2
            local output exit_code
            output=$(curl --no-keepalive --connect-timeout 10 --max-time 30 -s \
                -w "%{time_total} %{size_download} %{speed_download} %{http_code}" \
                -o /dev/null "$catalog_url")
            exit_code=$?
            if [ $exit_code -eq 0 ]; then
                local time size speed status
                time=$(echo "$output" | awk '{print $1}')
                size=$(echo "$output" | awk '{print $2}')
                speed=$(echo "$output" | awk '{print $3}')
                status=$(echo "$output" | awk '{print $4}')
                if [ "$status" -eq 200 ] && [ "$size" != "0" ]; then
                    echo "  Throughput: $(awk "BEGIN {printf \"%.1f\", $speed/1024}") KB/s (${size} bytes in ${time}s)" >&2
                    echo "$time"
                    return
                fi
            fi
            echo "  Catalog download failed (exit $exit_code)" >&2
            echo "999"
        }

        echo "[deferred] Probing regional servers (Europe, America, Asia)..."
        EUROPE_HOST=cvmfs-frankfurt.neurodesk.org
        EUROPE_HOST_BACKUP=cvmfs01.nikhef.nl
        AMERICA_HOST=cvmfs-jetstream.neurodesk.org
        AMERICA_HOST_BACKUP=cvmfs-s1bnl.opensciencegrid.org
        ASIA_HOST=cvmfs-brisbane.neurodesk.org
        ASIA_HOST_BACKUP=cvmfs-perth.neurodesk.org

        EUROPE_url="http://${EUROPE_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
        AMERICA_url="http://${AMERICA_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
        ASIA_url="http://${ASIA_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"

        EUROPE_url_backup="http://${EUROPE_HOST_BACKUP}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
        AMERICA_url_backup="http://${AMERICA_HOST_BACKUP}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
        ASIA_url_backup="http://${ASIA_HOST_BACKUP}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"

        _probe_tmpdir=$(mktemp -d)

        (
            _lat=$(get_latency "$EUROPE_url" "$EUROPE_HOST")
            if [ "$_lat" == "999" ]; then
                echo "Primary Europe server failed, trying backup..." >&2
                _lat=$(get_latency "$EUROPE_url_backup" "$EUROPE_HOST_BACKUP")
            fi
            echo "$_lat" > "$_probe_tmpdir/europe"
        ) &

        (
            _lat=$(get_latency "$AMERICA_url" "$AMERICA_HOST")
            if [ "$_lat" == "999" ]; then
                echo "Primary America server failed, trying backup..." >&2
                _lat=$(get_latency "$AMERICA_url_backup" "$AMERICA_HOST_BACKUP")
            fi
            echo "$_lat" > "$_probe_tmpdir/america"
        ) &

        (
            _lat=$(get_latency "$ASIA_url" "$ASIA_HOST")
            if [ "$_lat" == "999" ]; then
                echo "Primary Asia server failed, trying backup..." >&2
                _lat=$(get_latency "$ASIA_url_backup" "$ASIA_HOST_BACKUP")
            fi
            echo "$_lat" > "$_probe_tmpdir/asia"
        ) &

        wait

        EUROPE_latency=$(cat "$_probe_tmpdir/europe")
        AMERICA_latency=$(cat "$_probe_tmpdir/america")
        ASIA_latency=$(cat "$_probe_tmpdir/asia")
        rm -rf "$_probe_tmpdir"

        echo "Europe Latency (median of 3): ${EUROPE_latency}s"
        echo "America Latency (median of 3): ${AMERICA_latency}s"
        echo "Asia Latency (median of 3): ${ASIA_latency}s"

        SORTED_REGIONS=$(printf "%s europe\n%s america\n%s asia\n" \
            "$EUROPE_latency" "$AMERICA_latency" "$ASIA_latency" | sort -n)
        FASTEST_REGION=$(echo "$SORTED_REGIONS" | awk 'NR==1{print $2}')
        FASTEST_LATENCY=$(echo "$SORTED_REGIONS" | awk 'NR==1{print $1}')
        SECOND_REGION=$(echo "$SORTED_REGIONS" | awk 'NR==2{print $2}')
        SECOND_LATENCY=$(echo "$SORTED_REGIONS" | awk 'NR==2{print $1}')

        echo "Fastest region by latency: $FASTEST_REGION (${FASTEST_LATENCY}s), runner-up: $SECOND_REGION (${SECOND_LATENCY}s)"

        if [ "$FASTEST_LATENCY" != "999" ] && [ "$SECOND_LATENCY" != "999" ]; then
            MARGIN=$(awk "BEGIN {if ($FASTEST_LATENCY > 0) printf \"%.2f\", ($SECOND_LATENCY - $FASTEST_LATENCY) / $FASTEST_LATENCY; else print 999}")
            echo "Latency margin: $(awk "BEGIN {printf \"%.0f\", $MARGIN * 100}")%"

            if [ "$(awk "BEGIN {print ($MARGIN < 0.40) ? 1 : 0}")" -eq 1 ]; then
                echo "Margin is <40% — running throughput tiebreaker between $FASTEST_REGION and $SECOND_REGION..."

                _region_to_base_url() {
                    case "$1" in
                        europe)  echo "http://${EUROPE_HOST}/cvmfs/neurodesk.ardc.edu.au" ;;
                        america) echo "http://${AMERICA_HOST}/cvmfs/neurodesk.ardc.edu.au" ;;
                        asia)    echo "http://${ASIA_HOST}/cvmfs/neurodesk.ardc.edu.au" ;;
                    esac
                }

                _tp_tmpdir=$(mktemp -d)
                (get_throughput_time "$(_region_to_base_url "$FASTEST_REGION")" > "$_tp_tmpdir/first") &
                (get_throughput_time "$(_region_to_base_url "$SECOND_REGION")" > "$_tp_tmpdir/second") &
                wait

                FIRST_TP=$(cat "$_tp_tmpdir/first")
                SECOND_TP=$(cat "$_tp_tmpdir/second")
                rm -rf "$_tp_tmpdir"

                echo "Throughput time for $FASTEST_REGION: ${FIRST_TP}s"
                echo "Throughput time for $SECOND_REGION: ${SECOND_TP}s"

                if [ "$FIRST_TP" != "999" ] && [ "$SECOND_TP" != "999" ]; then
                    if [ "$(awk "BEGIN {print ($SECOND_TP < $FIRST_TP) ? 1 : 0}")" -eq 1 ]; then
                        echo "Throughput tiebreaker: switching from $FASTEST_REGION to $SECOND_REGION (faster bulk transfer)"
                        FASTEST_REGION="$SECOND_REGION"
                    else
                        echo "Throughput tiebreaker confirms $FASTEST_REGION"
                    fi
                else
                    echo "Throughput tiebreaker inconclusive — keeping $FASTEST_REGION"
                fi
            fi
        fi

        # Probe Direct vs CDN modes in parallel
        echo "Probing connection modes (Direct vs CDN)..."
        DIRECT_HOST=cvmfs-geoproximity.neurodesk.org
        CDN_HOST=cvmfs.neurodesk.org

        DIRECT_url="http://${DIRECT_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"
        CDN_url="http://${CDN_HOST}/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished"

        _mode_tmpdir=$(mktemp -d)
        (get_latency "$DIRECT_url" "$DIRECT_HOST" > "$_mode_tmpdir/direct") &
        (get_latency "$CDN_url" "$CDN_HOST" > "$_mode_tmpdir/cdn") &
        wait

        DIRECT_latency=$(cat "$_mode_tmpdir/direct")
        CDN_latency=$(cat "$_mode_tmpdir/cdn")
        rm -rf "$_mode_tmpdir"

        echo "Direct Latency (median of 3): ${DIRECT_latency}s"
        echo "CDN Latency (median of 3): ${CDN_latency}s"

        FASTEST_MODE=$(printf "%s direct\n%s cdn\n" "$DIRECT_latency" "$CDN_latency" | sort -n | head -n 1 | awk '{print $2}')

        echo "Fastest region determined: $FASTEST_REGION"
        echo "Fastest mode determined: $FASTEST_MODE"

        # Copy selected config and mount
        config_file_suffix="${FASTEST_MODE}.${FASTEST_REGION}"
        source_config="/etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.${config_file_suffix}"
        target_config="/etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf"

        if [ -f "$source_config" ]; then
            echo "Selected config file: $source_config"
            cp "$source_config" "$target_config"
        fi

        echo "Mounting CVMFS"
        if [ -x /etc/init.d/autofs ] && service autofs status >/dev/null 2>&1; then
            echo "autofs is running - not attempting to mount manually:"
            ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ 2>/dev/null && echo "CVMFS is ready after autofs mount" || echo "AutoFS not working!"
        else
            mkdir -p /cvmfs/neurodesk.ardc.edu.au
            mount -t cvmfs neurodesk.ardc.edu.au /cvmfs/neurodesk.ardc.edu.au

            ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ 2>/dev/null && echo "CVMFS is ready after manual mount" || echo "Manual CVMFS mount not successful"

            echo "CVMFS servers:"
            if [ "$FASTEST_MODE" = "direct" ]; then
                cvmfs_talk -i neurodesk.ardc.edu.au host probe 2>/dev/null || true
            fi
            cvmfs_talk -i neurodesk.ardc.edu.au host info 2>/dev/null || true
        fi

        # Save successful selection to cache
        if [ -d "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/" ]; then
            mkdir -p "$CVMFS_CACHE_DIR"
            cat > "$CVMFS_CACHE_FILE" <<CACHE_EOF
# CVMFS server selection cache - auto-generated by deferred_startup.sh
CACHED_REGION="${FASTEST_REGION}"
CACHED_MODE="${FASTEST_MODE}"
CACHE_EOF
            echo "[deferred] Saved CVMFS selection to cache: region=$FASTEST_REGION mode=$FASTEST_MODE"
        fi
    fi

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
