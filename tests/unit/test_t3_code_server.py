"""Contracts for the T3 Code sidecar and its image packaging."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from types import SimpleNamespace

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
    assert policy.startup_timeout == 120


@pytest.mark.parametrize("raw,expected", [("45", 45), ("2.5", 2.5), ("3600", 3600)])
def test_startup_timeout_is_configurable(tmp_path, raw, expected):
    supervisor = _supervisor_module()
    policy = supervisor.policy_from_environment(
        {**_policy_environment(tmp_path), "NEURODESKTOP_T3_CODE_STARTUP_TIMEOUT": raw},
        euid=os.geteuid(),
    )
    assert policy.startup_timeout == expected


@pytest.mark.parametrize("raw", ["", "soon", "0", "-5", "3601", "nan", "inf"])
def test_unusable_startup_timeout_is_rejected(tmp_path, raw):
    supervisor = _supervisor_module()
    with pytest.raises(supervisor.ConfigError, match="STARTUP_TIMEOUT"):
        supervisor.policy_from_environment(
            {**_policy_environment(tmp_path), "NEURODESKTOP_T3_CODE_STARTUP_TIMEOUT": raw},
            euid=os.geteuid(),
        )


def _policy_environment(tmp_path, executable=None):
    if executable is None:
        executable = tmp_path / "t3"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
    provider_bin = tmp_path / "providers"
    provider_bin.mkdir(exist_ok=True)
    return {
        "HOME": str(tmp_path),
        "NEURODESKTOP_T3_CODE_PORT": str(_supervisor_module().find_free_port()),
        "NEURODESKTOP_T3_CODE_EXECUTABLE": str(executable),
        "NEURODESKTOP_T3_CODE_PROVIDER_BIN": str(provider_bin),
    }


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
    """Verify repeated starts share one child and shutdown terminates its group."""
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
    client, _ = sock.accept()
    with client:
        if client.recv(4096):
            client.sendall(b'HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\n{}')
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
            dataclasses.replace(policy, startup_timeout=0.2), restart_limit=1
        )
        service.start()
        with pytest.raises(RuntimeError, match="parked"):
            await service.wait_ready(timeout=1)
        await service.close()

    asyncio.run(scenario())


def test_extension_package_and_image_install_contract():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    server_extension = (
        PACKAGE_ROOT / "neurodesk_t3_code/serverext.py"
    ).read_text(encoding="utf-8")
    config = (
        PACKAGE_ROOT
        / "jupyter-config/jupyter_server_config.d/neurodesk_t3_code.json"
    ).read_text(encoding="utf-8")
    layer = dockerfile.split('ARG T3_CODE_VERSION="')[1].split(
        "# Expose the installed agent families"
    )[0]

    assert 'ARG T3_CODE_VERSION="0.1.5"' in dockerfile
    # The npm `t3` package only downloads this archive per user at first run,
    # so the image installs the checksum-pinned release archive itself.
    assert re.search(r'ARG T3_CODE_AMD64_SHA256="[0-9a-f]{64}"', dockerfile)
    assert re.search(r'ARG T3_CODE_ARM64_SHA256="[0-9a-f]{64}"', dockerfile)
    assert (
        "https://github.com/pingdotgg/t3code/releases/download/v${T3_CODE_VERSION}/${t3_archive}"
        in layer
    )
    assert "sha256sum -c -" in layer
    assert "npm install" not in layer
    assert "build-essential" not in layer
    assert "/opt/t3-code/client/index.html" in layer
    assert 'node-pty/prebuilds/linux-${t3_arch}/pty.node' in layer
    assert 'if ldd /opt/t3-code/t3 | grep "not found"; then exit 1; fi' in layer
    assert "ln -s /opt/t3-code/t3 /usr/local/bin/t3" in layer
    assert _supervisor_module().DEFAULT_EXECUTABLE == Path("/opt/t3-code/t3")
    assert not repo_path("config/agents/t3-code").exists()
    assert "extensions/t3-code-server" in dockerfile
    assert '"neurodesk_t3_code": true' in config
    assert "async def _start_jupyter_server_extension(self, _serverapp" in server_extension


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


def test_t3_codex_provider_starts_from_a_read_only_directory(tmp_path):
    """T3 0.1.5 probes Codex from the server's working directory."""
    import subprocess

    guidance = tmp_path / "AGENTS.md"
    guidance.write_text("image guidance\n")
    quiet = tmp_path / "quiet-codex"
    quiet.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps({'argv': sys.argv[1:]}))\n"
    )
    quiet.chmod(0o755)
    wrapper = tmp_path / "codex"
    wrapper.write_text(
        repo_path("config/agents/t3-provider-bin/codex").read_text()
        .replace("/opt/neurodesktop/agent_shell_setup.sh", str(tmp_path / "absent.sh"))
        .replace("/opt/AGENTS.md", str(guidance))
        .replace("/usr/bin/codex", str(quiet))
    )
    wrapper.chmod(0o755)
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    read_only.chmod(0o555)
    if os.access(read_only, os.W_OK):
        pytest.skip("directory permissions are not enforced for this user")
    try:
        result = subprocess.run(
            [str(wrapper), "app-server"], cwd=read_only,
            capture_output=True, text=True, timeout=10,
        )
    finally:
        read_only.chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"argv": ["app-server"]}
    assert result.stderr == ""
    assert not (read_only / "AGENTS.md").exists()


