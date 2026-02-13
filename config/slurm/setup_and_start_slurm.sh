#!/bin/bash
set -euo pipefail

is_false() {
    local value
    value="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
    case "${value}" in
        0|false|no|off)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

can_write_dir() {
    local dir probe
    dir="$1"

    if [ ! -d "${dir}" ]; then
        return 1
    fi

    probe="${dir}/.nd-write-test-$$"
    if mkdir "${probe}" >/dev/null 2>&1; then
        rmdir "${probe}" >/dev/null 2>&1 || true
        return 0
    fi

    return 1
}

if is_false "${NEURODESKTOP_SLURM_ENABLE:-1}"; then
    echo "[INFO] Slurm startup disabled via NEURODESKTOP_SLURM_ENABLE."
    exit 0
fi

if [ "${EUID}" -ne 0 ]; then
    echo "[WARN] Slurm setup requires root. Skipping."
    exit 0
fi

if ! command -v slurmctld >/dev/null 2>&1 || ! command -v slurmd >/dev/null 2>&1 || ! command -v munged >/dev/null 2>&1; then
    echo "[WARN] Slurm or MUNGE binaries not found. Skipping Slurm startup."
    exit 0
fi

if ! id slurm >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/slurm --shell /usr/sbin/nologin slurm
fi

if ! id munge >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/munge --shell /usr/sbin/nologin munge
fi

detect_cpu_limit() {
    local cpu_limit
    cpu_limit="$(nproc 2>/dev/null || echo 1)"

    if [ -f /sys/fs/cgroup/cpu.max ]; then
        local quota period quota_limit
        read -r quota period < /sys/fs/cgroup/cpu.max || true
        if [ -n "${quota:-}" ] && [ "${quota}" != "max" ] && [ -n "${period:-}" ] && [ "${period}" -gt 0 ]; then
            quota_limit="$(awk -v q="$quota" -v p="$period" 'BEGIN { v=int(q/p); if (v < 1) v = 1; print v }')"
            if [ "${quota_limit}" -lt "${cpu_limit}" ]; then
                cpu_limit="${quota_limit}"
            fi
        fi
    fi

    local cpuset_file cpuset_value cpuset_limit
    for cpuset_file in /sys/fs/cgroup/cpuset.cpus.effective /sys/fs/cgroup/cpuset.cpus; do
        if [ -f "${cpuset_file}" ]; then
            cpuset_value="$(tr -d '[:space:]' < "${cpuset_file}")"
            if [ -n "${cpuset_value}" ]; then
                cpuset_limit="$(awk -v list="${cpuset_value}" '
                    BEGIN {
                        total = 0
                        split(list, chunks, ",")
                        for (i in chunks) {
                            if (chunks[i] ~ /^[0-9]+-[0-9]+$/) {
                                split(chunks[i], bounds, "-")
                                total += (bounds[2] - bounds[1] + 1)
                            } else if (chunks[i] ~ /^[0-9]+$/) {
                                total += 1
                            }
                        }
                        print total
                    }'
                )"
                if [ -n "${cpuset_limit}" ] && [ "${cpuset_limit}" -gt 0 ] && [ "${cpuset_limit}" -lt "${cpu_limit}" ]; then
                    cpu_limit="${cpuset_limit}"
                fi
                break
            fi
        fi
    done

    if [ "${cpu_limit}" -lt 1 ]; then
        cpu_limit=1
    fi

    echo "${cpu_limit}"
}

