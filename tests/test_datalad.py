import os
import subprocess
import pytest

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

def test_datalad_download(tmp_path):
    """Test downloading a small dataset with datalad."""
    code, output = run_cmd("command -v datalad")
    if code != 0:
        pytest.fail("datalad command not in PATH")

    # We will install a small repository. datalad install only downloads git/annex metadata, 
    # which is very fast and doesn't download the large real files yet.
    target_dir = tmp_path / "ds000001"
    
    # Needs git config for datalad to work without warnings or errors in some environments
    run_cmd("git config --global user.name 'Test User'")
    run_cmd("git config --global user.email 'test@example.com'")

    # Let's clone within the temp dir
    install_cmd = f"datalad clone https://github.com/OpenNeuroDatasets/ds000001.git ds000001"
    code, output = run_cmd(install_cmd, cwd=str(tmp_path))
    
    assert code == 0, f"Datalad install failed: {output}"
    assert target_dir.exists(), "Datalad dataset directory was not created"
    
    # Check if we can list the dataset contents
    assert (target_dir / "dataset_description.json").exists(), "Dataset description missing"

    # Try downloading just one small file to verify annex get works
    get_cmd = f"datalad get dataset_description.json"
    code, output = run_cmd(get_cmd, cwd=str(target_dir))
    assert code == 0, f"Datalad get failed: {output}"
