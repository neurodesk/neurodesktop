import subprocess
import os
import signal
import pytest
import sys

LMOD_INIT = "/usr/share/lmod/lmod/init/bash"

_ENV_PREAMBLE = (
    "source /opt/neurodesktop/environment_variables.sh 2>/dev/null; "
    f"source {LMOD_INIT} 2>/dev/null; "
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

def test_nipype_fslmaths(tmp_path, monkeypatch):
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
                # Restore the process environment after this test. Leaking the
                # FSL module's PATH makes later tests invoke FSL's Python rather
                # than the image's Python.
                monkeypatch.setenv(key, val)

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


def test_nipype_nonexistent_module_fails(tmp_path):
    """A failed module load must fail the nipype run and produce no output.

    Asserting only on a non-zero exit status is not enough: `module load` also
    exits non-zero when Lmod is missing entirely, so such a test passes on a
    machine with no CVMFS, no Lmod and no nipype at all. Assert the Lmod
    initialiser is present, then require that the interface run itself fails
    and leaves no output behind - the same shape as the nextflow negative test.
    """
    cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
    if cvmfs_disable in ["true", "1"]:
        pytest.skip("CVMFS is disabled")

    assert os.path.isfile(LMOD_INIT), (
        f"{LMOD_INIT} is missing - `module load` would fail for the wrong reason"
    )

    dummy_in = tmp_path / "dummy.nii.gz"
    dummy_in.touch()
    out_file = tmp_path / "dummy_maths.nii.gz"

    script = (
        "import nipype.interfaces.fsl as fsl; "
        "maths = fsl.ImageMaths(); "
        f'maths.inputs.in_file = "{dummy_in}"; '
        'maths.inputs.op_string = "-add 0"; '
        f'maths.inputs.out_file = "{out_file}"; '
        "maths.run()"
    )
    code, output = run_cmd(
        _ENV_PREAMBLE
        + "module load funny-name-tool; "
        + f'"{sys.executable}" -c \'{script}\'',
        timeout=180,
    )

    assert code != 0, (
        f"nipype run should have failed after a bogus module load: {output}"
    )
    assert not out_file.exists(), (
        "nipype produced an output file even though the module load failed"
    )