detect_memory_limit_mb() {
    local mem_kb mem_mb cgroup_bytes cgroup_mb reserve_mb
    mem_kb="$(awk '/MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || echo 1048576)"
    mem_mb=$((mem_kb / 1024))

    if [ -f /sys/fs/cgroup/memory.max ]; then
        cgroup_bytes="$(cat /sys/fs/cgroup/memory.max)"
        if [ "${cgroup_bytes}" != "max" ]; then
            cgroup_mb=$((cgroup_bytes / 1024 / 1024))
            if [ "${cgroup_mb}" -gt 0 ] && [ "${cgroup_mb}" -lt "${mem_mb}" ]; then
                mem_mb="${cgroup_mb}"
            fi
        fi
    fi

    reserve_mb="${NEURODESKTOP_SLURM_MEMORY_RESERVE_MB:-256}"
    if ! [[ "${reserve_mb}" =~ ^[0-9]+$ ]]; then
        reserve_mb=256
    fi
    if [ "${mem_mb}" -gt $((reserve_mb + 256)) ]; then
        mem_mb=$((mem_mb - reserve_mb))
    fi

    if [ "${mem_mb}" -lt 256 ]; then
        mem_mb=256
    fi

    echo "${mem_mb}"
}

detect_node_addr() {
    local host ip
    host="$1"
    ip="$(getent ahostsv4 "${host}" 2>/dev/null | awk 'NR==1 {print $1}')"
    if [ -z "${ip}" ]; then
        ip="$(hostname -i 2>/dev/null | awk '{print $1}')"
    fi
    if [ -z "${ip}" ]; then
        ip="127.0.0.1"
    fi
    echo "${ip}"
}

SLURM_ETC_DIR=/etc/slurm
SLURM_CONF_PATH="${SLURM_ETC_DIR}/slurm.conf"
SLURM_CGROUP_CONF_PATH="${SLURM_ETC_DIR}/cgroup.conf"
SLURMCTLD_PID_FILE=/run/slurm/slurmctld.pid
SLURMD_PID_FILE=/run/slurm/slurmd.pid

mkdir -p "${SLURM_ETC_DIR}" /run/slurm /var/log/slurm /var/spool/slurmctld /var/spool/slurmd
mkdir -p /etc/munge /run/munge /var/log/munge

chown -R slurm:slurm /run/slurm /var/log/slurm /var/spool/slurmctld /var/spool/slurmd
chown -R munge:munge /etc/munge /run/munge
chown -R root:root /var/log/munge
# /etc/munge must stay private, but /run/munge needs traversal for non-root clients.
chmod 0700 /etc/munge
chmod 0755 /run/munge
chmod 0700 /var/log/munge
touch /var/log/slurm/slurmctld.log /var/log/slurm/slurmd.log
touch /var/log/munge/munged.log
chown slurm:slurm /var/log/slurm/slurmctld.log /var/log/slurm/slurmd.log
chmod 0644 /var/log/slurm/slurmctld.log /var/log/slurm/slurmd.log
chown root:root /var/log/munge/munged.log
chmod 0600 /var/log/munge/munged.log

if [ ! -s /etc/munge/munge.key ]; then
    if command -v create-munge-key >/dev/null 2>&1; then
        create-munge-key >/dev/null 2>&1 || create-munge-key -f >/dev/null 2>&1
    elif command -v mungekey >/dev/null 2>&1; then
        mungekey --create >/dev/null
    else
        dd if=/dev/urandom of=/etc/munge/munge.key bs=1 count=1024 status=none
    fi
fi

chown munge:munge /etc/munge/munge.key
chmod 0400 /etc/munge/munge.key

MUNGE_NUM_THREADS="${NEURODESKTOP_MUNGE_NUM_THREADS:-10}"
if ! [[ "${MUNGE_NUM_THREADS}" =~ ^[0-9]+$ ]] || [ "${MUNGE_NUM_THREADS}" -lt 1 ]; then
    MUNGE_NUM_THREADS=10
fi

if ! pgrep -x munged >/dev/null 2>&1; then
    /usr/sbin/munged --force --num-threads "${MUNGE_NUM_THREADS}"
fi

if [ -S /run/munge/munge.socket.2 ]; then
    chmod 0777 /run/munge/munge.socket.2
fi

