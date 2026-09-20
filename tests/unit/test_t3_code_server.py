"""Contracts for the T3 Code sidecar and its image packaging."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat
import sys

import pytest

from testlib import repo_path


PACKAGE_ROOT = repo_path("extensions/t3-code-server")
sys.path.insert(0, str(PACKAGE_ROOT))


def _supervisor_module():
    from neurodesk_t3_code import supervisor

    return supervisor


@pytest.mark.parametrize("retired_flag", [None, "0", "1", "invalid"])
def test_automatic_policy_is_user_owned_and_has_safe_defaults(tmp_path, retired_flag):
    supervisor = _supervisor_module()
    executable = tmp_path / "t3"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    provider_bin = tmp_path / "providers"
    provider_bin.mkdir()

    policy = supervisor.policy_from_environment(
        {
            "HOME": str(tmp_path),
            "NEURODESKTOP_T3_CODE_EXECUTABLE": str(executable),
            "NEURODESKTOP_T3_CODE_PROVIDER_BIN": str(provider_bin),
        },
        euid=os.geteuid(),
    )

    if retired_flag is not None:
        # The removed flag must not disable startup or cause parsing failures.
        environment = {
            "HOME": str(tmp_path),
            "NEURODESKTOP_T3_CODE_EXECUTABLE": str(executable),
            "NEURODESKTOP_T3_CODE_PROVIDER_BIN": str(provider_bin),
            "NEURODESKTOP_T3_CODE_ENABLE": retired_flag,
        }
        assert supervisor.policy_from_environment(environment, euid=os.geteuid()) == policy
    assert isinstance(policy, supervisor.Policy)
    assert policy.host == "127.0.0.1"
    assert policy.port == 3773
    assert policy.base_dir == tmp_path / ".t3"
    assert policy.workdir == tmp_path


def test_policy_rejects_root_and_foreign_home(tmp_path):
    supervisor = _supervisor_module()
    executable = tmp_path / "t3"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    provider_bin = tmp_path / "providers"
    provider_bin.mkdir()
    environment = {
        "HOME": str(tmp_path),
        "NEURODESKTOP_T3_CODE_EXECUTABLE": str(executable),
        "NEURODESKTOP_T3_CODE_PROVIDER_BIN": str(provider_bin),
    }

    with pytest.raises(supervisor.ConfigError, match="root"):
        supervisor.policy_from_environment(environment, euid=0)

    with pytest.raises(supervisor.ConfigError, match="owned"):
        supervisor.policy_from_environment(environment, euid=os.geteuid() + 1)


def test_server_command_and_environment_use_protocol_safe_provider_binaries(tmp_path):
    supervisor = _supervisor_module()
    executable = tmp_path / "t3"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    provider_bin = tmp_path / "providers"
    provider_bin.mkdir()
    policy = supervisor.policy_from_environment(
        {
            "HOME": str(tmp_path),
            "PATH": "/usr/local/sbin:/usr/bin",
            "NEURODESKTOP_T3_CODE_HOST": "0.0.0.0",
            "NEURODESKTOP_T3_CODE_PORT": "4567",
            "NEURODESKTOP_T3_CODE_EXECUTABLE": str(executable),
            "NEURODESKTOP_T3_CODE_PROVIDER_BIN": str(provider_bin),
        },
        euid=os.geteuid(),
    )
    assert isinstance(policy, supervisor.Policy)

    command = supervisor.server_command(policy)
    environment = supervisor.server_environment(
        policy,
        {
            "PATH": "/usr/bin",
            "T3_SERVICE_LAUNCHER_CONTEXT": "desktop-owned",
            "T3_MCP_BEARER_TOKEN": "must-not-cross-boundary",
            "T3CODE_PORT": "9999",
            "T3CODE_CLOUDFLARED_PATH": "/old/cached/cloudflared",
        },
    )

    assert command == [
        str(executable),
        "serve",
        "--mode",
        "web",
        "--no-browser",
        "--host",
        "0.0.0.0",
        "--port",
        "4567",
        "--base-dir",
        str(tmp_path / ".t3"),
        str(tmp_path),
    ]
    assert environment["PATH"].split(os.pathsep)[0] == str(provider_bin)
    assert environment["CODEX_PATH"] == "/usr/bin/codex"
    assert environment["T3CODE_CLOUDFLARED_PATH"] == "/usr/local/bin/cloudflared"
    assert environment["T3CODE_LOG_LEVEL"] == "Warn"
    assert environment["T3CODE_TRACE_MIN_LEVEL"] == "Warn"
    assert environment["T3CODE_TRACE_FILE"] == "/dev/null"
    assert "T3_SERVICE_LAUNCHER_CONTEXT" not in environment
    assert "T3_MCP_BEARER_TOKEN" not in environment
    assert environment["T3CODE_PORT"] == "4567"


def test_supervisor_owns_one_process_and_stops_its_process_group(tmp_path):
    supervisor = _supervisor_module()
    executable = tmp_path / "fake-t3"
    executable.write_text(
        """#!/usr/bin/env python3
import signal
import socket
import sys
import time

host = sys.argv[sys.argv.index('--host') + 1]
port = int(sys.argv[sys.argv.index('--port') + 1])
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((host, port))
sock.listen()
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    time.sleep(0.1)
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    provider_bin = tmp_path / "providers"
    provider_bin.mkdir()
    port = supervisor.find_free_port()
    policy = supervisor.policy_from_environment(
        {
            "HOME": str(tmp_path),
            "NEURODESKTOP_T3_CODE_HOST": "127.0.0.1",
            "NEURODESKTOP_T3_CODE_PORT": str(port),
            "NEURODESKTOP_T3_CODE_EXECUTABLE": str(executable),
            "NEURODESKTOP_T3_CODE_PROVIDER_BIN": str(provider_bin),
        },
        euid=os.geteuid(),
    )
    assert isinstance(policy, supervisor.Policy)

    async def scenario():
        service = supervisor.T3Supervisor(policy)
        service.start()
        await service.wait_ready(timeout=5)
        first_pid = service.pid
        service.start()
        assert service.pid == first_pid
        await service.close()
        assert service.pid is None
        assert service.state == supervisor.ServiceState.STOPPED

    asyncio.run(scenario())


