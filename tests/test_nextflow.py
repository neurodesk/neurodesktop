import subprocess
import os
import pytest

def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
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
