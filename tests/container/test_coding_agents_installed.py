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
import shlex
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


@pytest.mark.parametrize("command,selector,image_cli,expected_argument", [
    (("codex-acp",), "CODEX_PATH", "/usr/bin/codex", "app-server"),
    (("claude-agent-acp",), "CLAUDE_CODE_EXECUTABLE",
     "/opt/neurodesktop/claude-exec", "stream-json"),
    (("/usr/bin/opencode", "acp"), None, None, None),
])
def test_coding_agent_acp_bootstraps_without_credentials(
    tmp_path, command, selector, image_cli, expected_argument
):
    """Start the shipped CLI through ACP without sending a model prompt."""
    (tmp_path / "codex").mkdir(mode=0o700)
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "CODEX_HOME": str(tmp_path / "codex"),
        "DISABLE_TELEMETRY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    invocation = tmp_path / "cli-arguments"
    if selector:
        shim = tmp_path / "image-cli"
        shim.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" >> {shlex.quote(str(invocation))}\n"
            f"exec {shlex.quote(image_cli)} \"$@\"\n"
        )
        shim.chmod(0o755)
        environment[selector] = str(shim)

    async def probe():
        process = await asyncio.create_subprocess_exec(
            *command, env=environment,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)

        async def request(request_id, method, params):
            process.stdin.write((json.dumps({
                "jsonrpc": "2.0", "id": request_id,
                "method": method, "params": params,
            }) + "\n").encode())
            await process.stdin.drain()
            async def response():
                while line := await process.stdout.readline():
                    message = json.loads(line)
                    if message.get("id") == request_id:
                        return message
                raise AssertionError(f"ACP exited during {method}")
            return await asyncio.wait_for(response(), timeout=60)

        try:
            message = await request(1, "initialize", {
                "protocolVersion": 1, "clientCapabilities": {},
            })
            assert "error" not in message, message
            assert message["result"]["protocolVersion"] == 1
            message = await request(2, "session/new", {
                "cwd": str(tmp_path), "mcpServers": [],
            })
            if "error" in message:
                # ACP auth_required is expected with an empty credential store.
                assert selector is not None, message
                assert message["error"]["code"] == -32000, message
                error_message = message["error"]["message"]
                assert (
                    error_message == "Authentication required"
                    or error_message.startswith("Authentication required:")
                ), message
            else:
                assert message["result"]["sessionId"], message
            if selector:
                assert expected_argument in invocation.read_text().splitlines()
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
    asyncio.run(probe())
