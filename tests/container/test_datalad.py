import subprocess

def run_cmd(cmd, cwd=None):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd
    )
    return process.returncode, process.stdout.strip()

def test_datalad_available():
    """Verify datalad is installed and functioning."""
    code, output = run_cmd("datalad --version")
    assert code == 0, f"Datalad version check failed: {output}"
