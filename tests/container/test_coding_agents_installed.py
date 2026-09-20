"""The coding-agent wrappers must be on PATH in the built image.

The wrappers' behaviour — model selection, MCP wiring, sandbox flags — is
unit-tested against the checked-in sources in
``tests/unit/test_coding_agents.py``. What only the image can answer is whether
they were installed and are reachable as commands.
"""

import os
import asyncio
import json
import signal
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


def test_codex_acp_initializes_with_image_codex(tmp_path):
    """Exercise the real adapter against the image CLI, not its bundled version."""
    async def probe():
        process = await asyncio.create_subprocess_exec(
            "codex-acp", env={**os.environ, "HOME": str(tmp_path),
                              "CODEX_HOME": str(tmp_path / "codex"),
                              "CODEX_PATH": "/usr/bin/codex"},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        try:
            request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": 1, "clientCapabilities": {}}}
            process.stdin.write((json.dumps(request) + "\n").encode())
            await process.stdin.drain()
            async def response():
                while line := await process.stdout.readline():
                    message = json.loads(line)
                    if message.get("id") == 1:
                        return message
                raise AssertionError("ACP exited before initialization")
            message = await asyncio.wait_for(response(), timeout=30)
            assert "error" not in message, message
            assert message["result"]["protocolVersion"] == 1
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
    asyncio.run(probe())