def _run_opencode_launcher(tmp_path, bashrc, environment):
    import subprocess

    home = tmp_path / "home"
    home.mkdir()
    if bashrc is not None:
        (home / ".bashrc").write_text(bashrc)
    stub = tmp_path / "opencode-stub"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:],\n"
        "                  'key': os.environ.get('NEURODESK_API_KEY')}))\n"
    )
    stub.chmod(0o755)
    launcher = tmp_path / "opencode"
    launcher.write_text(
        repo_path("config/agents/t3-provider-bin/opencode").read_text().replace(
            "/usr/bin/opencode", str(stub)
        ).replace("/opt/neurodesktop/opencode_update_config.py", str(repo_path("config/agents/opencode_update_config.py")))
    )
    launcher.chmod(0o755)
    args = ["serve", "--port", "4096"]
    result = subprocess.run(
        [str(launcher), *args], cwd=tmp_path,
        env={**{k: v for k, v in os.environ.items() if k != "NEURODESK_API_KEY"},
             "HOME": str(home), **environment},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["argv"] == args
    return payload["key"]


@pytest.mark.parametrize("export", [
    "export NEURODESK_API_KEY='shell-key'\n",
    'export NEURODESK_API_KEY="shell-key"\n',
    "export NEURODESK_API_KEY=shell-key\n",
    "export NEURODESK_API_KEY=stale\nexport NEURODESK_API_KEY='shell-key'\n",
])
def test_opencode_launcher_hydrates_the_key_the_interactive_wrapper_stored(
    tmp_path, export
):
    """T3 execs the launcher without a login shell, so ~/.bashrc never runs."""
    bashrc = f"# neurodesk\n{export}export OTHER=leave-me\n"
    assert _run_opencode_launcher(tmp_path, bashrc, {}) == "shell-key"


@pytest.mark.parametrize("bashrc", [None, "", "# nothing to export\n",
                                    "NEURODESK_API_KEY=not-an-export\n"])
def test_opencode_launcher_starts_clean_without_a_stored_key(tmp_path, bashrc):
    assert _run_opencode_launcher(tmp_path, bashrc, {}) is None


def test_opencode_launcher_prefers_the_environment_over_the_shell_profile(tmp_path):
    key = _run_opencode_launcher(
        tmp_path, "export NEURODESK_API_KEY='shell-key'\n",
        {"NEURODESK_API_KEY": "session-key"},
    )
    assert key == "session-key"


def test_provider_default_survives_login_path_hydration_without_overwriting_settings(tmp_path):
    from types import SimpleNamespace
    from neurodesk_t3_code.supervisor import prepare_startup_settings
    policy = SimpleNamespace(base_dir=tmp_path / '.t3', provider_bin=tmp_path / 'image-providers')
    prepare_startup_settings(policy)
    path = policy.base_dir / 'userdata/settings.json'
    settings = json.loads(path.read_text())
    assert settings['providerInstances']['codex']['config']['binaryPath'] == str(policy.provider_bin / 'codex')
    assert path.stat().st_mode & 0o077 == 0
    settings['providerInstances']['codex']['config']['binaryPath'] = '/custom/codex'
    settings['theme'] = 'custom'
    path.write_text(json.dumps(settings))
    original = path.read_bytes()
    prepare_startup_settings(policy)
    assert path.read_bytes() == original
    path.write_text('{invalid')
    prepare_startup_settings(policy)
    assert path.read_text() == '{invalid'


def test_opencode_is_seeded_enabled_because_t3_ships_that_driver_off(tmp_path):
    """T3 hides a driver it ships disabled unless the instance says otherwise.

    The flag belongs on the instance envelope: T3 folds any in-config flag into
    the envelope on load, and an explicit envelope flag beats the driver default.
    """
    from neurodesk_t3_code.supervisor import prepare_startup_settings
    policy = SimpleNamespace(base_dir=tmp_path, provider_bin=tmp_path / "providers")
    prepare_startup_settings(policy)
    path = tmp_path / "userdata/settings.json"
    settings = json.loads(path.read_text())
    assert settings["enableProviderUpdateChecks"] is False
    instances = settings["providerInstances"]
    assert instances["opencode"] == {
        "driver": "opencode",
        "enabled": True,
        "config": {"binaryPath": str(policy.provider_bin / "opencode")},
    }
    assert instances["codex"] == {
        "driver": "codex",
        "config": {"binaryPath": str(policy.provider_bin / "codex")},
    }
    assert path.stat().st_mode & 0o077 == 0
    before = path.read_bytes()
    prepare_startup_settings(policy)
    assert path.read_bytes() == before


@pytest.mark.parametrize("bookkeeping", [{}, {"enabled": False}])
def test_opencode_seed_ignores_t3s_own_default_off_bookkeeping(tmp_path, bookkeeping):
    """T3 writes `providers.opencode.enabled` false on every fresh environment.

    Reading that as a user decision would make the seed a no-op on the
    persistent `~/.t3` every existing container already carries.
    """
    from neurodesk_t3_code.supervisor import prepare_startup_settings
    policy = SimpleNamespace(base_dir=tmp_path, provider_bin=tmp_path / "providers")
    path = tmp_path / "userdata/settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"providers": {"opencode": bookkeeping}}))
    prepare_startup_settings(policy)
    settings = json.loads(path.read_text())
    assert settings["providerInstances"]["opencode"]["enabled"] is True
    assert settings["providers"] == {"opencode": bookkeeping}


@pytest.mark.parametrize("existing", [
    {"providers": {"opencode": {"enabled": True}}},
    {"providers": {"opencode": {"binaryPath": "/custom"}}},
    {"providers": {"opencode": {"serverUrl": "http://127.0.0.1:4096"}}},
    {"providerInstances": {"opencode": {"driver": "opencode", "enabled": False}}},
    {"providerInstances": {"work": {"driver": "opencode"}}},
    {"providers": {"opencode": None}},
    {"providers": {"opencode": {"binaryPath": "PROVIDER_BIN/opencode"}}},
])
def test_opencode_seed_leaves_a_configured_opencode_alone(tmp_path, existing):
    from neurodesk_t3_code.supervisor import prepare_startup_settings
    policy = SimpleNamespace(base_dir=tmp_path, provider_bin=tmp_path / "providers")
    path = tmp_path / "userdata/settings.json"
    path.parent.mkdir()
    # Only earlier images wrote a legacy Codex default, so an OpenCode entry
    # naming the image launcher is the user's, not ours to promote.
    existing = json.loads(
        json.dumps(existing).replace("PROVIDER_BIN", str(policy.provider_bin))
    )
    path.write_text(json.dumps(existing))
    prepare_startup_settings(policy)
    settings = json.loads(path.read_text())
    assert settings.get("providers", {}) == existing.get("providers", {})
    assert settings["providerInstances"] == {
        **existing.get("providerInstances", {}),
        "codex": {"driver": "codex",
                  "config": {"binaryPath": str(policy.provider_bin / "codex")}},
    }


@pytest.mark.parametrize("existing", [
    {"providerInstances": {"codex": {"driver": "codex", "enabled": False}}},
    {"providerInstances": {"work": {"driver": "codex", "config": {"binaryPath": "/custom"}}}},
    {"providerInstances": {"codex": {"driver": "other"}}},
    {"providers": {"codex": {"binaryPath": "/custom"}}},
    {"providers": {"codex": {"enabled": False}}},
    {"providers": {"codex": {}}},
    {"providers": {"codex": None}},
])
def test_provider_seed_preserves_user_configuration(tmp_path, existing):
    """A user-owned Codex stays untouched while an absent OpenCode still seeds."""
    from neurodesk_t3_code.supervisor import prepare_startup_settings
    policy = SimpleNamespace(base_dir=tmp_path, provider_bin=tmp_path / "providers")
    path = tmp_path / "userdata/settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps(existing))
    prepare_startup_settings(policy)
    settings = json.loads(path.read_text())
    assert settings.get("providers", {}) == existing.get("providers", {})
    assert settings["providerInstances"] == {
        **existing.get("providerInstances", {}),
        "opencode": {"driver": "opencode", "enabled": True,
                     "config": {"binaryPath": str(policy.provider_bin / "opencode")}},
    }


@pytest.mark.parametrize("existing", [
    {"providers": []},
    {"providerInstances": []},
    {"providerInstances": {"broken": None}},
    [],
])
def test_provider_seed_writes_nothing_over_unreadable_settings(tmp_path, existing):
    from neurodesk_t3_code.supervisor import prepare_startup_settings
    policy = SimpleNamespace(base_dir=tmp_path, provider_bin=tmp_path / "providers")
    path = tmp_path / "userdata/settings.json"
    path.parent.mkdir()
    if isinstance(existing, dict):
        existing = {**existing, "enableProviderUpdateChecks": False}
    path.write_text(json.dumps(existing))
    before = path.read_bytes()
    prepare_startup_settings(policy)
    assert path.read_bytes() == before


def test_provider_seed_promotes_only_exact_previous_default(tmp_path):
    from neurodesk_t3_code.supervisor import prepare_startup_settings
    policy = SimpleNamespace(base_dir=tmp_path, provider_bin=tmp_path / "providers")
    default = {"binaryPath": str(policy.provider_bin / "codex")}
    other = {"driver": "claudeAgent", "enabled": False}
    opencode = {"driver": "opencode", "enabled": True,
                "config": {"binaryPath": str(policy.provider_bin / "opencode")}}
    settings = {"providers": {"codex": default},
                "providerInstances": {"claudeAgent": other}, "theme": "dark"}
    path = tmp_path / "userdata/settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps(settings))
    prepare_startup_settings(policy)
    actual = json.loads(path.read_text())
    assert actual == {**settings, "enableProviderUpdateChecks": False, "providerInstances": {
        "claudeAgent": other, "codex": {"driver": "codex", "config": default},
        "opencode": opencode}}
    assert path.stat().st_mode & 0o077 == 0
    before = path.read_bytes()
    prepare_startup_settings(policy)
    assert path.read_bytes() == before

    settings["enableProviderUpdateChecks"] = False
    settings["providers"]["codex"]["enabled"] = False
    settings["providerInstances"]["opencode"] = opencode
    path.write_text(json.dumps(settings))
    before = path.read_bytes()
    prepare_startup_settings(policy)
    assert path.read_bytes() == before


@pytest.mark.parametrize("original", [{}, {"enableProviderUpdateChecks": True},
                                     {"enableProviderUpdateChecks": False}])
def test_update_notices_disabled_without_changing_provider_settings(tmp_path, original):
    from neurodesk_t3_code.supervisor import prepare_startup_settings
    policy = SimpleNamespace(base_dir=tmp_path, provider_bin=tmp_path / "providers")
    path = tmp_path / "userdata/settings.json"
    path.parent.mkdir()
    original = {**original, "providerInstances": {
        "custom": {"driver": "codex", "enabled": False},
        "custom-opencode": {"driver": "opencode", "config": {"binaryPath": "/custom/opencode"}},
    }}
    path.write_text(json.dumps(original))
    prepare_startup_settings(policy)
    assert json.loads(path.read_text()) == {**original, "enableProviderUpdateChecks": False}
    before = path.read_bytes()
    prepare_startup_settings(policy)
    assert path.read_bytes() == before


def test_update_notice_policy_preserves_malformed_settings(tmp_path):
    from neurodesk_t3_code.supervisor import prepare_startup_settings
    policy = SimpleNamespace(base_dir=tmp_path, provider_bin=tmp_path / "providers")
    prepare_startup_settings(policy)
    path = tmp_path / "userdata/settings.json"
    assert json.loads(path.read_text()) == {
        "enableProviderUpdateChecks": False,
        "providerInstances": {
            "codex": {"driver": "codex",
                      "config": {"binaryPath": str(policy.provider_bin / "codex")}},
            "opencode": {"driver": "opencode", "enabled": True,
                         "config": {"binaryPath": str(policy.provider_bin / "opencode")}},
        },
    }
    assert path.stat().st_mode & 0o077 == 0
    for content in ("{invalid", "[]"):
        path.write_text(content)
        prepare_startup_settings(policy)
        assert path.read_text() == content


def test_readiness_waits_for_http_after_tcp_listener_opens(tmp_path):
    """Reproduce a bound TCP port whose first HTTP request never receives a response."""
    supervisor = _supervisor_module()

    async def scenario():
        requests = []
        ready = asyncio.Event()

        async def handle(reader, writer):
            try:
                request = await reader.readuntil(b'\r\n\r\n')
                requests.append(request)
                if len(requests) == 1:
                    # T3 can accept an early request that never gets a response.
                    await reader.read()
                else:
                    await ready.wait()
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}')
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        async with server:
            policy = SimpleNamespace(readiness_host='127.0.0.1', startup_timeout=3,
                                     port=server.sockets[0].getsockname()[1])
            service = supervisor.T3Supervisor(policy)
            service._process = SimpleNamespace(returncode=None)
            pending = asyncio.create_task(service._wait_for_listener())
            try:
                await asyncio.sleep(0.1)
                assert not pending.done(), 'TCP alone must not mark the server ready'
                ready.set()
                assert await pending is True
                assert len(requests) >= 2
                assert all(r.startswith(b'GET /.well-known/t3/environment HTTP/1.1\r\n')
                           for r in requests)
                assert all(b'Authorization:' not in r for r in requests)
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())


