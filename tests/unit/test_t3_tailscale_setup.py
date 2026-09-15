"""Exercise the guided flow without authenticating or creating real credentials."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from testlib import load_source_module, repo_path


HOST = "neurodesktop.example-tail.ts.net"
TARGET = "http://127.0.0.1:3773"
ENVIRONMENT = "test-environment-id"


def proxy_config(target=TARGET):
    return {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {f"{HOST}:443": {"Handlers": {"/": {"Proxy": target}}}},
    }


@pytest.fixture
def wizard():
    return load_source_module(
        "t3_setup", "/opt/neurodesktop/t3_tailscale_setup.py",
        "scripts/t3_tailscale_setup.py",
    )


@pytest.fixture
def flow(wizard, monkeypatch, tmp_path, capsys):
    state = SimpleNamespace(
        status={"BackendState": "Running", "Self": {"DNSName": HOST + "."}},
        config={}, calls=[], answers=iter(["", "yes"]),
    )
    base = tmp_path / "t3"
    (base / "userdata").mkdir(parents=True)
    (base / "userdata/environment-id").write_text(ENVIRONMENT + "\n")
    socket = tmp_path / "run/tailscaled.sock"
    socket.parent.mkdir()
    socket.touch()
    state.argv = ["--base-dir", str(base), "--socket", str(socket),
                  "--state-dir", str(tmp_path / "state")]
    monkeypatch.setattr(wizard.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(wizard, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True),
        stdout=SimpleNamespace(isatty=lambda: True),
        stderr=SimpleNamespace(isatty=lambda: True, write=lambda text: sys.stderr.write(text)),
    ))
    monkeypatch.setattr("builtins.input", lambda prompt: next(state.answers))
    monkeypatch.setattr(wizard, "t3_environment", lambda port: {"environmentId": ENVIRONMENT})

    def run(command, **kwargs):
        state.calls.append((command, kwargs))
        if command[0] == "/bin/t3":
            assert command == ["/bin/t3", "pair", "--base-dir", str(base)]
            assert "capture_output" not in kwargs and "stdout" not in kwargs
            return SimpleNamespace(returncode=0)
        action = command[2:]
        if action == ["status", "--json"]:
            output = state.status
        elif action == ["serve", "status", "--json"]:
            output = state.config
        elif action == ["up", "--accept-dns=false"]:
            assert "capture_output" not in kwargs
            state.status = {"BackendState": "Running", "Self": {"DNSName": HOST + "."}}
            output = {}
        elif action == ["serve", "--bg", "--https=443", TARGET]:
            state.config = proxy_config()
            output = {}
        else:
            pytest.fail(f"Unexpected command: {command}")
        return SimpleNamespace(returncode=0, stdout=json.dumps(output))

    monkeypatch.setattr(wizard.subprocess, "run", run)
    return state


def test_complete_setup_logs_in_serves_then_pairs(wizard, flow, capsys):
    flow.status = {"BackendState": "NeedsLogin"}
    assert wizard.main(flow.argv) == 0
    actions = [command[2:] for command, _ in flow.calls[:-1]]
    assert actions.index(["up", "--accept-dns=false"]) < actions.index(
        ["serve", "--bg", "--https=443", TARGET]
    )
    assert flow.calls[-1][0][0] == "/bin/t3"
    output = capsys.readouterr().out
    assert f"Host: https://{HOST}" in output
    assert ENVIRONMENT in output
    assert "/opt/neurodesktop/t3-provider-bin/codex" in output
    assert "/opt/neurodesktop/t3-provider-bin/claude" in output


def test_rerun_reuses_login_and_matching_proxy(wizard, flow):
    flow.config = proxy_config()
    assert wizard.main(flow.argv) == 0
    assert not any("up" in cmd or "--bg" in cmd for cmd, _ in flow.calls)
    assert sum(cmd[0] == "/bin/t3" for cmd, _ in flow.calls) == 1


def test_desktop_confirmation_is_required_before_pairing(wizard, flow, capsys):
    flow.answers = iter(["", "no"])
    assert wizard.main(flow.argv) == 1
    assert not any(cmd[0] == "/bin/t3" for cmd, _ in flow.calls)
    assert "Pairing paused" in capsys.readouterr().err


@pytest.mark.parametrize("config", [
    proxy_config("http://127.0.0.1:8888"),
    {**proxy_config(), "AllowFunnel": {f"{HOST}:443": True}},
    {"TCP": {"443": {"TCPForward": "localhost:22"}}},
])
def test_existing_services_and_public_funnel_are_not_modified(wizard, flow, config):
    flow.config = config
    assert wizard.main(flow.argv) == 1
    assert all("--json" in cmd for cmd, _ in flow.calls)


def test_check_has_no_mutations_or_tokens(wizard, flow, monkeypatch):
    flow.config = proxy_config()
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Unexpected prompt"))
    monkeypatch.setattr(wizard, "private_directory", lambda _: pytest.fail("Unexpected directory change"))
    assert wizard.main([*flow.argv, "--check"]) == 0
    assert all("--json" in cmd for cmd, _ in flow.calls)


def test_wrong_t3_state_directory_stops_before_tailscale(wizard, flow, capsys):
    (Path(flow.argv[1]) / "userdata/environment-id").write_text("another-server")
    assert wizard.main(flow.argv) == 1
    assert flow.calls == []
    assert "--base-dir" in capsys.readouterr().err


def test_server_change_during_setup_does_not_mint_token(wizard, flow, monkeypatch):
    responses = iter([ENVIRONMENT, "replacement-server"])
    monkeypatch.setattr(wizard, "t3_environment", lambda _: {"environmentId": next(responses)})
    assert wizard.main(flow.argv) == 1
    assert not any(cmd[0] == "/bin/t3" for cmd, _ in flow.calls)


def test_redirected_setup_refuses_to_print_secrets(wizard, flow, monkeypatch, capsys):
    monkeypatch.setattr(wizard.sys.stdout, "isatty", lambda: False)
    assert wizard.main(flow.argv) == 1
    assert flow.calls == []
    assert "interactive terminal" in capsys.readouterr().err


def test_ctrl_c_leaves_existing_connection_unchanged(wizard, flow, monkeypatch):
    def interrupted(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr("builtins.input", interrupted)
    assert wizard.main(flow.argv) == 130
    assert flow.calls == []


@pytest.mark.parametrize("status", [
    {"BackendState": "NeedsMachineAuth"},
    {"BackendState": "Running", "Self": None},
    {"BackendState": "Running", "Self": {"DNSName": "example-tail.ts.net"}},
])
def test_incomplete_connection_or_tailnet_suffix_is_rejected(wizard, status):
    with pytest.raises(wizard.SetupError):
        wizard.device_host(status)


def test_failed_status_never_echoes_auth_url(wizard, monkeypatch, capsys):
    monkeypatch.setattr(wizard.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout='{"AuthURL":"secret-login-url"}', stderr="secret-login-url",
    ))
    with pytest.raises(wizard.SetupError) as error:
        wizard.command_json(["tailscale", "status", "--json"])
    assert "secret-login-url" not in str(error.value) + str(capsys.readouterr())


def test_missing_local_t3_explains_startup_requirement(wizard, monkeypatch):
    def failed_open(*args, **kwargs):
        raise OSError("connection refused")
    monkeypatch.setattr(wizard.urllib.request, "build_opener", lambda handler: SimpleNamespace(open=failed_open))
    with pytest.raises(wizard.SetupError, match="NEURODESKTOP_T3_CODE_ENABLE=1"):
        wizard.t3_environment(3773)


def test_daemon_start_retries_api_race_and_detaches(wizard, monkeypatch, tmp_path):
    socket = tmp_path / "ts.sock"
    process = SimpleNamespace(poll=lambda: None)
    launches = []

    def start(command, **kwargs):
        launches.append((command, kwargs))
        socket.touch()
        return process

    attempts = []

    def status(command):
        attempts.append(command)
        if len(attempts) == 1:
            raise wizard.SetupError("not ready")
        return {"BackendState": "NeedsLogin"}

    monkeypatch.setattr(wizard.subprocess, "Popen", start)
    monkeypatch.setattr(wizard, "command_json", status)
    monkeypatch.setattr(wizard.time, "sleep", lambda _: None)
    result = wizard.ensure_daemon("tailscaled", ["tailscale"], socket, tmp_path / "state")
    assert result["BackendState"] == "NeedsLogin"
    command, kwargs = launches[0]
    assert "--tun=userspace-networking" in command and "--port=0" in command
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] == kwargs["stderr"] == wizard.subprocess.DEVNULL
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
    assert len(attempts) == 2


def test_image_installs_user_invoked_command():
    dockerfile = repo_path("Dockerfile").read_text()
    assert "source=scripts/t3_tailscale_setup.py,target=/tmp/t3_tailscale_setup.py,ro" in dockerfile
    assert "install -m 0755 -o root -g users /tmp/t3_tailscale_setup.py /opt/neurodesktop/t3_tailscale_setup.py" in dockerfile
    assert "ln -s /opt/neurodesktop/t3_tailscale_setup.py /usr/local/bin/neurodesktop-t3-setup" in dockerfile
