import subprocess
import os
import pytest

def run_cmd(cmd, timeout=180):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        timeout=timeout,
    )
    return process.returncode, process.stdout.strip()

def test_nextflow_version():
    """Verify nextflow is installed and functioning."""
    code, output = run_cmd("nextflow -version")
    assert code == 0, f"Nextflow version check failed: {output}"
    assert "n e x t f l o w" in output.lower() or "nextflow" in output.lower(), f"Unexpected Nextflow output: {output}"

def test_nf_core_version():
    """Verify nf-core is installed and functioning."""
    code, output = run_cmd("nf-core --version")
    assert code == 0, f"nf-core version check failed: {output}"
    assert "nf-core" in output.lower(), f"Unexpected nf-core output: {output}"

def test_nf_test_version():
    """Verify nf-test is installed and functioning."""
    code, output = run_cmd("nf-test --version")
    if code != 0:
        code, output = run_cmd("nf-test version")
        
    assert code == 0, f"nf-test version check failed: {output}"

def test_nf_neuro_modules():
    """Verify nf-neuro modules are present."""
    modules_dir = os.environ.get("NF_NEURO_MODULES_DIR", "/opt/nf-neuro/modules")
    is_valid = os.path.exists(os.path.join(modules_dir, ".git")) or os.path.exists(os.path.join(modules_dir, "README.md"))
    assert is_valid, f"nf-neuro modules checkout not found at {modules_dir}"


def test_nextflow_fslmaths(tmp_path):
    """Verify nextflow can run a minimal workflow using fslmaths."""
    cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
    if cvmfs_disable in ["true", "1"]:
        pytest.skip("CVMFS is disabled (CVMFS_DISABLE=true)")
    if not os.path.isdir("/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules"):
        pytest.fail("CVMFS is enabled but neurodesk-modules not mounted — startup scripts failed")
    workflow = """
process RUN_FSLMATHS {
    publishDir 'results', mode: 'copy'
    output:
    path 'output.nii.gz'
    script:
    '''
    set +euo pipefail
    source /opt/neurodesktop/environment_variables.sh 2>/dev/null || true
    source /usr/share/lmod/lmod/init/bash 2>/dev/null || true
    export MODULEPATH=/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/all:/opt/neurocommand/local/containers/modules/all:${MODULEPATH:-}
    module load fsl >/dev/null 2>&1 || true
    if ! command -v fslmaths >/dev/null 2>&1; then
        echo "fslmaths not found in PATH"
        exit 1
    fi
    touch output.nii.gz
    '''
}

workflow {
    RUN_FSLMATHS()
}
"""
    workflow_file = tmp_path / "main.nf"
    workflow_file.write_text(workflow)

    cmd = f"cd {tmp_path} && nextflow run main.nf -ansi-log false"
    code, output = run_cmd(cmd)

    assert code == 0, f"Nextflow FSLMaths workflow failed: {output}"
    assert (tmp_path / "results" / "output.nii.gz").exists(), "Nextflow did not produce expected output"
