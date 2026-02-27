import subprocess
import pytest

def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return process.returncode, process.stdout.strip()

def test_nipype_importable():
    """Verify nipype can be imported and successfully prints its version."""
    code, output = run_cmd("python -c \"import nipype; print(nipype.__version__)\"")
    assert code == 0, f"Failed to import nipype: {output}"
    assert len(output) > 0, "No version output string found"

def test_nipype_fsl_bet(tmp_path):
    """Verify we can run a simple FSL BET command via nipype."""
    # Create a minimal valid-ish NIfTI file or just an empty file if that's enough to trigger BET
    # Actually, BET requires a valid NIfTI header. To avoid uploading a binary, we can test 
    # if the fsl.BET() node can be imported from nipype and built into a command successfully.
    # It might fail if FSL is missing from path. We can test just the command line generation
    # or actually running it if FSL is installed.
    # 
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

    code, _ = run_cmd("command -v bet")
    assert code == 0, "FSL 'bet' command not in PATH even after lmod. FSL module loading failed!"

    import nipype.interfaces.fsl as fsl
    btr = fsl.BET()
    # Provide a dummy file just to see if it generates the command correctly
    dummy_in = tmp_path / "dummy.nii.gz"
    dummy_in.touch() # Create empty file

    btr.inputs.in_file = str(dummy_in)
    btr.inputs.frac = 0.5
    btr.inputs.out_file = str(tmp_path / "dummy_brain.nii.gz")
    cmdline = btr.cmdline
    
    assert "bet" in cmdline, f"BET command not generated properly: {cmdline}"
    assert "dummy.nii.gz" in cmdline, f"Input file not in command line: {cmdline}"
    assert "-f 0.5" in cmdline, f"Fractional intensity threshold not in command line: {cmdline}"
