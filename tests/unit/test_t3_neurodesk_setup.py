"""Exercise the guided flow without authenticating or creating real credentials."""

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from testlib import load_source_module, repo_path


HOST = "neurodesktop.example-tail.ts.net"
TARGET = "http://127.0.0.1:3773"
ENVIRONMENT = "test-environment-id"


CONTAINER_HOST = "17c6b37bfe33.example-tail.ts.net"
CODE = "TESTPAIRCODE"
PAIR_URL = f"https://{HOST}/pair#token={CODE}"


def proxy_config(target=TARGET, host=HOST):
    return {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {f"{host}:443": {"Handlers": {"/": {"Proxy": target}}}},
    }


def device_name(label):
    return f"{label}.{HOST.split('.', 1)[1]}"


@pytest.fixture
def wizard():
    return load_source_module(
        "t3_setup", "/opt/neurodesktop/t3_neurodesk_setup.py",
        "scripts/t3_neurodesk_setup.py",
    )


@pytest.fixture
def flow(wizard, monkeypatch, tmp_path, capsys):
    state = SimpleNamespace(
        status={"BackendState": "Running", "Self": {"DNSName": HOST + "."}},
        config={}, calls=[], answers=iter([""]),
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
            assert command[:6] == ["/bin/t3", "--log-level=warn", "auth", "pairing", "create",
                                   "--base-dir"]
            assert command[6] == str(base) and "--json" in command
            # The link must be built for the name the device holds now.
            served = state.status["Self"]["DNSName"].rstrip(".")
            assert command[command.index("--base-url") + 1] == f"https://{served}"
            # T3's own diagnostics must not reach the terminal alongside the link.
            assert kwargs["stderr"] is wizard.subprocess.DEVNULL
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"credential": CODE, "pairUrl": f"https://{served}/pair#token={CODE}"}))
        action = command[2:]
        if action == ["status", "--json"]:
            output = state.status
        elif action == ["serve", "status", "--json"]:
            output = state.config
        elif action[:2] == ["up", "--accept-dns=false"]:
            assert "capture_output" not in kwargs
            name = HOST.split(".")[0]
            for flag in action[2:]:
                assert flag.startswith("--hostname=")
                name = flag.split("=", 1)[1]
            state.status = {"BackendState": "Running", "Self": {"DNSName": device_name(name) + "."}}
            output = {}
        elif len(action) == 2 and action[0] == "set" and action[1].startswith("--hostname="):
            state.status = {**state.status,
                            "Self": {"DNSName": device_name(action[1].split("=", 1)[1]) + "."}}
            output = {}
        elif action == ["serve", "--bg", "--https=443", TARGET]:
            # Serve binds the name the device holds now, not the one it had before.
            state.config = proxy_config(host=state.status["Self"]["DNSName"].rstrip("."))
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
    assert actions.index(["up", "--accept-dns=false", "--hostname=neurodesktop"]) < actions.index(
        ["serve", "--bg", "--https=443", TARGET]
    )
    assert flow.calls[-1][0][0] == "/bin/t3"
    output = capsys.readouterr().out
    assert PAIR_URL in output
    assert ENVIRONMENT in output
    # The desktop cannot reach the container address T3 would print itself.
    assert "172." not in output and "127.0.0.1:3773/pair" not in output


def test_rerun_reuses_login_and_matching_proxy(wizard, flow):
    flow.config = proxy_config()
    assert wizard.main(flow.argv) == 0
    assert not any("up" in cmd or "--bg" in cmd or "set" in cmd for cmd, _ in flow.calls)
    assert sum(cmd[0] == "/bin/t3" for cmd, _ in flow.calls) == 1


def test_desktop_confirmation_is_required_before_pairing(wizard, flow, monkeypatch, capsys):
    def interrupted(prompt):
        assert "Press Enter" in prompt
        raise KeyboardInterrupt()

    flow.config = proxy_config()
    monkeypatch.setattr("builtins.input", interrupted)
    assert wizard.main(flow.argv) == 130
    assert not any(cmd[0] == "/bin/t3" for cmd, _ in flow.calls)
    assert CODE not in capsys.readouterr().out


