#!/bin/bash
#SBATCH --job-name=astra-lightcone-bet
#SBATCH --partition=neurodesktop
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4096M
#SBATCH --time=00:15:00

set -euo pipefail

: "${NEURODESKTOP_PILOT_WORKSPACE:?NEURODESKTOP_PILOT_WORKSPACE is required}"
: "${NEURODESKTOP_PILOT_RUN_ID:?NEURODESKTOP_PILOT_RUN_ID is required}"
: "${SLURM_JOB_ID:?This script must run inside a Slurm allocation}"

set +u
source /opt/neurodesktop/environment_variables.sh
set -u
source /usr/share/lmod/lmod/init/bash
module purge
module load fsl/6.0.7.22

PILOT_RUN_DIR="${NEURODESKTOP_PILOT_WORKSPACE}/runs/${NEURODESKTOP_PILOT_RUN_ID}"
PILOT_PROJECT_DIR="${PILOT_RUN_DIR}/project"
PILOT_LIGHTCONE_HOME="${PILOT_RUN_DIR}/lightcone-home"
PILOT_EXPORT_DIR="${NEURODESKTOP_PILOT_WORKSPACE}/exports/${NEURODESKTOP_PILOT_RUN_ID}/ro-crate"
mkdir -p "${PILOT_LIGHTCONE_HOME}/.lightcone" "${PILOT_RUN_DIR}/lightcone"
printf 'container:\n  runtime: none\n' > "${PILOT_LIGHTCONE_HOME}/.lightcone/config.yaml"
export HOME="${PILOT_LIGHTCONE_HOME}"
export PATH="/opt/uv/tools/lightcone-cli/bin:${PATH}"

/opt/conda/bin/python \
    "${NEURODESKTOP_PILOT_WORKSPACE}/project/src/collect-pilot-evidence.py" \
    capture-module \
    --workspace "${NEURODESKTOP_PILOT_WORKSPACE}" \
    --run-directory "${PILOT_RUN_DIR}"

cd "${PILOT_PROJECT_DIR}"
astra validate astra.yaml
lc run --jobs 1

lc status --json > "${PILOT_RUN_DIR}/lightcone/status.json.tmp"
mv "${PILOT_RUN_DIR}/lightcone/status.json.tmp" \
    "${PILOT_RUN_DIR}/lightcone/status.json"
lc verify | tee "${PILOT_RUN_DIR}/lightcone/verify.log"
lc export wrroc --output "${PILOT_EXPORT_DIR}"
/opt/uv/tools/lightcone-cli/bin/python \
    src/collect-pilot-evidence.py collect-outputs \
    --workspace "${NEURODESKTOP_PILOT_WORKSPACE}" \
    --run-directory "${PILOT_RUN_DIR}"