NODE_HOSTNAME="$(hostname -s 2>/dev/null || hostname)"
NODE_HOSTNAME_FULL="$(hostname 2>/dev/null || echo "${NODE_HOSTNAME}")"
NODE_ADDR="$(detect_node_addr "${NODE_HOSTNAME}")"
NODE_CPUS="$(detect_cpu_limit)"
NODE_MEMORY_MB="$(detect_memory_limit_mb)"
PARTITION_NAME="${NEURODESKTOP_SLURM_PARTITION:-neurodesktop}"
CGROUP_PLUGIN="${NEURODESKTOP_SLURM_CGROUP_PLUGIN:-autodetect}"
CGROUP_MOUNTPOINT="${NEURODESKTOP_SLURM_CGROUP_MOUNTPOINT:-/sys/fs/cgroup}"
USE_CGROUP_MODE=0
CGROUP_DISABLE_REASON=""

if is_false "${NEURODESKTOP_SLURM_USE_CGROUP:-auto}"; then
    CGROUP_DISABLE_REASON="disabled via NEURODESKTOP_SLURM_USE_CGROUP"
elif [ "${NEURODESKTOP_SLURM_USE_CGROUP:-auto}" = "auto" ]; then
    # Default to non-cgroup mode in containers; opt in with NEURODESKTOP_SLURM_USE_CGROUP=1.
    CGROUP_DISABLE_REASON="auto mode defaults to non-cgroup mode for container compatibility"
else
    if [ ! -d "${CGROUP_MOUNTPOINT}" ]; then
        CGROUP_DISABLE_REASON="mountpoint ${CGROUP_MOUNTPOINT} is missing"
    elif [ -f "${CGROUP_MOUNTPOINT}/cgroup.controllers" ]; then
        # cgroup v2: slurmd needs writable scope directories under system.slice when IgnoreSystemd=yes.
        if ! mkdir -p "${CGROUP_MOUNTPOINT}/system.slice" >/dev/null 2>&1 || \
           ! can_write_dir "${CGROUP_MOUNTPOINT}/system.slice"; then
            CGROUP_DISABLE_REASON="cgroup v2 system.slice is not writable"
        elif ! mkdir -p "${CGROUP_MOUNTPOINT}/system.slice/${NODE_HOSTNAME}_slurmstepd.scope" >/dev/null 2>&1; then
            CGROUP_DISABLE_REASON="cgroup v2 slurmstepd scope directory is not creatable"
        elif [ "${NODE_HOSTNAME_FULL}" != "${NODE_HOSTNAME}" ] && \
             ! mkdir -p "${CGROUP_MOUNTPOINT}/system.slice/${NODE_HOSTNAME_FULL}_slurmstepd.scope" >/dev/null 2>&1; then
            CGROUP_DISABLE_REASON="cgroup v2 slurmstepd scope directory is not creatable"
        else
            USE_CGROUP_MODE=1
        fi
    elif can_write_dir "${CGROUP_MOUNTPOINT}"; then
        USE_CGROUP_MODE=1
    else
        CGROUP_DISABLE_REASON="mountpoint ${CGROUP_MOUNTPOINT} is not writable"
    fi
fi

if [ "${USE_CGROUP_MODE}" -eq 1 ]; then
    mkdir -p "${CGROUP_MOUNTPOINT}" >/dev/null 2>&1 || true
    PROCTRACK_TYPE="proctrack/cgroup"
    TASK_PLUGIN="task/cgroup,task/affinity"
    JOBACCT_GATHER_TYPE="jobacct_gather/cgroup"
    CGROUP_CONSTRAIN_CORES="yes"
    CGROUP_CONSTRAIN_RAM="yes"
    CGROUP_CONSTRAIN_SWAP="yes"
    echo "[INFO] Slurm cgroup mode enabled (plugin: ${CGROUP_PLUGIN}, mountpoint: ${CGROUP_MOUNTPOINT})."
