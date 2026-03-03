import subprocess
import os
from pathlib import Path
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


def test_coding_agent_claude_replaces_dangling_symlink(tmp_path):
    """Verify Claude wrapper restores binary when ~/.local/bin/claude is a dangling symlink."""
    wrapper_path = Path("/usr/local/sbin/claude")
    if not wrapper_path.exists():
        pytest.skip("Claude wrapper not installed in this environment")

    home_dir = tmp_path / "home"
    bin_dir = home_dir / ".local" / "bin"
    bin_dir.mkdir(parents=True)

    claude_link = bin_dir / "claude"
    claude_link.symlink_to(home_dir / "missing" / "claude")

    fake_default_claude = tmp_path / "default-claude"
    fake_default_claude.write_text(
        "#!/bin/sh\n"
        "echo \"$0 $@\"\n",
        encoding="utf-8",
    )
    fake_default_claude.chmod(0o755)

    test_wrapper = tmp_path / "claude-wrapper-test"
    wrapper_contents = wrapper_path.read_text(encoding="utf-8")
    wrapper_contents = wrapper_contents.replace(
        'DEFAULT_CLAUDE_BIN="/opt/jovyan_defaults/.local/bin/claude"',
        f'DEFAULT_CLAUDE_BIN="{fake_default_claude}"',
    )
    test_wrapper.write_text(wrapper_contents, encoding="utf-8")
    test_wrapper.chmod(0o755)

    result = subprocess.run(
        [str(test_wrapper), "--version"],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(home_dir)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert (bin_dir / "claude").exists(), "Claude binary was not restored"
    assert not (bin_dir / "claude").is_symlink(), "Dangling symlink was not replaced"
    assert os.access(bin_dir / "claude", os.X_OK), "Restored binary is not executable"
    assert "--allow-dangerously-skip-permissions --version" in result.stdout
