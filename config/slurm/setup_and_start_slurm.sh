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
NODE_ADDR="$(detect_node_addr "${NODE_HOSTNAME}")"
NODE_CPUS="$(detect_cpu_limit)"
NODE_MEMORY_MB="$(detect_memory_limit_mb)"
PARTITION_NAME="${NEURODESKTOP_SLURM_PARTITION:-neurodesktop}"
USE_CGROUP_MODE=1

if is_false "${NEURODESKTOP_SLURM_USE_CGROUP:-auto}"; then
    USE_CGROUP_MODE=0
elif [ "${NEURODESKTOP_SLURM_USE_CGROUP:-auto}" = "auto" ]; then
    # In many containers without systemd, slurmd cannot create system.slice scopes.
    if [ ! -d /sys/fs/cgroup/system.slice ] || [ ! -w /sys/fs/cgroup/system.slice ]; then
        USE_CGROUP_MODE=0
    fi
fi

if [ "${USE_CGROUP_MODE}" -eq 1 ]; then
    PROCTRACK_TYPE="proctrack/cgroup"
    TASK_PLUGIN="task/cgroup,task/affinity"
    JOBACCT_GATHER_TYPE="jobacct_gather/cgroup"
    CGROUP_CONSTRAIN_CORES="yes"
    CGROUP_CONSTRAIN_RAM="yes"
    CGROUP_CONSTRAIN_SWAP="yes"
    echo "[INFO] Slurm cgroup mode enabled."
else
    PROCTRACK_TYPE="proctrack/linuxproc"
    TASK_PLUGIN="task/none"
    JOBACCT_GATHER_TYPE="jobacct_gather/none"
    CGROUP_CONSTRAIN_CORES="no"
    CGROUP_CONSTRAIN_RAM="no"
    CGROUP_CONSTRAIN_SWAP="no"
    echo "[INFO] Slurm cgroup mode disabled (container cgroup layout is not compatible)."
fi

# Some container runtimes expose cgroup v2 without systemd/dbus.
# Slurm cgroup/v2 expects this parent path when IgnoreSystemd=yes.
# Creating it preempts scope creation failures in containers without systemd.
if [ ! -d /sys/fs/cgroup/system.slice ]; then
    if [ -d /sys/fs/cgroup ] && [ -w /sys/fs/cgroup ]; then
        mkdir -p /sys/fs/cgroup/system.slice || true
    fi
fi

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

cat > "${SLURM_CGROUP_CONF_PATH}" <<EOF
CgroupPlugin=autodetect
CgroupMountpoint=/sys/fs/cgroup
IgnoreSystemd=yes
IgnoreSystemdOnFailure=yes
ConstrainCores=${CGROUP_CONSTRAIN_CORES}
ConstrainRAMSpace=${CGROUP_CONSTRAIN_RAM}
ConstrainSwapSpace=${CGROUP_CONSTRAIN_SWAP}
AllowedRAMSpace=100
AllowedSwapSpace=0
EOF

if [ -d /etc/slurm-llnl ]; then
    ln -sf "${SLURM_CONF_PATH}" /etc/slurm-llnl/slurm.conf
    ln -sf "${SLURM_CGROUP_CONF_PATH}" /etc/slurm-llnl/cgroup.conf
fi

export SLURM_CONF="${SLURM_CONF_PATH}"

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
    if /usr/sbin/slurmd -N "${NODE_HOSTNAME}" -f "${SLURM_CONF_PATH}" -L /var/log/slurm/slurmd.log; then
        return 0
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