else
    PROCTRACK_TYPE="proctrack/linuxproc"
    TASK_PLUGIN="task/affinity"
    JOBACCT_GATHER_TYPE="jobacct_gather/none"
    CGROUP_CONSTRAIN_CORES="no"
    CGROUP_CONSTRAIN_RAM="no"
    CGROUP_CONSTRAIN_SWAP="no"
    if [ -n "${CGROUP_DISABLE_REASON}" ]; then
        echo "[INFO] Slurm cgroup mode disabled (${CGROUP_DISABLE_REASON})."
    else
        echo "[INFO] Slurm cgroup mode disabled."
    fi
fi

SLURMD_FALLBACK_ATTEMPTED=0
SLURMD_FALLBACK_REASON=""
SLURMD_LEGACY_CGROUP_COMPAT_ATTEMPTED=0
SLURMD_LEGACY_CGROUP_COMPAT_REASON=""
LEGACY_CGROUP_COMPAT_PLUGIN="${NEURODESKTOP_SLURM_LEGACY_CGROUP_PLUGIN:-cgroup/v1}"
LEGACY_CGROUP_COMPAT_MOUNTPOINT="${NEURODESKTOP_SLURM_LEGACY_CGROUP_MOUNTPOINT:-/tmp/cgroup}"

DEF_MEM_PER_CPU=$((NODE_MEMORY_MB / NODE_CPUS))
if [ "${DEF_MEM_PER_CPU}" -lt 1 ]; then
    DEF_MEM_PER_CPU=1
fi

cat > "${SLURM_CONF_PATH}" <<EOF
ClusterName=neurodesktop
SlurmctldHost=${NODE_HOSTNAME}

SlurmUser=slurm
AuthType=auth/munge
MpiDefault=none
ProctrackType=${PROCTRACK_TYPE}
TaskPlugin=${TASK_PLUGIN}
JobAcctGatherType=${JOBACCT_GATHER_TYPE}
AccountingStorageType=accounting_storage/none
SelectType=select/cons_tres
SelectTypeParameters=CR_Core_Memory
SchedulerType=sched/backfill
SwitchType=switch/none

SlurmctldPidFile=${SLURMCTLD_PID_FILE}
SlurmdPidFile=${SLURMD_PID_FILE}
SlurmdParameters=config_overrides
StateSaveLocation=/var/spool/slurmctld
SlurmdSpoolDir=/var/spool/slurmd

SlurmctldPort=6817
SlurmdPort=6818

SlurmctldTimeout=120
SlurmdTimeout=300
InactiveLimit=0
MinJobAge=30
KillWait=30
Waittime=0
ReturnToService=2
DefMemPerCPU=${DEF_MEM_PER_CPU}

NodeName=${NODE_HOSTNAME} NodeAddr=${NODE_ADDR} CPUs=${NODE_CPUS} RealMemory=${NODE_MEMORY_MB} State=UNKNOWN
PartitionName=${PARTITION_NAME} Nodes=${NODE_HOSTNAME} Default=YES MaxTime=INFINITE State=UP
EOF

if [ "${USE_CGROUP_MODE}" -eq 1 ]; then
    cat > "${SLURM_CGROUP_CONF_PATH}" <<EOF
CgroupPlugin=${CGROUP_PLUGIN}
CgroupMountpoint=${CGROUP_MOUNTPOINT}
IgnoreSystemd=yes
IgnoreSystemdOnFailure=yes
ConstrainCores=${CGROUP_CONSTRAIN_CORES}
ConstrainRAMSpace=${CGROUP_CONSTRAIN_RAM}
ConstrainSwapSpace=${CGROUP_CONSTRAIN_SWAP}
AllowedRAMSpace=100
AllowedSwapSpace=0
EOF
else
    cat > "${SLURM_CGROUP_CONF_PATH}" <<EOF
# Slurm non-cgroup mode: cgroup plugins are disabled via slurm.conf plugin settings.
# Keep this file minimal so slurmd does not attempt to load a cgroup plugin.
EOF
fi

