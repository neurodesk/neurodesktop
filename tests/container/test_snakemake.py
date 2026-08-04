import subprocess
import signal
import pytest
import os
import tempfile

_ENV_PREAMBLE = (
    "source /opt/neurodesktop/environment_variables.sh 2>/dev/null; "
    "source /usr/share/lmod/lmod/init/bash 2>/dev/null; "
)

def run_cmd(cmd, timeout=180):
    """Run a shell command and return (exit_code, output). Kills process group on timeout."""
    process = subprocess.Popen(
        cmd, shell=True, executable='/bin/bash',
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
        return process.returncode, stdout.strip()
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise

def _fsl_available():
    """Check if FSL module loads and fslmaths is on PATH (30s timeout)."""
    try:
        code, _ = run_cmd(
            _ENV_PREAMBLE +
            "module load fsl 2>/dev/null; command -v fslmaths",
            timeout=120,
        )
        return code == 0
    except subprocess.TimeoutExpired:
        return False

def test_snakemake_version():
    """Verify snakemake is installed and functioning."""
    cmd = f"snakemake --version"
    code, output = run_cmd(cmd)
    assert code == 0, f"Snakemake version check failed: {output}"
    assert output and len(output.split(".")) >= 2, f"Unexpected Snakemake output: {output}"

def test_snakemake_fslmaths():
    """Verify snakemake can run a workflow using fslmaths."""
    cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
    if cvmfs_disable in ["true", "1"]:
        pytest.skip("CVMFS is disabled (CVMFS_DISABLE=true)")
    if not os.path.isdir("/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules"):
        pytest.fail("CVMFS is enabled but neurodesk-modules not mounted — startup scripts failed")
    if not _fsl_available():
        pytest.fail("module load fsl failed — fslmaths not available")
    with tempfile.TemporaryDirectory() as tmpdir:
        snakefile_content = """
rule all:
    input:
        "output.nii.gz"

rule run_fslmaths:
    output:
        "output.nii.gz"
    shell:
        \"\"\"
        set +euo pipefail
        source /opt/neurodesktop/environment_variables.sh 2>/dev/null || true
        source /usr/share/lmod/lmod/init/bash 2>/dev/null || true
        module load fsl 2>&1 || true
        if ! command -v fslmaths >/dev/null 2>&1; then
            echo "fslmaths not found in PATH"
            echo "MODULEPATH=$MODULEPATH"
            exit 1
        fi
        touch {output}
        \"\"\"
"""
        snakefile_path = os.path.join(tmpdir, "Snakefile")
        with open(snakefile_path, "w") as f:
            f.write(snakefile_content)

        cmd = f"cd {tmpdir} && snakemake --cores 1"
        code, output = run_cmd(cmd)

        assert code == 0, f"Snakemake FSLMaths workflow failed: {output}"
        assert os.path.exists(os.path.join(tmpdir, "output.nii.gz")), "Snakemake did not produce expected output"