# Answers only after SLOW_START seconds, and exits at once while FAIL_FLAG exists,
# standing in for a T3 server that is slow or broken under load.
SLOW_T3 = """#!/usr/bin/env python3
import os, signal, socket, subprocess, sys, time
sys.exit(1) if os.path.exists(os.environ["FAIL_FLAG"]) else None
helper = subprocess.Popen(["sleep", "60"])
with open(os.environ["SPAWN_LOG"], "a") as log:
    log.write(f"spawn {os.getpid()} {helper.pid}\\n")
time.sleep(float(os.environ["SLOW_START"]))
host = sys.argv[sys.argv.index("--host") + 1]
port = int(sys.argv[sys.argv.index("--port") + 1])
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((host, port))
sock.listen()
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    client, _ = sock.accept()
    with client:
        if client.recv(4096):
            client.sendall(b"HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\n{}")
"""


def _slow_t3(tmp_path, monkeypatch, *, slow_start, failing):
    supervisor = _supervisor_module()
    executable = tmp_path / "slow-t3"
    executable.write_text(SLOW_T3, encoding="utf-8")
    executable.chmod(0o755)
    fail_flag, spawn_log = tmp_path / "fail", tmp_path / "spawns"
    if failing:
        fail_flag.touch()
    monkeypatch.setenv("FAIL_FLAG", str(fail_flag))
    monkeypatch.setenv("SPAWN_LOG", str(spawn_log))
    monkeypatch.setenv("SLOW_START", str(slow_start))
    environment = _policy_environment(tmp_path, executable)
    return supervisor, environment, fail_flag, spawn_log


