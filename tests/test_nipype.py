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
    # Attempt to load FSL using the python lmod package
    try:
        import importlib.util
        if importlib.util.find_spec("lmod"):
            import lmod
            if hasattr(lmod, 'module'):
                lmod.module('load', 'fsl')
            elif hasattr(lmod, 'load'):
                # Handle async style jupyterlmod if available synchronously
                pass
        else:
            # Fallback to the system Lmod python wrapper
            import sys
            import os
            # Provide MODULEPATH for offline/test environments if not set
            if not os.environ.get("MODULEPATH"):
                os.environ["MODULEPATH"] = "/neurodesktop-storage/containers/modules/:/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/"
            lmod_init = "/usr/share/lmod/lmod/init"
            if lmod_init not in sys.path:
                sys.path.insert(0, lmod_init)
            import env_modules_python as lmod
            lmod.module('use', '/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/')
            lmod.module('load', 'fsl')
    except Exception as e:
        print(f"Warning: could not load fsl via python lmod: {e}")

    code, _ = run_cmd("command -v fslmaths")
    if code != 0:
        cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
        if cvmfs_disable in ["true", "1"]:
            pytest.skip("fslmaths not available — CVMFS is disabled")
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
