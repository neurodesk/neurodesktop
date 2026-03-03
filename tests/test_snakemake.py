import subprocess
import pytest
import os
import tempfile

def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, executable='/bin/bash', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return process.returncode, process.stdout.strip()

def test_snakemake_version():
    """Verify snakemake is installed and functioning."""
    cmd = f"snakemake --version"
    code, output = run_cmd(cmd)
    assert code == 0, f"Snakemake version check failed: {output}"
    # Validations depends on output format, generally returns just the version number e.g. "7.32.4"
    assert output and len(output.split(".")) >= 2, f"Unexpected Snakemake output: {output}"

def test_snakemake_fslmaths():
    """Verify snakemake can run a workflow using fslmaths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        snakefile_content = """
rule all:
    input:
        "output.nii.gz"

rule run_fslmaths:
    output:
        "output.nii.gz"
    shell:
        \"\"\"
        set +euo pipefail
        source /opt/neurodesktop/environment_variables.sh 2>/dev/null || true
        source /usr/share/lmod/lmod/init/bash 2>/dev/null || true
        export MODULEPATH=/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/all:/opt/neurocommand/local/containers/modules/all:${{MODULEPATH:-}}
        module load fsl >/dev/null 2>&1 || true
        if ! command -v fslmaths >/dev/null 2>&1; then
            echo "fslmaths not found in PATH"
            exit 1
        fi
        touch {output}
        \"\"\"
"""
        snakefile_path = os.path.join(tmpdir, "Snakefile")
        with open(snakefile_path, "w") as f:
            f.write(snakefile_content)
            
        cmd = f"cd {tmpdir} && snakemake --cores 1"
        code, output = run_cmd(cmd)
        
        assert code == 0, f"Snakemake FSLMaths workflow failed: {output}"
        assert os.path.exists(os.path.join(tmpdir, "output.nii.gz")), "Snakemake did not produce expected output"
