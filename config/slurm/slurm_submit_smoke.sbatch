#!/bin/bash
#SBATCH --job-name=nd-slurm-smoke
#SBATCH --output=/tmp/nd-slurm-smoke-%j.out
#SBATCH --error=/tmp/nd-slurm-smoke-%j.err
#SBATCH --time=00:02:00
#SBATCH --partition=neurodesktop
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=1
#SBATCH --mem=512M

set -euo pipefail

echo "[INFO] Starting Slurm submit smoke test"
echo "[INFO] job_id=${SLURM_JOB_ID} partition=${SLURM_JOB_PARTITION} node_list=${SLURM_JOB_NODELIST}"
echo "[INFO] submit_host=${SLURM_SUBMIT_HOST} submit_dir=${SLURM_SUBMIT_DIR}"

expected_host="$(scontrol show hostname "${SLURM_JOB_NODELIST}" | head -n1)"
echo "[INFO] expected_host=${expected_host}"

rank_lines="$(srun --ntasks=2 bash -lc 'echo "rank=${SLURM_PROCID} host=$(hostname)"' | sort)"
echo "${rank_lines}"

if ! printf '%s\n' "${rank_lines}" | grep -q 'rank=0 '; then
    echo "[FAIL] Missing rank=0 output."
    exit 1
fi
if ! printf '%s\n' "${rank_lines}" | grep -q 'rank=1 '; then
    echo "[FAIL] Missing rank=1 output."
    exit 1
fi
if ! printf '%s\n' "${rank_lines}" | grep -q "host=${expected_host}"; then
    echo "[FAIL] srun tasks did not run on expected host ${expected_host}."
    exit 1
fi

single_step_output="$(srun --ntasks=1 bash -lc 'echo "single_step_host=$(hostname) cpus=$(nproc)"')"
echo "${single_step_output}"

echo "[PASS] Slurm submit smoke test finished successfully."
