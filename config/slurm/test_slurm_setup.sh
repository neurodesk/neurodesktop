#!/bin/bash
set -euo pipefail

PARTITION_NAME="${NEURODESKTOP_SLURM_PARTITION:-neurodesktop}"
NODE_HOSTNAME="$(hostname -s 2>/dev/null || hostname)"
FAILURES=0
CMD_FAILURES=0

pass() {
    echo "[PASS] $1"
}

fail() {
    echo "[FAIL] $1"
    FAILURES=$((FAILURES + 1))
}

print_cgroup_hint_if_needed() {
    if [ -r /var/log/slurm/slurmd.log ] && grep -q "Could not create scope directory .*system.slice" /var/log/slurm/slurmd.log; then
        echo "[INFO] Detected cgroup/systemd scope error in slurmd."
        echo "[INFO] Try creating the scope directory and restarting slurmd:"
        echo "[INFO]   sudo mkdir -p /sys/fs/cgroup/system.slice/\$(hostname)_slurmstepd.scope"
        echo "[INFO]   sudo slurmd"
        echo "[INFO] Use non-cgroup Slurm mode: NEURODESKTOP_SLURM_USE_CGROUP=0"
    fi
}

require_cmd() {
    local cmd="$1"
    if command -v "${cmd}" >/dev/null 2>&1; then
        pass "Found command '${cmd}'."
    else
        fail "Missing command '${cmd}'."
        CMD_FAILURES=$((CMD_FAILURES + 1))
    fi
}

if [ "${1:-}" = "--bootstrap" ]; then
    if [ -x /opt/neurodesktop/setup_and_start_slurm.sh ]; then
        if [ "${EUID}" -eq 0 ]; then
            if /opt/neurodesktop/setup_and_start_slurm.sh; then
                pass "Ran Slurm bootstrap as root."
            else
                fail "Slurm bootstrap failed when running as root."
            fi
        elif command -v sudo >/dev/null 2>&1; then
            if sudo -n true >/dev/null 2>&1; then
                if sudo -n /opt/neurodesktop/setup_and_start_slurm.sh; then
                    pass "Ran Slurm bootstrap via passwordless sudo."
                else
                    fail "Slurm bootstrap failed via passwordless sudo."
                fi
            elif [ -t 0 ] && [ -t 1 ]; then
                echo "[INFO] sudo requires a password to run bootstrap..."
                if sudo /opt/neurodesktop/setup_and_start_slurm.sh; then
                    pass "Ran Slurm bootstrap via sudo."
                else
                    fail "Slurm bootstrap failed via sudo."
                fi
            else
                fail "Passwordless sudo not available in non-interactive shell."
            fi
        else
            fail "Cannot run bootstrap (not root and no sudo available)."
        fi
    else
        fail "Bootstrap script not found at /opt/neurodesktop/setup_and_start_slurm.sh."
    fi
fi

require_cmd munge
require_cmd scontrol
require_cmd sinfo
require_cmd srun

if [ "${CMD_FAILURES}" -gt 0 ]; then
    echo "[FAIL] Required commands are missing."
    exit 1
fi

if [ -S /run/munge/munge.socket.2 ]; then
    pass "MUNGE socket exists at /run/munge/munge.socket.2."
else
    fail "MUNGE socket missing at /run/munge/munge.socket.2."
fi

if munge -n >/dev/null 2>&1; then
    pass "MUNGE credential generation works for current user."
else
    fail "MUNGE credential generation failed for current user."
fi

if scontrol ping >/dev/null 2>&1; then
    pass "slurmctld is reachable."
else
    fail "slurmctld ping failed."
fi

NODE_INFO="$(scontrol show node "${NODE_HOSTNAME}" 2>&1 || true)"
NODE_STATE="$(printf '%s\n' "${NODE_INFO}" | sed -n 's/.*State=\([^ ]*\).*/\1/p' | head -n1)"
NODE_REASON="$(printf '%s\n' "${NODE_INFO}" | sed -n 's/.*Reason=\([^ ]*\).*/\1/p' | head -n1)"

if [ -z "${NODE_STATE}" ]; then
    fail "Could not read node state for ${NODE_HOSTNAME}."
else
    if [[ "${NODE_STATE}" =~ (UNKNOWN|NOT_RESPONDING|DOWN|DRAIN|FAIL) ]]; then
        fail "Node ${NODE_HOSTNAME} unhealthy: State=${NODE_STATE} Reason=${NODE_REASON:-N/A}."
    else
        pass "Node ${NODE_HOSTNAME} state is ${NODE_STATE}."
    fi
fi

PARTITION_INFO="$(sinfo -h -p "${PARTITION_NAME}" -o '%P %a %T %D' 2>&1 || true)"
if [ -n "${PARTITION_INFO}" ] && ! printf '%s\n' "${PARTITION_INFO}" | grep -qi 'error'; then
    pass "Partition '${PARTITION_NAME}' visible: ${PARTITION_INFO}"
else
    fail "Partition '${PARTITION_NAME}' not healthy or not visible: ${PARTITION_INFO}"
fi

if SRUN_OUTPUT="$(srun -I20 -N1 -n1 -p "${PARTITION_NAME}" /bin/hostname 2>&1)"; then
    if printf '%s\n' "${SRUN_OUTPUT}" | grep -qx "${NODE_HOSTNAME}"; then
        pass "srun executed on ${NODE_HOSTNAME}."
    else
        pass "srun executed successfully: ${SRUN_OUTPUT}"
    fi
else
    fail "srun smoke test failed: ${SRUN_OUTPUT}"
fi

if [ "${FAILURES}" -eq 0 ]; then
    echo "[PASS] Slurm smoke test passed."
    exit 0
fi

print_cgroup_hint_if_needed
echo "[FAIL] Slurm smoke test failed with ${FAILURES} issue(s)."
exit 1