@pytest.mark.parametrize("response", [
    SimpleNamespace(returncode=1, stdout=""),
    # A link for an address only the container can reach is never offered.
    SimpleNamespace(returncode=0, stdout=f'{{"pairUrl": "http://172.17.0.2:3773/pair#token={CODE}"}}'),
    SimpleNamespace(returncode=0, stdout='{"credential": "code-without-a-link"}'),
    SimpleNamespace(returncode=0, stdout="not json"),
])
def test_failed_pairing_never_echoes_t3_output(wizard, monkeypatch, capsys, response):
    monkeypatch.setattr(wizard.subprocess, "run", lambda *a, **k: response)
    with pytest.raises(wizard.SetupError) as error:
        wizard.pairing_url("/bin/t3", Path("/home/jovyan/.t3"), f"https://{HOST}")
    assert "172.17.0.2" not in str(error.value) + str(capsys.readouterr())


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
    flow.config = proxy_config()
    monkeypatch.setattr("builtins.input", interrupted)
    assert wizard.main(flow.argv) == 130
    # Only read-only status queries ran before the desktop check.
    assert all("--json" in cmd for cmd, _ in flow.calls)


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
    with pytest.raises(wizard.SetupError, match="starts automatically with Jupyter"):
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
    assert "source=scripts/t3_neurodesk_setup.py,target=/tmp/t3_neurodesk_setup.py,ro" in dockerfile
    assert "install -m 0755 -o root -g users /tmp/t3_neurodesk_setup.py /opt/neurodesktop/t3_neurodesk_setup.py" in dockerfile
    assert "ln -s /opt/neurodesktop/t3_neurodesk_setup.py /usr/local/bin/t3_neurodesk_setup" in dockerfile


@pytest.mark.parametrize("check", [False, True])
def test_an_endpoint_the_device_cannot_answer_is_never_offered(wizard, flow, capsys, check):
    flow.config = proxy_config()
    flow.status["Self"]["DNSName"] = CONTAINER_HOST + "."
    argv = [*flow.argv, "--tailscale-hostname", "", *(["--check"] if check else [])]
    assert wizard.main(argv) == (1 if check else 0)
    printed = capsys.readouterr()
    # The unreachable name is named as superseded, never handed to the desktop.
    assert f"https://{HOST}/pair#" not in printed.out
    if check:
        assert HOST in printed.err
        assert all("--json" in command for command, _ in flow.calls)
    else:
        assert HOST in printed.out
        assert ["serve", "--bg", "--https=443", TARGET] in [c[2:] for c, _ in flow.calls]
        assert f"https://{CONTAINER_HOST}/pair#token={CODE}" in printed.out


def test_every_superseded_endpoint_is_reported_and_replaced(wizard, flow, capsys):
    flow.config = proxy_config()
    flow.config["Web"]["second.example-tail.ts.net:443"] = flow.config["Web"][f"{HOST}:443"]
    flow.status["Self"]["DNSName"] = CONTAINER_HOST + "."
    assert wizard.main([*flow.argv, "--tailscale-hostname", ""]) == 0
    output = capsys.readouterr().out
    reported = set(re.findall(r"[a-z0-9-]+\.example-tail\.ts\.net", output))
    assert {HOST, "second.example-tail.ts.net"} <= reported
    assert f"https://{CONTAINER_HOST}/pair#token={CODE}" in output


def test_container_id_hostname_is_replaced_by_a_stable_device_name(wizard, flow, capsys):
    flow.status["Self"]["DNSName"] = CONTAINER_HOST + "."
    assert wizard.main(flow.argv) == 0
    assert ["set", "--hostname=neurodesktop"] in [c[2:] for c, _ in flow.calls]
    assert PAIR_URL in capsys.readouterr().out


def test_a_name_tailscale_disambiguated_is_kept(wizard, flow):
    flow.status["Self"]["DNSName"] = device_name("neurodesktop-1") + "."
    flow.config = proxy_config(host=device_name("neurodesktop-1"))
    assert wizard.main(flow.argv) == 0
    assert not any("set" in command for command, _ in flow.calls)


def test_an_invalid_stable_device_name_is_refused(wizard, flow):
    with pytest.raises(SystemExit):
        wizard.main([*flow.argv, "--tailscale-hostname", "not a label"])