if [ -d /etc/slurm-llnl ]; then
    ln -sf "${SLURM_CONF_PATH}" /etc/slurm-llnl/slurm.conf
    ln -sf "${SLURM_CGROUP_CONF_PATH}" /etc/slurm-llnl/cgroup.conf
fi

export SLURM_CONF="${SLURM_CONF_PATH}"

slurmd_log_indicates_cgroup_failure() {
    if [ ! -r /var/log/slurm/slurmd.log ]; then
        return 1
    fi

    grep -Eiq \
        "Could not create scope directory .*system\.slice|_slurmstepd\.scope|cgroup.*(read-only|permission denied|no such file)|failed to create cgroup" \
        /var/log/slurm/slurmd.log
}

slurmd_log_indicates_disabled_plugin_unsupported() {
    if [ ! -r /var/log/slurm/slurmd.log ]; then
        return 1
    fi

    grep -Eiq \
        "plugin name for disabled|cannot find cgroup plugin for disabled|cannot create cgroup context for disabled" \
        /var/log/slurm/slurmd.log
}

switch_to_non_cgroup_mode() {
    local reason
    reason="${1:-automatic fallback to non-cgroup mode}"

    USE_CGROUP_MODE=0
    CGROUP_DISABLE_REASON="${reason}"
    SLURMD_FALLBACK_REASON="${reason}"
    PROCTRACK_TYPE="proctrack/linuxproc"
    TASK_PLUGIN="task/affinity"
    JOBACCT_GATHER_TYPE="jobacct_gather/none"
    CGROUP_CONSTRAIN_CORES="no"
    CGROUP_CONSTRAIN_RAM="no"
    CGROUP_CONSTRAIN_SWAP="no"

    echo "[WARN] SLURM fallback activated: ${reason}"
    echo "[WARN] Using ProctrackType=${PROCTRACK_TYPE}, TaskPlugin=${TASK_PLUGIN}, JobAcctGatherType=${JOBACCT_GATHER_TYPE}"

    if [ -f "${SLURM_CONF_PATH}" ]; then
        sed -i \
            -e "s/^ProctrackType=.*/ProctrackType=${PROCTRACK_TYPE}/" \
            -e "s/^TaskPlugin=.*/TaskPlugin=${TASK_PLUGIN}/" \
            -e "s/^JobAcctGatherType=.*/JobAcctGatherType=${JOBACCT_GATHER_TYPE}/" \
            "${SLURM_CONF_PATH}"
    fi

    cat > "${SLURM_CGROUP_CONF_PATH}" <<EOF
# Slurm non-cgroup mode: cgroup plugins are disabled via slurm.conf plugin settings.
# Keep this file minimal so slurmd does not attempt to load a cgroup plugin.
EOF

    if pgrep -x slurmctld >/dev/null 2>&1; then
        scontrol reconfigure >/dev/null 2>&1 || true
    fi
}

switch_to_legacy_cgroup_compat_mode() {
    local reason
    reason="${1:-automatic compatibility fallback for legacy Slurm cgroup plugin behavior}"

    SLURMD_LEGACY_CGROUP_COMPAT_REASON="${reason}"
    mkdir -p "${LEGACY_CGROUP_COMPAT_MOUNTPOINT}" >/dev/null 2>&1 || true

    echo "[WARN] Legacy cgroup compatibility fallback activated: ${reason}"
    echo "[WARN] Writing cgroup.conf with CgroupPlugin=${LEGACY_CGROUP_COMPAT_PLUGIN} and CgroupMountpoint=${LEGACY_CGROUP_COMPAT_MOUNTPOINT}"

    cat > "${SLURM_CGROUP_CONF_PATH}" <<EOF
CgroupPlugin=${LEGACY_CGROUP_COMPAT_PLUGIN}
CgroupMountpoint=${LEGACY_CGROUP_COMPAT_MOUNTPOINT}
ConstrainCores=no
ConstrainRAMSpace=no
ConstrainSwapSpace=no
AllowedRAMSpace=100
AllowedSwapSpace=0
EOF

    if pgrep -x slurmctld >/dev/null 2>&1; then
        scontrol reconfigure >/dev/null 2>&1 || true
    fi
}