def test_startup_timeout_lets_a_slow_server_finish_its_first_start(tmp_path, monkeypatch):
    """A loaded host can take longer than the old fixed 20 second deadline."""
    supervisor, environment, _flag, spawn_log = _slow_t3(
        tmp_path, monkeypatch, slow_start=1.0, failing=False
    )

    async def start(timeout):
        policy = supervisor.policy_from_environment(
            {**environment, "NEURODESKTOP_T3_CODE_STARTUP_TIMEOUT": timeout},
            euid=os.geteuid(),
        )
        service = supervisor.T3Supervisor(policy, restart_limit=2)
        service.start()
        try:
            await service.wait_ready(timeout=15)
            return service.state
        except RuntimeError:
            return service.state
        finally:
            await service.close()

    assert asyncio.run(start("0.3")) is supervisor.ServiceState.PARKED
    assert spawn_log.read_text().count("spawn") == 2
    for line in spawn_log.read_text().splitlines():
        for pid in line.split()[1:]:
            _assert_exited(int(pid))
    spawn_log.unlink()
    assert asyncio.run(start("10")) is supervisor.ServiceState.READY
    assert spawn_log.read_text().count("spawn") == 1


def test_waiting_covers_every_retry_rather_than_one_startup_timeout(tmp_path, monkeypatch):
    """Readiness waiters must see the final outcome after backoff, not give up early."""
    supervisor, environment, fail_flag, _log = _slow_t3(
        tmp_path, monkeypatch, slow_start=0, failing=True
    )
    policy = supervisor.policy_from_environment(
        {**environment, "NEURODESKTOP_T3_CODE_STARTUP_TIMEOUT": "0.5"}, euid=os.geteuid()
    )

    async def scenario():
        service = supervisor.T3Supervisor(policy, restart_limit=3)
        service.start()
        # Backoff of 1 s and 2 s already exceeds the 0.5 s startup timeout.
        asyncio.get_running_loop().call_later(2.0, fail_flag.unlink)
        await service.wait_ready()
        assert service.state is supervisor.ServiceState.READY
        await service.close()

    asyncio.run(scenario())


