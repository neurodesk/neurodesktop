import subprocess
import os
import pytest
import shlex

def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return process.returncode, process.stdout.strip()

def test_slurm_commands_available():
    """Verify essential SLURM commands are in the PATH."""
    expected_cmds = [
        "munge",
        "sbatch",
        "scancel",
        "scontrol",
        "sinfo",
        "squeue",
        "srun"
    ]
    for cmd in expected_cmds:
        code, _ = run_cmd(f"command -v {cmd}")
        assert code == 0, f"Command missing: {cmd}"

def _slurm_should_be_running():
    """Return True if Slurm is expected to be running in this environment."""
    enable = os.environ.get("NEURODESKTOP_SLURM_ENABLE", "1")
    mode = os.environ.get("NEURODESKTOP_SLURM_MODE", "local")
    return enable not in ("0", "false", "no") and mode == "local"


def _skip_if_slurm_not_expected():
    """Skip the test if Slurm is intentionally disabled."""
    if not _slurm_should_be_running():
        pytest.skip("Slurm is disabled via NEURODESKTOP_SLURM_ENABLE=0 or non-local mode")


def test_slurm_setup_when_enabled():
    """When Slurm is expected, verify config and services were set up."""
    if not _slurm_should_be_running():
        pytest.skip("Slurm is disabled — nothing to assert")

    assert os.path.exists("/etc/slurm/slurm.conf"), (
        "Slurm is enabled but /etc/slurm/slurm.conf is missing — "
        "startup scripts failed to configure Slurm"
    )
    assert os.path.exists("/run/munge/munge.socket.2"), (
        "Slurm is enabled but MUNGE socket missing at /run/munge/munge.socket.2 — "
        "munged failed to start"
    )
    code, output = run_cmd("scontrol ping")
    assert code == 0, (
        f"Slurm is enabled but slurmctld is not responding: {output}"
    )


def test_munge_credential_generation():
    """Verify MUNGE credential generation works for current user."""
    _skip_if_slurm_not_expected()
    code, output = run_cmd("munge -n")
    assert code == 0, f"MUNGE credential generation failed: {output}"

def test_node_state():
    """Verify the compute node is healthy."""
    _skip_if_slurm_not_expected()
    code, hostname = run_cmd("hostname -s")
    if code != 0:
        _, hostname = run_cmd("hostname")
    
    code, out = run_cmd(f"scontrol show node {hostname}")
    assert code == 0, f"Could not read node state for {hostname}"
    
    state = ""
    for line in out.splitlines():
        if "State=" in line:
            parts = line.split()
            for p in parts:
                if p.startswith("State="):
                    state = p.split("=")[1]
                    break
    
    assert state != "", f"Could not parse state for {hostname}"
    # Valid states are typically IDLE, ALLOCATED, MIXED, etc. 
    # Invalid states include UNKNOWN, DOWN, DRAIN, FAIL, NOT_RESPONDING
    invalid_states = ["UNKNOWN", "DOWN", "DRAIN", "FAIL", "NOT_RESPONDING"]
    assert not any(iv in state for iv in invalid_states), f"Node state is unhealthy: {state}"

def test_srun_smoke_test():
    """Verify srun can execute a basic command."""
    _skip_if_slurm_not_expected()
    partition_name = os.environ.get("NEURODESKTOP_SLURM_PARTITION", "neurodesktop")
    code, output = run_cmd(f"srun -I20 -N1 -n1 -p {partition_name} /bin/hostname")
    assert code == 0, f"srun smoke test failed: {output}"

def test_sbatch_completes_and_writes_output(tmp_path):
    """Submission alone cannot detect a broken batch execution environment."""
    _skip_if_slurm_not_expected()
    partition = os.environ.get("NEURODESKTOP_SLURM_PARTITION", "neurodesktop")
    output_file = tmp_path / "batch-result.txt"
    script = tmp_path / "job.sh"
    script.write_text("#!/bin/bash\nset -euo pipefail\n"
                      "source /opt/neurodesktop/agent_bash_env.sh\n"
                      "module --version\n"
                      "printf 'neurodesktop-batch-ok\\n' > "
                      + shlex.quote(str(output_file)) + "\n")
    process = subprocess.Popen(
        ["sbatch", "--parsable", "--wait", "--job-name=neurodesktop-test",
         "--partition", partition, "--time=00:01:00", "--ntasks=1",
         "--cpus-per-task=1", "--mem=64M", "--output", str(tmp_path / "slurm-%j.out"),
         str(script)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    output = ""
    try:
        output, _ = process.communicate(timeout=180)
        assert process.returncode == 0, f"Batch job did not complete successfully: {output}"
        job_id = output.splitlines()[0].split(";")[0].strip()
        assert job_id.isdigit(), output
        assert output_file.read_text() == "neurodesktop-batch-ok\n"
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=10)
        pytest.fail(f"Batch job did not complete within 180s: {output}")
    finally:
        # sbatch emits its job ID before waiting. Cancel that specific job even
        # if the wait timed out; never cancel another user's jobs by name.
        for line in output.splitlines():
            job_id = line.split(";")[0].strip()
            if job_id.isdigit():
                subprocess.run(["scancel", job_id], capture_output=True, timeout=10)
                break