log_slurmd_fallback_success_if_needed() {
    if [ "${SLURMD_FALLBACK_ATTEMPTED}" -eq 1 ]; then
        echo "[INFO] slurmd started successfully using non-cgroup fallback (${SLURMD_FALLBACK_REASON:-unspecified reason})."
    fi
}

log_slurmd_legacy_cgroup_compat_success_if_needed() {
    if [ "${SLURMD_LEGACY_CGROUP_COMPAT_ATTEMPTED}" -eq 1 ]; then
        echo "[INFO] slurmd started successfully using legacy cgroup compatibility fallback (${SLURMD_LEGACY_CGROUP_COMPAT_REASON:-unspecified reason})."
    fi
}

ensure_cgroup_v2_scope_dirs() {
    if [ "${USE_CGROUP_MODE}" -ne 1 ]; then
        return 0
    fi
    if [ ! -f "${CGROUP_MOUNTPOINT}/cgroup.controllers" ]; then
        return 0
    fi

    if ! mkdir -p "${CGROUP_MOUNTPOINT}/system.slice/${NODE_HOSTNAME}_slurmstepd.scope" >/dev/null 2>&1; then
        return 1
    fi

    if [ "${NODE_HOSTNAME_FULL}" != "${NODE_HOSTNAME}" ] && \
       ! mkdir -p "${CGROUP_MOUNTPOINT}/system.slice/${NODE_HOSTNAME_FULL}_slurmstepd.scope" >/dev/null 2>&1; then
        return 1
    fi

    return 0
}

node_has_bad_state() {
    local state node_info
    node_info="$(scontrol show node "${NODE_HOSTNAME}" 2>/dev/null || true)"
    state="$(printf '%s\n' "${node_info}" | awk '
        /State=/ {
            for (i = 1; i <= NF; i++) {
                if ($i ~ /^State=/) {
                    sub(/^State=/, "", $i)
                    print $i
                    exit
                }
            }
        }'
    )"
    [[ -z "${state}" || "${state}" == *UNKNOWN* || "${state}" == *NOT_RESPONDING* ]]
}

start_slurmctld() {
    if /usr/sbin/slurmctld -f "${SLURM_CONF_PATH}" -L /var/log/slurm/slurmctld.log; then
        return 0
    fi
    echo "[ERROR] Failed to start slurmctld. Recent log output:"
    tail -n 80 /var/log/slurm/slurmctld.log || true
    return 1
}

