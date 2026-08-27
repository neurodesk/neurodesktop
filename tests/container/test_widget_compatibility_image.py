"""Installed widget-manager compatibility with server-side notebook execution."""

import importlib.metadata
import json
import os
import re
import socket
import subprocess
import sys
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
                "Jupyter Server exited before serving the widget notebook:\n"
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

    def evaluate(self, context: str, expression: str):
        result = self.request(
            "script.evaluate",
            {
                "expression": expression,
                "target": {"context": context},
                "awaitPromise": True,
            },
        )
        return result["result"].get("value")

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


def _click_dom_element(
    bidi: _BidiSession, context: str, element_expression: str
) -> None:
    point = json.loads(
        bidi.evaluate(
            context,
            "JSON.stringify((() => {"
            f"const node = {element_expression};"
            "if (!node) return null;"
            "const rect = node.getBoundingClientRect();"
            "return {x: Math.round(rect.left + rect.width / 2),"
            "y: Math.round(rect.top + rect.height / 2)};"
            "})())",
        )
    )
    assert point is not None
    bidi.request(
        "input.performActions",
        {
            "context": context,
            "actions": [
                {
                    "type": "pointer",
                    "id": "mouse",
                    "parameters": {"pointerType": "mouse"},
                    "actions": [
                        {
                            "type": "pointerMove",
                            "duration": 0,
                            "origin": "viewport",
                            "x": point["x"],
                            "y": point["y"],
                        },
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ],
        },
    )


def _wait_for_expression(
    bidi: _BidiSession,
    context: str,
    expression: str,
    *,
    timeout: float = 30,
) -> object:
    deadline = time.monotonic() + timeout
    last_value = None
    while time.monotonic() < deadline:
        last_value = bidi.evaluate(context, expression)
        if last_value:
            return last_value
        time.sleep(0.1)
    browser_errors = [
        event["params"]["text"]
        for event in bidi.events
        if event.get("method") == "log.entryAdded"
        and event["params"].get("level") == "error"
    ]
    browser_state = bidi.evaluate(
        context,
        "JSON.stringify({"
        "body: document.body.innerText.slice(-2000),"
        "outputs: [...document.querySelectorAll('.jp-OutputArea-output')]"
        ".map(node => node.outerHTML.slice(0, 2000))"
        "})",
    )
    pytest.fail(
        f"Browser condition did not become true; last value was {last_value!r}; "
        f"browser errors were {browser_errors!r}; state was {browser_state}"
    )


def test_widget_manager_waits_for_a_late_model_registration():
    """Yjs output may arrive before the matching kernel ``comm_open``.

    ``jupyter-server-documents`` delivers notebook output over a different
    websocket from widget comms.  The shipped frontend must therefore retry a
    missing model briefly instead of permanently rendering ``model not found``
    (or waiting forever at ``Loading widget...``).
    """
    assert importlib.metadata.version("ipykernel") == "6.31.0"
    assert importlib.metadata.version("ipywidgets") == "8.1.9"
    assert importlib.metadata.version("jupyterlab_widgets") == "3.0.17"

    labextension = (
        Path(sys.prefix)
        / "share/jupyter/labextensions/@jupyter-widgets/jupyterlab-manager"
    )
    package = json.loads((labextension / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == "5.0.16"

    bundles = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((labextension / "static").glob("*.js"))
    )
    late_model_retry = re.compile(
        r"async get_model\([^)]*\).*?Date\.now\(\).*?"
        r"setTimeout\([^,]+,100\).*?widget model not found",
        re.DOTALL,
    )
    assert late_model_retry.search(bundles), (
        "the installed widget manager still fails immediately when a widget "
        "view reaches the browser before its model"
    )
    assert "neurodesktop-widget-model-retry" in bundles
    assert "Date.now()-o<1e4" in bundles


def test_server_side_execution_renders_streams_and_a_widget(tmp_path: Path) -> None:
    """Stream updates stay valid before a server-executed widget renders."""
    notebook = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_code_cell(
                "import asyncio\n"
                "import sys\n"
                "import ipywidgets as widgets\n"
                "from IPython.display import display\n"
                "for fragment in range(20):\n"
                "    sys.stdout.write(f'\\rstream-fragment-{fragment:02d}')\n"
                "    sys.stdout.flush()\n"
                "    await asyncio.sleep(0.05)\n"
                "print()\n"
                "model_id = 'delayed-hbox-model'\n"
                "async def open_model_later():\n"
                "    await asyncio.sleep(3)\n"
                "    return widgets.HBox([\n"
                "        widgets.IntSlider(value=42),\n"
                "        widgets.Label(value='nested'),\n"
                "    ], model_id=model_id)\n"
                "delayed_model_task = asyncio.create_task(open_model_later())\n"
                "display({\n"
                "    'application/vnd.jupyter.widget-view+json': {\n"
                "        'version_major': 2,\n"
                "        'version_minor': 0,\n"
                "        'model_id': model_id,\n"
                "    }\n"
                "}, raw=True)"
            ),
            nbformat.v4.new_markdown_cell("Widget regression end."),
        ],
        metadata={
            "kernelspec": {
                "display_name": "Python [conda env:base] *",
                "language": "python",
                "name": "conda-base-py",
            }
        },
    )
    nbformat.write(notebook, tmp_path / "widget.ipynb")

    server_port = _unused_port()
    firefox_port = _unused_port()
    token = "widget-browser-regression"
    server_log_path = tmp_path / "jupyter-server.log"
    server_log = server_log_path.open("w", encoding="utf-8")
    firefox_log = (tmp_path / "firefox.log").open("w", encoding="utf-8")
    server_home = tmp_path / "server-home"
    server_home.mkdir()
    server_env = os.environ.copy()
    server_env["HOME"] = str(server_home)
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
        env=server_env,
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
                    f"http://127.0.0.1:{server_port}/lab/tree/widget.ipynb"
                    f"?token={token}"
                ),
                "wait": "complete",
            },
        )
        _wait_for_expression(
            bidi,
            context,
            "Boolean(document.querySelector('.jp-Notebook .jp-CodeCell'))",
        )
        _wait_for_expression(
            bidi,
            context,
            "document.body.innerText.includes("
            "'Python [conda env:base] * | Idle'"
            ")",
        )
        assert bidi.evaluate(
            context,
            "document.querySelector('.jp-Notebook .jp-CodeCell').click(); true",
        )
        _click_dom_element(
            bidi,
            context,
            "[...document.querySelectorAll('[role=menuitem]')]"
            ".find(node => node.textContent.trim() === 'Run')",
        )
        _wait_for_expression(
            bidi,
            context,
            "[...document.querySelectorAll('[role=menuitem]')]"
            ".some(node => node.textContent.trim().startsWith("
            "'Run Selected Cell') && !node.textContent.includes('and'))",
        )
        _click_dom_element(
            bidi,
            context,
            "[...document.querySelectorAll('[role=menuitem]')]"
            ".find(node => node.textContent.trim().startsWith("
            "'Run Selected Cell') && !node.textContent.includes('and'))",
        )
        _wait_for_expression(
            bidi,
            context,
            "Boolean("
            "document.querySelector('.jupyter-widgets.widget-box') || "
            "document.body.innerText.includes('model not found') || "
            "document.querySelector("
            "'.jp-OutputArea-output[data-mime-type=\"text/plain\"]'"
            ")"
            ")",
            timeout=45,
        )
        output = json.loads(
            bidi.evaluate(
                context,
                "JSON.stringify((() => {"
                "return {"
                "modelError: document.body.innerText.includes("
                "'model not found'"
                "),"
                "plainTextFallback: [...document.querySelectorAll("
                "'.jp-OutputArea-output[data-mime-type=\"text/plain\"]'"
                ")].some(node => node.textContent.includes('VBox(')),"
                "streamText: document.body.innerText.includes("
                "'stream-fragment-19'),"
                "widgetBoxes: document.querySelectorAll("
                "'.jupyter-widgets.widget-box'"
                ").length"
                "};"
                "})())",
            )
        )
        assert output == {
            "modelError": False,
            "plainTextFallback": False,
            "streamText": True,
            "widgetBoxes": 1,
        }
        browser_errors = [
            event["params"]["text"]
            for event in bidi.events
            if event.get("method") == "log.entryAdded"
            and event["params"].get("level") == "error"
        ]
        stream_output_errors = [
            error
            for error in browser_errors
            if "appendStreamOutput" in error or "insert is not a function" in error
        ]
        assert not stream_output_errors, stream_output_errors
    finally:
        if bidi is not None:
            bidi.close()
        _stop(firefox)
        _stop(server)
        firefox_log.close()
        server_log.close()
