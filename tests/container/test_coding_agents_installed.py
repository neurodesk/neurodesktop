"""The coding-agent wrappers must be on PATH in the built image.

The wrappers' behaviour — model selection, MCP wiring, sandbox flags — is
unit-tested against the checked-in sources in
``tests/unit/test_coding_agents.py``. What only the image can answer is whether
they were installed and are reachable as commands.
"""

import os
from pathlib import Path

import pytest

from testlib import run_cmd


WRAPPERS = {
    "claude": "/usr/local/sbin/claude",
    "codex": "/usr/local/sbin/codex",
    "opencode": "/usr/local/sbin/opencode",
}


@pytest.mark.parametrize("command,installed_path", sorted(WRAPPERS.items()))
def test_coding_agent_wrapper_is_installed_and_on_path(command, installed_path):
    wrapper = Path(
        os.environ.get(
            f"NEURODESKTOP_TEST_{command.upper()}_WRAPPER", installed_path
        )
    )

    assert wrapper.exists(), f"{command} wrapper missing: {wrapper}"
    assert os.access(wrapper, os.X_OK), f"{command} wrapper not executable: {wrapper}"

    code, output = run_cmd(f"command -v {command}")
    assert code == 0, f"{command} agent command missing from PATH: {output}"