def test_parked_sidecar_restarts_without_touching_other_processes(tmp_path, monkeypatch):
    """Retry SciGent starts a fresh budget and leaves a kernel-like sibling running."""
    import subprocess

    supervisor, environment, fail_flag, _log = _slow_t3(
        tmp_path, monkeypatch, slow_start=0, failing=True
    )
    policy = supervisor.policy_from_environment(environment, euid=os.geteuid())
    kernel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    async def scenario():
        service = supervisor.T3Supervisor(policy, restart_limit=1)
        service.start()
        with pytest.raises(RuntimeError, match="parked"):
            await service.wait_ready(timeout=5)
        fail_flag.unlink()
        service.start()
        assert service.state is supervisor.ServiceState.STARTING
        await service.wait_ready(timeout=10)
        assert service.state is supervisor.ServiceState.READY
        service_pid = service.pid
        await service.close()
        return service_pid

    try:
        service_pid = asyncio.run(scenario())
        assert service_pid != kernel.pid
        assert kernel.poll() is None, "restarting SciGent must not stop kernels"
    finally:
        kernel.kill()
        kernel.wait()


def test_waiters_follow_a_restart_after_a_ready_server_exits(tmp_path, monkeypatch):
    import signal

    supervisor, environment, _flag, _log = _slow_t3(
        tmp_path, monkeypatch, slow_start=0, failing=False
    )
    policy = supervisor.policy_from_environment(environment, euid=os.geteuid())

    async def scenario():
        service = supervisor.T3Supervisor(policy, restart_limit=3)
        service.start()
        await service.wait_ready(timeout=5)
        first = service.pid
        os.killpg(first, signal.SIGKILL)
        while service.state is supervisor.ServiceState.READY:
            await asyncio.sleep(0.01)
        await service.wait_ready(timeout=10)
        assert service.state is supervisor.ServiceState.READY
        assert service.pid != first
        await service.close()

    asyncio.run(scenario())


