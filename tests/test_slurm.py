import subprocess
import os
import pytest
import time

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

def test_munge_socket_exists():
    """Verify MUNGE socket is present."""
    assert os.path.exists("/run/munge/munge.socket.2"), "MUNGE socket missing"

def test_munge_credential_generation():
    """Verify MUNGE credential generation works for current user."""
    code, output = run_cmd("munge -n")
    assert code == 0, f"MUNGE credential generation failed: {output}"

def test_slurmctld_ping():
    """Verify slurmctld is reachable."""
    code, output = run_cmd("scontrol ping")
    assert code == 0, f"slurmctld ping failed: {output}"

def test_node_state():
    """Verify the compute node is healthy."""
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
    partition_name = os.environ.get("NEURODESKTOP_SLURM_PARTITION", "neurodesktop")
    code, output = run_cmd(f"srun -I20 -N1 -n1 -p {partition_name} /bin/hostname")
    assert code == 0, f"srun smoke test failed: {output}"

def test_sbatch_account_check():
    """Verify sbatch submits correctly and does not fail with InvalidAccount."""
    partition_name = os.environ.get("NEURODESKTOP_SLURM_PARTITION", "neurodesktop")
    code, output = run_cmd(f"sbatch --parsable -p {partition_name} --time=00:01:00 --ntasks=1 --cpus-per-task=1 --mem=64M --wrap '/bin/true'")
    assert code == 0, f"sbatch check failed to submit: {output}"
    
    job_id = output.split(";")[0].strip()
    assert job_id.isdigit(), f"sbatch output did not provide a valid job ID: {output}"
    
    account_invalid = False
    for _ in range(10):
        code, status_out = run_cmd(f"squeue -h -j {job_id} -o '%T|%r'")
        if not status_out:
            break
        
        parts = status_out.split("|")
        if len(parts) > 1 and parts[1] == "InvalidAccount":
            account_invalid = True
            break
        
        time.sleep(1)
        
    run_cmd(f"scancel {job_id}")
    
    assert not account_invalid, f"sbatch job {job_id} pending with Reason=InvalidAccount"
