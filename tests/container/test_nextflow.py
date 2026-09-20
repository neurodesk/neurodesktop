import subprocess
import signal
import os
import pytest

_ENV_PREAMBLE = (
    "source /opt/neurodesktop/environment_variables.sh && "
    "source /usr/share/lmod/lmod/init/bash && "
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
            "module load fsl && command -v fslmaths",
            timeout=120,
        )
        return code == 0
    except subprocess.TimeoutExpired:
        return False

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


def test_nextflow_fslmaths(tmp_path, image_math_case):
    """Verify nextflow can run a minimal workflow using fslmaths."""
    source, check_output = image_math_case
    cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
    if cvmfs_disable in ["true", "1"]:
        pytest.skip("CVMFS is disabled (CVMFS_DISABLE=true)")
    if not os.path.isdir("/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules"):
        pytest.fail("CVMFS is enabled but neurodesk-modules not mounted — startup scripts failed")
    if not _fsl_available():
        pytest.fail("module load fsl failed — fslmaths not available")
    workflow = """
process RUN_FSLMATHS {
    publishDir 'results', mode: 'copy'
    input:
    path 'input.nii.gz'
    output:
    path 'output.nii.gz'
    script:
    '''
    set +u
    set -eo pipefail
    source /opt/neurodesktop/environment_variables.sh
    source /usr/share/lmod/lmod/init/bash
    module load fsl
    if ! command -v fslmaths >/dev/null 2>&1; then
        echo "fslmaths not found in PATH"
        echo "MODULEPATH=$MODULEPATH"
        exit 1
    fi
    fslmaths input.nii.gz -mul 2 output.nii.gz
    '''
}

workflow {
    RUN_FSLMATHS(file(params.input))
}
"""
    workflow_file = tmp_path / "main.nf"
    workflow_file.write_text(workflow)

    cmd = f"cd {tmp_path} && nextflow run main.nf -ansi-log false --input '{source}'"
    code, output = run_cmd(cmd, timeout=600)

    assert code == 0, f"Nextflow FSLMaths workflow failed: {output}"
    check_output(tmp_path / "results" / "output.nii.gz")


def test_nextflow_nonexistent_module_fails(tmp_path):
    """Verify nextflow workflow fails when loading a non-existent module."""
    cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
    if cvmfs_disable in ["true", "1"]:
        pytest.skip("CVMFS is disabled (CVMFS_DISABLE=true)")
    if not os.path.isdir("/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules"):
        pytest.fail("CVMFS is enabled but neurodesk-modules not mounted")

    assert _fsl_available(), "Working Lmod/FSL prerequisites required"
    code, output = run_cmd("nextflow -version")
    assert code == 0, output

    workflow = """
process RUN_FSLMATHS {
    publishDir 'results', mode: 'copy'
    output:
    path 'output.nii.gz'
    script:
    '''
    set +u
    set -eo pipefail
    source /opt/neurodesktop/environment_variables.sh
    source /usr/share/lmod/lmod/init/bash
    if module load funny-name-tool; then
        touch output.nii.gz
    else
        echo EXPECTED_MISSING_MODULE:funny-name-tool >&2
        exit 42
    fi
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

    assert code != 0, (
        f"Workflow should have failed with non-existent module but succeeded: {output}"
    )
    task_errors = list(tmp_path.glob("work/*/*/.command.err"))
    assert len(task_errors) == 1, output
    assert "EXPECTED_MISSING_MODULE:funny-name-tool" in task_errors[0].read_text(), output
    assert not (tmp_path / "results" / "output.nii.gz").exists(), (
        "Output should not exist when module load fails"
    )