def test_unusable_state_directory_parks_instead_of_hanging_waiters(tmp_path):
    supervisor = _supervisor_module()
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    policy = supervisor.policy_from_environment(
        {**_policy_environment(tmp_path), "NEURODESKTOP_T3_CODE_HOME": str(blocker / ".t3")},
        euid=os.geteuid(),
    )

    async def scenario():
        service = supervisor.T3Supervisor(policy, restart_limit=1)
        service.start()
        with pytest.raises(RuntimeError, match="parked"):
            await service.wait_ready(timeout=5)
        await service.close()

    asyncio.run(scenario())


def test_unexpected_failure_parks_so_retry_recovers_after_repair(tmp_path, monkeypatch):
    """A RecursionError from deeply nested settings must not need a Jupyter restart."""
    supervisor, environment, _flag, _log = _slow_t3(
        tmp_path, monkeypatch, slow_start=0, failing=False
    )
    policy = supervisor.policy_from_environment(environment, euid=os.geteuid())
    prepare = supervisor.prepare_startup_settings
    repaired = False

    def settings(policy):
        if not repaired:
            raise RecursionError("maximum recursion depth exceeded")
        prepare(policy)

    monkeypatch.setattr(supervisor, "prepare_startup_settings", settings)

    async def scenario():
        nonlocal repaired
        service = supervisor.T3Supervisor(policy)
        service.start()
        with pytest.raises(RuntimeError, match="did not start: parked"):
            await service.wait_ready(timeout=5)
        repaired = True
        service.start()
        await service.wait_ready(timeout=10)
        assert service.state is supervisor.ServiceState.READY
        await service.close()
        assert service.state is supervisor.ServiceState.STOPPED

    asyncio.run(scenario())


