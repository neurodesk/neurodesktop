import subprocess
import os
import signal
import pytest
import sys

_ENV_PREAMBLE = (
    "source /opt/neurodesktop/environment_variables.sh 2>/dev/null; "
    "source /usr/share/lmod/lmod/init/bash 2>/dev/null; "
)

def run_cmd(cmd, timeout=180):
    """Run a shell command and return (exit_code, output). Kills process group on timeout."""
    process = subprocess.Popen(
        cmd,
        shell=True,
        executable="/bin/bash",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
        return process.returncode, stdout.strip()
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise

def test_nipype_importable():
    """Verify nipype can be imported and successfully prints its version."""
    code, output = run_cmd(
        f'"{sys.executable}" -c "import nipype; print(nipype.__version__)"'
    )
    assert code == 0, f"Failed to import nipype: {output}"
    assert len(output) > 0, "No version output string found"

def test_nipype_fslmaths(tmp_path):
    """Verify we can build a simple FSLMaths command via nipype."""
    cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
    if cvmfs_disable in ["true", "1"]:
        pytest.skip("CVMFS is disabled")

    # Load FSL in the full neurodesk environment and capture env vars
    code, output = run_cmd(
        _ENV_PREAMBLE +
        'module load fsl 2>/dev/null; '
        'env',
        timeout=60,
    )
    if code != 0:
        pytest.fail("Failed to source neurodesk environment and load FSL module")

    for line in output.splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            if key in ("PATH", "LD_LIBRARY_PATH", "MODULEPATH",
                       "FSLDIR", "FSLOUTPUTTYPE", "neurodesk_singularity_opts",
                       "APPTAINER_BINDPATH"):
                os.environ[key] = val

    code, _ = run_cmd("command -v fslmaths")
    if code != 0:
        pytest.fail(
            "fslmaths not in PATH after module load — CVMFS is enabled but "
            "FSL module failed to load. Startup scripts may have failed."
        )

    import nipype.interfaces.fsl as fsl
    maths = fsl.ImageMaths()
    dummy_in = tmp_path / "dummy.nii.gz"
    dummy_in.touch()

    maths.inputs.in_file = str(dummy_in)
    maths.inputs.op_string = "-add 0"
    maths.inputs.out_file = str(tmp_path / "dummy_maths.nii.gz")
    cmdline = maths.cmdline

    assert "fslmaths" in cmdline, f"FSLMaths command not generated properly: {cmdline}"
    assert "dummy.nii.gz" in cmdline, f"Input file not in command line: {cmdline}"
    assert "-add 0" in cmdline, f"Math operation not in command line: {cmdline}"


def test_nipype_nonexistent_module_fails():
    """Verify that loading a non-existent module fails."""
    cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
    if cvmfs_disable in ["true", "1"]:
        pytest.skip("CVMFS is disabled")

    code, output = run_cmd(
        _ENV_PREAMBLE + 'module load funny-name-tool',
        timeout=60,
    )
    assert code != 0, (
        f"Loading non-existent module should have failed: {output}"
    )
