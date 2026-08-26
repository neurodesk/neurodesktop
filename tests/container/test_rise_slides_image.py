"""Browser regression tests for the installed standalone RISE application."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import nbformat
import pytest
import websocket


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_server(
    url: str, process: subprocess.Popen[str], log_path: Path
) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(
                "Jupyter Server exited before serving the RISE application:\n"
                + log_path.read_text(encoding="utf-8")
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    pytest.fail("Jupyter Server did not become ready")


class _BidiSession:
    def __init__(self, url: str, firefox: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + 30
        while True:
            if firefox.poll() is not None:
                pytest.fail("Firefox exited before opening its WebDriver BiDi endpoint")
            try:
                self.websocket = websocket.create_connection(
                    url,
                    timeout=1,
                    suppress_origin=True,
                )
                self.websocket.settimeout(30)
                break
            except (OSError, websocket.WebSocketException):
                if time.monotonic() >= deadline:
                    pytest.fail("Firefox did not open its WebDriver BiDi endpoint")
                time.sleep(0.1)
        self._next_id = 1
        self.events: list[dict] = []

    def request(self, method: str, params: dict) -> dict:
        request_id = self._next_id
        self._next_id += 1
        self.websocket.send(
            json.dumps({"id": request_id, "method": method, "params": params})
        )
        while True:
            message = json.loads(self.websocket.recv())
            if message.get("id") != request_id:
                self.events.append(message)
                continue
            if message.get("type") == "error":
                pytest.fail(f"WebDriver BiDi {method} failed: {message}")
            return message["result"]

    def close(self) -> None:
        self.websocket.close()


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _wait_for_body_text(
    bidi: _BidiSession, context: str, expected: str
) -> None:
    deadline = time.monotonic() + 20
    body_text = ""
    while time.monotonic() < deadline:
        result = bidi.request(
            "script.evaluate",
            {
                "expression": "document.body.innerText",
                "target": {"context": context},
                "awaitPromise": True,
            },
        )
        body_text = result["result"]["value"]
        if expected in body_text:
            return
        time.sleep(0.1)
    browser_errors = [
        event["params"]["text"]
        for event in bidi.events
        if event.get("method") == "log.entryAdded"
        and event["params"].get("level") == "error"
    ]
    pytest.fail(
        f"RISE did not render {expected!r}; body text was {body_text!r}; "
        f"browser errors were {browser_errors!r}"
    )


def test_plain_notebook_renders_as_a_rise_slide(tmp_path: Path) -> None:
    """A user opening ``/rise/<notebook>`` sees the notebook's first slide."""
    notebook = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_markdown_cell(
                "# RISE browser regression",
                metadata={"slideshow": {"slide_type": "slide"}},
            )
        ]
    )
    nbformat.write(notebook, tmp_path / "slides.ipynb")

    server_port = _unused_port()
    firefox_port = _unused_port()
    token = "rise-browser-regression"
    server_log_path = tmp_path / "jupyter-server.log"
    server_log = server_log_path.open("w", encoding="utf-8")
    firefox_log = (tmp_path / "firefox.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        [
            "/opt/conda/bin/jupyter",
            "server",
            "--no-browser",
            "--ServerApp.allow_root=True",
            f"--ServerApp.port={server_port}",
            "--ServerApp.port_retries=0",
            f"--ServerApp.root_dir={tmp_path}",
            f"--FileContentsManager.preferred_dir={tmp_path}",
            f"--IdentityProvider.token={token}",
        ],
        stdout=server_log,
        stderr=subprocess.STDOUT,
        text=True,
    )

    firefox_home = tmp_path / "firefox-home"
    firefox_profile = tmp_path / "firefox-profile"
    firefox_home.mkdir()
    firefox_profile.mkdir()
    firefox_env = os.environ.copy()
    firefox_env["HOME"] = str(firefox_home)
    firefox = subprocess.Popen(
        [
            "/usr/bin/firefox",
            "--headless",
            "--profile",
            str(firefox_profile),
            f"--remote-debugging-port={firefox_port}",
            "about:blank",
        ],
        env=firefox_env,
        stdout=firefox_log,
        stderr=subprocess.STDOUT,
        text=True,
    )

    bidi = None
    try:
        _wait_for_server(
            f"http://127.0.0.1:{server_port}/api/status?token={token}",
            server,
            server_log_path,
        )
        bidi = _BidiSession(f"ws://127.0.0.1:{firefox_port}/session", firefox)
        bidi.request("session.new", {"capabilities": {}})
        bidi.request("session.subscribe", {"events": ["log.entryAdded"]})
        context = bidi.request("browsingContext.create", {"type": "tab"})[
            "context"
        ]
        bidi.request(
            "browsingContext.navigate",
            {
                "context": context,
                "url": (
                    f"http://127.0.0.1:{server_port}/rise/slides.ipynb"
                    f"?token={token}"
                ),
                "wait": "complete",
            },
        )
        _wait_for_body_text(bidi, context, "RISE browser regression")
    finally:
        if bidi is not None:
            bidi.close()
        _stop(firefox)
        _stop(server)
        firefox_log.close()
        server_log.close()