def test_spawn_failure_parks_sidecar_without_escaping_into_jupyter(tmp_path):
    supervisor = _supervisor_module()
    executable = tmp_path / "t3"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    provider_bin = tmp_path / "providers"
    provider_bin.mkdir()
    policy = supervisor.policy_from_environment(
        {
            "HOME": str(tmp_path),
            "NEURODESKTOP_T3_CODE_PORT": str(supervisor.find_free_port()),
            "NEURODESKTOP_T3_CODE_EXECUTABLE": str(executable),
            "NEURODESKTOP_T3_CODE_PROVIDER_BIN": str(provider_bin),
        },
        euid=os.geteuid(),
    )
    assert isinstance(policy, supervisor.Policy)
    executable.unlink()

    async def scenario():
        service = supervisor.T3Supervisor(
            policy, readiness_timeout=0.2, restart_limit=1
        )
        service.start()
        with pytest.raises(RuntimeError, match="parked"):
            await service.wait_ready(timeout=1)
        await service.close()

    asyncio.run(scenario())


def test_extension_package_and_image_install_contract():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    package = json.loads(
        repo_path("config/agents/t3-code/package.json").read_text(encoding="utf-8")
    )
    server_extension = (
        PACKAGE_ROOT / "neurodesk_t3_code/serverext.py"
    ).read_text(encoding="utf-8")
    config = (
        PACKAGE_ROOT
        / "jupyter-config/jupyter_server_config.d/neurodesk_t3_code.json"
    ).read_text(encoding="utf-8")

    assert 'ARG T3_CODE_VERSION="0.0.42"' in dockerfile
    assert package["dependencies"]["t3"] == "0.0.42"
    # The self-contained build bundles its dependencies, so nothing is
    # compiled, no install script runs, and one platform build is installed.
    assert "npm install --omit=dev --ignore-scripts" in dockerfile
    assert "--no-package-lock" in dockerfile
    assert "build-essential" not in dockerfile.split("ARG T3_CODE_VERSION")[1].split(
        "# Expose the installed agent families"
    )[0]
    assert "ls -d /opt/t3-code/node_modules/@t3code/* | wc -l" in dockerfile
    assert "/client/index.html" in dockerfile
    assert "node-pty/build/Release/pty.node" in dockerfile
    assert "apt-install-retry libatomic1" in dockerfile
    assert "/opt/t3-code/node_modules/.bin/t3" in dockerfile
    assert "extensions/t3-code-server" in dockerfile
    assert '"neurodesk_t3_code": true' in config
    assert "_start_jupyter_server_extension(self, _serverapp)" in server_extension


def test_convenience_docker_run_publishes_the_direct_pairing_port():
    launcher = repo_path("build_and_run.sh").read_text(encoding="utf-8")

    assert "-p 127.0.0.1:3774:3773" in launcher
    assert "-p 127.0.0.1:3773:3773" not in launcher
    assert "NEURODESKTOP_T3_CODE_ENABLE" not in launcher
    assert "NEURODESKTOP_T3_CODE_HOST=0.0.0.0" in launcher


def test_login_path_codex_wrapper_keeps_t3_app_server_stdout_clean(tmp_path):
    """T3 hydrates the login PATH and can select the interactive wrapper."""
    import subprocess

    home = tmp_path / "home"
    home.mkdir()
    quiet = tmp_path / "quiet-codex"
    quiet.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps({'argv': sys.argv[1:]}))\n"
    )
    quiet.chmod(0o755)
    wrapper = tmp_path / "codex"
    wrapper.write_text(
        repo_path("config/agents/codex").read_text().replace(
            "/opt/neurodesktop/t3-provider-bin/codex", str(quiet)
        ).replace(
            "/opt/neurodesktop/codex-exec", str(quiet)
        )
    )
    wrapper.chmod(0o755)
    (tmp_path / "AGENTS.md").write_text("existing guidance\n")
    args = ["app-server", "--config", 'model="model with spaces"']
    result = subprocess.run(
        [str(wrapper), *args], cwd=tmp_path,
        env={**os.environ, "HOME": str(home), "CODEX_HOME": str(home / ".codex"),
             "T3CODE_HOME": str(home / ".t3")},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"argv": args}
    assert result.stderr == ""
    assert not (home / ".codex").exists()


def test_provider_default_survives_login_path_hydration_without_overwriting_settings(tmp_path):
    from types import SimpleNamespace
    from neurodesk_t3_code.supervisor import seed_provider_settings
    policy = SimpleNamespace(base_dir=tmp_path / '.t3', provider_bin=tmp_path / 'image-providers')
    seed_provider_settings(policy)
    path = policy.base_dir / 'userdata/settings.json'
    settings = json.loads(path.read_text())
    assert settings['providers']['codex']['binaryPath'] == str(policy.provider_bin / 'codex')
    assert path.stat().st_mode & 0o077 == 0
    settings['providers']['codex']['binaryPath'] = '/custom/codex'
    settings['theme'] = 'custom'
    path.write_text(json.dumps(settings))
    original = path.read_bytes()
    seed_provider_settings(policy)
    assert path.read_bytes() == original
    path.write_text('{invalid')
    seed_provider_settings(policy)
    assert path.read_text() == '{invalid'
