import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

import httpx
import pytest

from test_rise_slides_image import _BidiSession, _stop, _unused_port, _wait_for_server


def _evaluate(bidi, context, expression):
    return bidi.request("script.evaluate", {
        "expression": expression, "target": {"context": context}, "awaitPromise": True,
    })["result"].get("value")


def _wait(bidi, context, expression, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _evaluate(bidi, context, expression)
        if result:
            return result
        time.sleep(0.1)
    pytest.fail("VS Code did not complete its browser action; " + str(
        _evaluate(bidi, context, "document.body.innerText.slice(-4000)")
    ))


def _keys(bidi, context, *values):
    bidi.request("input.performActions", {
        "context": context, "actions": [{"type": "key", "id": "keyboard", "actions": [
            *({"type": "keyDown", "value": value} for value in values),
            *({"type": "keyUp", "value": value} for value in reversed(values)),
        ]}],
    })


def _type(bidi, context, text):
    bidi.request("input.performActions", {
        "context": context, "actions": [{"type": "key", "id": "keyboard", "actions": [
            event for character in text for event in (
                {"type": "keyDown", "value": character},
                {"type": "keyUp", "value": character},
            )
        ]}],
    })


def run_vscode_edit_workflow(tmp_path):
    (tmp_path / "acceptance.txt").write_text("original document\n")
    port, firefox_port = _unused_port(), _unused_port()
    token = "vscode-browser-acceptance"
    prefix = "/user/acceptance/"
    base = f"http://127.0.0.1:{port}{prefix}"
    server_log_path = tmp_path / "jupyter-server.log"
    server_log = server_log_path.open("w")
    server = firefox = bidi = firefox_log = None
    try:
        firefox_log = (tmp_path / "firefox.log").open("w")
        server = subprocess.Popen([
            "/opt/conda/bin/jupyter", "server", "--config=/etc/jupyter/jupyter_notebook_config.py",
            "--no-browser", "--ServerApp.allow_root=True",
            f"--ServerApp.port={port}", "--ServerApp.port_retries=0",
            f"--ServerApp.root_dir={tmp_path}", f"--ServerApp.base_url={prefix}",
            f"--FileContentsManager.preferred_dir={tmp_path}",
            f"--IdentityProvider.token={token}",
        ], stdout=server_log, stderr=subprocess.STDOUT)
        profile = tmp_path / "firefox-profile"
        profile.mkdir()
        firefox_home = tmp_path / "firefox-home"
        firefox_home.mkdir()
        firefox = subprocess.Popen([
            "/usr/bin/firefox", "--headless", "--profile", str(profile),
            f"--remote-debugging-port={firefox_port}", "about:blank",
        ], env={**os.environ, "HOME": str(firefox_home)}, stdout=firefox_log,
           stderr=subprocess.STDOUT)
        _wait_for_server(base + "api/status?token=" + token, server, server_log_path)
        denied = httpx.get(base + "vscode/", follow_redirects=False, timeout=30)
        assert denied.status_code in {302, 403}, "The VS Code proxy allowed anonymous access"
        bidi = _BidiSession(f"ws://127.0.0.1:{firefox_port}/session", firefox)
        bidi.request("session.new", {"capabilities": {}})
        context = bidi.request("browsingContext.create", {"type": "tab"})["context"]
        bidi.request("browsingContext.navigate", {
            "context": context,
            "url": base + "vscode/?" + urllib.parse.urlencode({"token": token, "folder": str(tmp_path)}),
            "wait": "complete",
        })
        _wait(bidi, context, "Boolean(document.querySelector('.monaco-workbench'))", timeout=90)
        point = json.loads(_wait(bidi, context, """(() => {
            const trust = [...document.querySelectorAll('button, [role="button"], .monaco-button')]
                .find(node => node.textContent.includes('Yes, I trust the authors'));
            if (trust && trust.getClientRects().length) {
                trust.click();
                return false;
            }
            const label = [...document.querySelectorAll('.monaco-list-row .label-name')]
                .find(node => node.textContent === 'acceptance.txt');
            if (!label) return false;
            const row = label.closest('.monaco-list-row');
            const rect = label.getBoundingClientRect();
            const x = Math.round(rect.left + rect.width / 2);
            const y = Math.round(rect.top + rect.height / 2);
            if (!rect.width || !rect.height || !row.contains(document.elementFromPoint(x, y))) {
                return false;
            }
            return JSON.stringify({x, y});
        })()"""))
        bidi.request("input.performActions", {
            "context": context, "actions": [{
                "type": "pointer", "id": "vscode-file", "parameters": {"pointerType": "mouse"},
                "actions": [
                    {"type": "pointerMove", "duration": 0, "origin": "viewport", **point},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pointerUp", "button": 0},
                    {"type": "pause", "duration": 100},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pointerUp", "button": 0},
                ],
            }],
        })
        _wait(bidi, context, "[...document.querySelectorAll('.view-lines')].some(node => /original\\s+document/.test(node.textContent))")
        _evaluate(bidi, context, "document.querySelector('.monaco-editor textarea').focus()")
        _keys(bidi, context, "\ue009", "a")
        expected = "saved from the authenticated vscode editor"
        _type(bidi, context, expected)
        _keys(bidi, context, "\ue009", "s")
        deadline = time.monotonic() + 20
        contents = None
        while time.monotonic() < deadline:
            request = urllib.request.Request(base + "api/contents/acceptance.txt?content=1", headers={
                "Authorization": "token " + token,
            })
            with urllib.request.urlopen(request, timeout=5) as response:
                contents = json.load(response)
            if contents["content"] == expected:
                break
            time.sleep(0.1)
        assert contents["content"] == expected, "The editor did not save its changes through the Jupyter proxy"
    finally:
        if bidi is not None:
            bidi.close()
        if firefox is not None:
            _stop(firefox)
        if server is not None:
            _stop(server)
        if firefox_log is not None:
            firefox_log.close()
        server_log.close()