start_slurmd() {
    if ! ensure_cgroup_v2_scope_dirs; then
        if [ "${USE_CGROUP_MODE}" -eq 1 ] && [ "${SLURMD_FALLBACK_ATTEMPTED}" -eq 0 ]; then
            SLURMD_FALLBACK_ATTEMPTED=1
            echo "[WARN] Failed to prepare cgroup scope directories; falling back to non-cgroup mode."
            switch_to_non_cgroup_mode "auto fallback after cgroup scope directory setup failure"
        else
            echo "[ERROR] Failed to prepare cgroup v2 scope directories in ${CGROUP_MOUNTPOINT}/system.slice."
            return 1
        fi
    fi

    if /usr/sbin/slurmd -N "${NODE_HOSTNAME}" -f "${SLURM_CONF_PATH}" -L /var/log/slurm/slurmd.log; then
        log_slurmd_fallback_success_if_needed
        log_slurmd_legacy_cgroup_compat_success_if_needed
        return 0
    fi

    if [ "${USE_CGROUP_MODE}" -eq 1 ] && [ "${SLURMD_FALLBACK_ATTEMPTED}" -eq 0 ] && slurmd_log_indicates_cgroup_failure; then
        SLURMD_FALLBACK_ATTEMPTED=1
        echo "[WARN] slurmd failed with cgroup errors; falling back to non-cgroup mode and retrying."
        switch_to_non_cgroup_mode "auto fallback after slurmd cgroup startup failure"
        if /usr/sbin/slurmd -N "${NODE_HOSTNAME}" -f "${SLURM_CONF_PATH}" -L /var/log/slurm/slurmd.log; then
            log_slurmd_fallback_success_if_needed
            log_slurmd_legacy_cgroup_compat_success_if_needed
            return 0
        fi
    fi

    if [ "${SLURMD_LEGACY_CGROUP_COMPAT_ATTEMPTED}" -eq 0 ] && slurmd_log_indicates_disabled_plugin_unsupported; then
        SLURMD_LEGACY_CGROUP_COMPAT_ATTEMPTED=1
        switch_to_legacy_cgroup_compat_mode "auto compatibility fallback after unsupported CgroupPlugin=disabled"
        if /usr/sbin/slurmd -N "${NODE_HOSTNAME}" -f "${SLURM_CONF_PATH}" -L /var/log/slurm/slurmd.log; then
            log_slurmd_fallback_success_if_needed
            log_slurmd_legacy_cgroup_compat_success_if_needed
            return 0
        fi
    fi

    echo "[ERROR] Failed to start slurmd. Recent log output:"
    tail -n 80 /var/log/slurm/slurmd.log || true
    return 1
}

wait_for_healthy_node() {
    local retries="${1:-10}"
    local _i
    for _i in $(seq 1 "${retries}"); do
        if scontrol ping >/dev/null 2>&1 && ! node_has_bad_state; then
            scontrol update NodeName="${NODE_HOSTNAME}" State=RESUME >/dev/null 2>&1 || true
            return 0
        fi
        sleep 1
    done
    return 1
}

if [ -f "${SLURMCTLD_PID_FILE}" ] && ! pgrep -x slurmctld >/dev/null 2>&1; then
    rm -f "${SLURMCTLD_PID_FILE}"
fi
if [ -f "${SLURMD_PID_FILE}" ] && ! pgrep -x slurmd >/dev/null 2>&1; then
    rm -f "${SLURMD_PID_FILE}"
fi

if pgrep -x slurmctld >/dev/null 2>&1; then
    scontrol reconfigure >/dev/null 2>&1 || true
else
    start_slurmctld
fi

if pgrep -x slurmd >/dev/null 2>&1; then
    if node_has_bad_state; then
        pkill -x slurmd >/dev/null 2>&1 || true
        rm -f "${SLURMD_PID_FILE}"
        sleep 1
        start_slurmd
    else
        pkill -HUP -x slurmd >/dev/null 2>&1 || true
    fi
    scontrol update NodeName="${NODE_HOSTNAME}" State=RESUME >/dev/null 2>&1 || true
else
    start_slurmd
fi

if ! wait_for_healthy_node 10; then
    echo "[WARN] Node ${NODE_HOSTNAME} not healthy yet. Forcing slurmd restart."
    pkill -x slurmd >/dev/null 2>&1 || true
    rm -f "${SLURMD_PID_FILE}"
    sleep 1
    start_slurmd
    if ! wait_for_healthy_node 10; then
        echo "[ERROR] Node ${NODE_HOSTNAME} failed to become healthy after restart."
        scontrol show node "${NODE_HOSTNAME}" || true
        tail -n 120 /var/log/slurm/slurmd.log || true
        exit 1
    fi
fi

echo "[INFO] Slurm single-node queue '${PARTITION_NAME}' ready on ${NODE_HOSTNAME} (${NODE_ADDR}) (CPUs=${NODE_CPUS}, RealMemory=${NODE_MEMORY_MB}MB, DefMemPerCPU=${DEF_MEM_PER_CPU}MB)."
