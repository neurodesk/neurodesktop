import subprocess
import os
import signal
import pytest
import sys

LMOD_INIT = "/usr/share/lmod/lmod/init/bash"

_ENV_PREAMBLE = (
    "source /opt/neurodesktop/environment_variables.sh && "
    f"source {LMOD_INIT} && "
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

def test_nipype_fslmaths(tmp_path, image_math_case):
    """Execute Nipype through the same Lmod environment as a notebook user."""
    if os.environ.get("CVMFS_DISABLE", "false").lower() in {"true", "1"}:
        pytest.skip("CVMFS is disabled")
    source, check_output = image_math_case
    output_image = tmp_path / "output.nii.gz"
    script = tmp_path / "run_maths.py"
    script.write_text(
        "from nipype.interfaces.fsl import ImageMaths\n"
        f"ImageMaths(in_file={str(source)!r}, op_string='-mul 2', "
        f"out_file={str(output_image)!r}).run()\n"
    )
    code, output = run_cmd(
        _ENV_PREAMBLE + f'module load fsl && "{sys.executable}" "{script}"',
        timeout=600,
    )
    assert code == 0, output
    check_output(output_image)


def test_nipype_nonexistent_module_fails(tmp_path, image_math_case):
    if os.environ.get("CVMFS_DISABLE", "false").lower() in {"true", "1"}:
        pytest.skip("CVMFS is disabled")
    source, _ = image_math_case
    # Prove imports, Lmod and the real input work before testing a failed load.
    code, output = run_cmd(
        _ENV_PREAMBLE + 'type module && module load fsl && command -v fslmaths',
        timeout=300,
    )
    assert code == 0, output
    code, output = run_cmd(f'"{sys.executable}" -c "import nipype.interfaces.fsl"')
    assert code == 0, output
    output_image = tmp_path / "should-not-exist.nii.gz"
    entered = tmp_path / "interface-entered"
    script = tmp_path / "run_maths.py"
    script.write_text(
        "from pathlib import Path\n"
        "from nipype.interfaces.fsl import ImageMaths\n"
        f"Path({str(entered)!r}).touch()\n"
        f"ImageMaths(in_file={str(source)!r}, op_string='-mul 2', "
        f"out_file={str(output_image)!r}).run()\n"
    )
    code, output = run_cmd(
        _ENV_PREAMBLE + 'module load funny-name-tool && '
        + f'"{sys.executable}" "{script}"',
        timeout=300,
    )
    assert code != 0, output
    assert "funny-name-tool" in output, output
    assert not entered.exists(), "A failed module load must stop before Nipype executes"
    assert not output_image.exists()
