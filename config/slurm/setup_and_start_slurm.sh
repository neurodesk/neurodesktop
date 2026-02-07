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

SLURM_ETC_DIR=/etc/slurm
SLURM_CONF_PATH="${SLURM_ETC_DIR}/slurm.conf"
SLURM_CGROUP_CONF_PATH="${SLURM_ETC_DIR}/cgroup.conf"

mkdir -p "${SLURM_ETC_DIR}" /run/slurm /var/log/slurm /var/spool/slurmctld /var/spool/slurmd
mkdir -p /etc/munge /run/munge /var/log/munge

chown -R slurm:slurm /run/slurm /var/log/slurm /var/spool/slurmctld /var/spool/slurmd
chown -R munge:munge /etc/munge /run/munge /var/log/munge
chmod 0700 /etc/munge /run/munge

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

if ! pgrep -x munged >/dev/null 2>&1; then
    /usr/sbin/munged --force
fi

NODE_HOSTNAME="$(hostname -s 2>/dev/null || hostname)"
NODE_CPUS="$(detect_cpu_limit)"
NODE_MEMORY_MB="$(detect_memory_limit_mb)"
PARTITION_NAME="${NEURODESKTOP_SLURM_PARTITION:-neurodesktop}"
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
ProctrackType=proctrack/cgroup
TaskPlugin=task/cgroup,task/affinity
JobAcctGatherType=jobacct_gather/cgroup
AccountingStorageType=accounting_storage/none
SelectType=select/cons_tres
SelectTypeParameters=CR_Core_Memory
SchedulerType=sched/backfill
SwitchType=switch/none

SlurmctldPidFile=/run/slurm/slurmctld.pid
SlurmdPidFile=/run/slurm/slurmd.pid
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

NodeName=${NODE_HOSTNAME} NodeAddr=127.0.0.1 CPUs=${NODE_CPUS} RealMemory=${NODE_MEMORY_MB} State=UNKNOWN
PartitionName=${PARTITION_NAME} Nodes=${NODE_HOSTNAME} Default=YES MaxTime=INFINITE State=UP
EOF

cat > "${SLURM_CGROUP_CONF_PATH}" <<EOF
CgroupPlugin=autodetect
CgroupMountpoint=/sys/fs/cgroup
IgnoreSystemd=yes
ConstrainCores=yes
ConstrainRAMSpace=yes
ConstrainSwapSpace=yes
AllowedRAMSpace=100
AllowedSwapSpace=0
EOF

if [ -d /etc/slurm-llnl ]; then
    ln -sf "${SLURM_CONF_PATH}" /etc/slurm-llnl/slurm.conf
    ln -sf "${SLURM_CGROUP_CONF_PATH}" /etc/slurm-llnl/cgroup.conf
fi

export SLURM_CONF="${SLURM_CONF_PATH}"

if pgrep -x slurmctld >/dev/null 2>&1; then
    scontrol reconfigure >/dev/null 2>&1 || true
else
    /usr/sbin/slurmctld -f "${SLURM_CONF_PATH}" -L /var/log/slurm/slurmctld.log
fi

if pgrep -x slurmd >/dev/null 2>&1; then
    scontrol update NodeName="${NODE_HOSTNAME}" State=RESUME >/dev/null 2>&1 || true
else
    /usr/sbin/slurmd -f "${SLURM_CONF_PATH}" -L /var/log/slurm/slurmd.log
fi

for _ in $(seq 1 10); do
    if scontrol ping >/dev/null 2>&1; then
        scontrol update NodeName="${NODE_HOSTNAME}" State=RESUME >/dev/null 2>&1 || true
        break
    fi
    sleep 1
done

echo "[INFO] Slurm single-node queue '${PARTITION_NAME}' ready on ${NODE_HOSTNAME} (CPUs=${NODE_CPUS}, RealMemory=${NODE_MEMORY_MB}MB, DefMemPerCPU=${DEF_MEM_PER_CPU}MB)."
