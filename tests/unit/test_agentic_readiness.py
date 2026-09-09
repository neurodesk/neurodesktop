"""Check readiness without accessing a real account session."""

import json

import pytest

from testlib import load_source_module


@pytest.fixture
def readiness():
    return load_source_module("agentic_readiness_test", "/opt/check_agentic_readiness.py",
                              ".github/scripts/check_agentic_readiness.py")


@pytest.fixture
def auth(tmp_path):
    directory = tmp_path / "auth"
    directory.mkdir(mode=0o700)
    path = directory / "auth.json"
    path.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "dummy" * 10}}))
    path.chmod(0o600)
    return directory


def test_readiness_accepts_private_subscription_storage(readiness, auth):
    assert readiness.check_auth_directory(auth) == auth


def test_readiness_reports_missing_configured_auth_directory(readiness, tmp_path):
    auth = tmp_path / "missing-auth-directory"

    with pytest.raises(ValueError) as raised:
        readiness.check_auth_directory(auth)

    message = str(raised.value)
    assert "AGENTIC_CODEX_HOME" in message
    assert "directory does not exist" in message
    assert str(auth) in message
    assert "unauthenticated" not in message


def test_readiness_reports_missing_saved_login_data(readiness, auth):
    (auth / "auth.json").unlink()

    with pytest.raises(ValueError) as raised:
        readiness.check_auth_directory(auth)

    message = str(raised.value)
    assert "auth.json" in message
    assert "saved login data is missing" in message
    assert str(auth) in message


@pytest.mark.parametrize("target", ["directory", "file"])
def test_readiness_rejects_shared_credentials(readiness, auth, target):
    (auth if target == "directory" else auth / "auth.json").chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        readiness.check_auth_directory(auth)


def test_readiness_rejects_symlink_auth(readiness, auth):
    path = auth / "auth.json"
    path.rename(auth / "original")
    path.symlink_to(auth / "original")
    with pytest.raises(ValueError):
        readiness.check_auth_directory(auth)


@pytest.mark.parametrize("token", ["short", 42, None])
def test_readiness_rejects_auth_without_a_usable_subscription_token(readiness, auth, token):
    (auth / "auth.json").write_text(json.dumps({
        "auth_mode": "chatgpt", "tokens": {"access_token": token},
    }))
    with pytest.raises(ValueError, match="usable subscription token"):
        readiness.check_auth_directory(auth)


def test_readiness_refuses_mismatched_daemon_mount(readiness, auth, monkeypatch, capsys):
    monkeypatch.setenv("AGENTIC_CODEX_HOME", str(auth))
    from types import SimpleNamespace
    monkeypatch.setattr(readiness.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="a different mounted credential hash", stderr=""))
    with pytest.raises(ValueError, match="same authentication"):
        readiness.main()
    assert "dummy" not in capsys.readouterr().out
