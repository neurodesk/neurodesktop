"""Checkout tests for the Kasm image's local Jupyter launcher."""

import os
import subprocess
from urllib.parse import parse_qs, urlparse

from testlib import load_source_module, repo_path


launcher = load_source_module(
    "kasm_open_jupyter", "/opt/neurodesktop/kasm-open-jupyter",
    "config/kasm/open_jupyter.py",
)


def test_jupyter_link_preserves_base_path_and_encodes_token():
    url = launcher.lab_url({
        "url": "http://localhost:8888/user/researcher/",
        "token": "a+b&c=d",
    })
    parsed = urlparse(url)
    assert parsed.path == "/user/researcher/lab"
    assert parse_qs(parsed.query) == {"token": ["a+b&c=d"]}


def test_jupyter_launcher_selects_its_local_server(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=(
            '{"url":"http://localhost:9999/","port":9999,"token":"wrong"}\n'
            '{"url":"http://localhost:8888/","port":8888,"token":"session"}\n'
        ))

    monkeypatch.setattr(launcher.subprocess, "run", run)
    launcher.main()
    assert calls[-1] == [
        "neurodesktop-firefox", "http://localhost:8888/lab?token=session",
    ]


def test_image_refuses_an_unset_session_password():
    env = {key: value for key, value in os.environ.items() if key != "VNC_PW"}
    result = subprocess.run(
        ["bash", str(repo_path("config/kasm/entrypoint.sh"))],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "Set VNC_PW" in result.stderr
