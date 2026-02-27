import subprocess
import pytest

def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return process.returncode, process.stdout.strip()

def test_coding_agent_claude():
    """Verify Claude agent wrapper is available."""
    code, output = run_cmd("command -v claude")
    assert code == 0, f"Claude agent command missing: {output}"

def test_coding_agent_codex():
    """Verify Codex agent wrapper is available."""
    code, output = run_cmd("command -v codex")
    assert code == 0, f"Codex agent command missing: {output}"

def test_coding_agent_opencode():
    """Verify OpenCode agent wrapper is available."""
    code, output = run_cmd("command -v opencode")
    assert code == 0, f"OpenCode agent command missing: {output}"
