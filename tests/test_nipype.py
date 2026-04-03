import subprocess
import os
import pytest
import sys

def run_cmd(cmd, timeout=180):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd,
        shell=True,
        executable="/bin/bash",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return process.returncode, process.stdout.strip()

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

    # Load FSL by sourcing the environment setup (handles MODULEPATH glob expansion)
    code, output = run_cmd(
        'source /opt/neurodesktop/environment_variables.sh 2>/dev/null; '
        'source /usr/share/lmod/lmod/init/bash 2>/dev/null; '
        'module load fsl 2>/dev/null; '
        'env'
    )
    if code == 0:
        for line in output.splitlines():
            if "=" in line:
                key, _, val = line.partition("=")
                if key in ("PATH", "LD_LIBRARY_PATH", "MODULEPATH",
                           "FSLDIR", "FSLOUTPUTTYPE"):
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