def _assert_exited(pid):
    """Probe a process that may be a reparented zombie awaiting reaping by init."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as stat_file:
                if stat_file.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return
        except FileNotFoundError:
            return
        time.sleep(0.05)
    raise AssertionError(f"process {pid} is still running")


def test_retry_at_the_first_parked_instant_really_restarts(tmp_path, monkeypatch):
    """PARKED must not be visible while the failed task is still cleaning up."""
    supervisor, environment, _flag, _log = _slow_t3(
        tmp_path, monkeypatch, slow_start=0, failing=False
    )
    policy = supervisor.policy_from_environment(environment, euid=os.geteuid())

    async def scenario():
        service = supervisor.T3Supervisor(policy)
        listener = service._wait_for_listener

        async def fail_with_child_running():
            await asyncio.sleep(0.3)
            raise RuntimeError("unexpected supervision failure")

        service._wait_for_listener = fail_with_child_running
        service.start()
        while service.state is not supervisor.ServiceState.PARKED:
            await asyncio.sleep(0)
        failed_task = service._task
        service._wait_for_listener = listener
        service.start()
        assert service._task is not failed_task, "Retry must start a new supervision task"
        assert service.state is supervisor.ServiceState.STARTING
        await service.wait_ready(timeout=10)
        assert service.state is supervisor.ServiceState.READY
        await service.close()

    asyncio.run(scenario())
