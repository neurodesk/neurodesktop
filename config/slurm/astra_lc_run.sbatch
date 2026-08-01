#!/bin/bash
#SBATCH --job-name=astra-lc-run
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=02:00:00
#
# Materialize an ASTRA project with `lc run` inside a Slurm allocation, and
# leave machine-readable run evidence the provenance viewer can read.
#
# This is the OPTIONAL `lc` path. The default Neurodesktop flow is one
# `sbatch` script per analysis step (see /opt/AGENTS.md); that flow produces
# no manifest, so the viewer reads "Not executed". Use this template when you
# want the badge to reflect a real run.
#
# `lc run` never submits to Slurm itself: it always dispatches through Dask,
# and when SLURM_JOB_ID is set it starts an in-process scheduler and launches
# one `dask worker` per allocated node with srun. So `lc` goes *inside* an
# allocation rather than in front of one, which is what this wrapper is for.
#
# Usage:
#
#   cd /home/jovyan/my-analysis          # the directory holding astra.yaml
#   mkdir -p logs
#   NEURODESK_ASTRA_MODULES="fsl/6.0.7.22" \
#   NEURODESK_ASTRA_UNIVERSE="baseline" \
#     sbatch /opt/neurodesktop/astra_lc_run.sbatch
#
# Environment:
#
#   NEURODESK_ASTRA_PROJECT   directory holding astra.yaml (default: the
#                             directory sbatch was run from)
#   NEURODESK_ASTRA_MODULES   space-separated `module load` arguments, each
#                             with an explicit version (e.g. "fsl/6.0.7.22")
#   NEURODESK_ASTRA_UNIVERSE  universe to materialize (default: every universe)
#   NEURODESK_ASTRA_JOBS      parallel jobs passed to `lc run` (default: 1)
#
# What you get: an amber "Executed, unverified" badge. It does not go green.
# `lc verify` prints its result to the console and stamps nothing into the
# manifest, and Neurodesktop does not synthesize a verification record it did
# not earn. See docs/architecture/astra.md.

set -euo pipefail

: "${SLURM_JOB_ID:?This template must be submitted with sbatch, not run directly}"

project="${NEURODESK_ASTRA_PROJECT:-${SLURM_SUBMIT_DIR:-${PWD}}}"
universe="${NEURODESK_ASTRA_UNIVERSE:-}"
jobs="${NEURODESK_ASTRA_JOBS:-1}"

# environment_variables.sh is written for an interactive shell and references
# variables this script has not set.
set +u
# shellcheck disable=SC1091
source /opt/neurodesktop/environment_variables.sh
set -u

# shellcheck disable=SC1091
source /usr/share/lmod/lmod/init/bash
module purge
for spec in ${NEURODESK_ASTRA_MODULES:-}; do
    case "${spec}" in
        */*) ;;
        *)
            echo "Refusing to load '${spec}' without an explicit version." >&2
            echo "Use tool/version, e.g. fsl/6.0.7.22." >&2
            exit 2
            ;;
    esac
    module load "${spec}"
done

# `lc` shells out to the `dask` CLI to start its srun workers, and both live in
# the isolated uv tool environment rather than on the default PATH. Without
# this, `lc run` fails inside the allocation complaining that `dask` is missing.
export PATH="/opt/uv/tools/lightcone-cli/bin:${PATH}"
for required in lc dask astra; do
    command -v "${required}" >/dev/null || {
        echo "'${required}' is not on PATH inside the allocation." >&2
        exit 2
    }
done

cd "${project}"
test -f astra.yaml || {
    echo "No astra.yaml in ${project}." >&2
    exit 2
}

# Neurodesktop has no container runtime `lc` supports: Apptainer is not one of
# docker/podman/podman-hpc/kubernetes, so `lc` resolves runtime `none` and runs
# recipes on the host. That is honest for a `module load` analysis and harmless
# for a spec with no image, but a spec that DOES declare a container would have
# its declared image recorded in the manifest without ever running -- the
# provenance mismatch the viewer paints red. Refuse instead of recording it.
/opt/conda/bin/python - "${PWD}/astra.yaml" <<'PY'
import sys
import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    spec = yaml.safe_load(handle) or {}

declared = []

def walk(node, trail):
    if not isinstance(node, dict):
        return
    if node.get("container"):
        declared.append(".".join(trail) or "<root>")
    for output in node.get("outputs") or []:
        if isinstance(output, dict) and (output.get("recipe") or {}).get("container"):
            declared.append(".".join(trail + [str(output.get("id"))]))
    for key, child in (node.get("analyses") or {}).items():
        walk(child, trail + [str(key)])

walk(spec, [])
if declared:
    sys.exit(
        "This spec declares a container at: "
        + ", ".join(declared)
        + "\nNeurodesktop has no container runtime lc supports (Apptainer is "
        "not docker/podman/podman-hpc/kubernetes), so the image would be "
        "recorded as used without ever running. Drop the container and pin "
        "tools with `module load` instead."
    )
PY

astra validate astra.yaml

# Writing status.json beside a manifest the viewer also recognises would make
# the directory ambiguous, and it fails closed rather than guessing -- which
# would blank the graph instead of showing this run.
for other in run-manifest.json manifest.json ro-crate-metadata.json; do
    test -e "${other}" && {
        echo "Refusing to write status.json: ${project}/${other} already exists," >&2
        echo "and two run-evidence files beside one spec cannot be read" >&2
        echo "unambiguously. Remove the stale one first." >&2
        exit 2
    }
done

echo "→ lc run (universe=${universe:-all}, jobs=${jobs}) in ${project}"
if [ -n "${universe}" ]; then
    lc run --universe "${universe}" --jobs "${jobs}"
    lc status --universe "${universe}" --json > status.json.tmp
else
    lc run --jobs "${jobs}"
    lc status --json > status.json.tmp
fi

# Rename last: a half-written status.json is run evidence the viewer would
# refuse, and refusing takes the whole graph down with it.
mv status.json.tmp status.json
echo "→ wrote ${project}/status.json"
echo "  Open astra.yaml in JupyterLab and press Refresh to pick it up."
