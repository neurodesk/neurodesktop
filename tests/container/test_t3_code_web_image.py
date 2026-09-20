"""Real T3 UI, pairing and Jupyter-prefixed browser transport."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

import websocket

import pytest

from test_rise_slides_image import _BidiSession, _unused_port, _wait_for_server, _stop


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def assert_jupyter_auth_required(prefix):
    client = urllib.request.build_opener(NoRedirect)
    for path in ("neurodesk-t3/", "neurodesk-t3/_adapter.js", "neurodesk-t3-status"):
        with pytest.raises(urllib.error.HTTPError) as error:
            client.open(prefix + path, timeout=5)
        assert error.value.code in {302, 403}
    request = urllib.request.Request(prefix + "neurodesk-t3/_session", data=b"", method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        client.open(request, timeout=5)
    assert error.value.code == 403
    with pytest.raises(websocket.WebSocketBadStatusException) as error:
        websocket.create_connection(prefix.replace("http:", "ws:") + "neurodesk-t3/ws", timeout=5)
    assert error.value.status_code == 403


def evaluate(bidi, context, expression):
    result = bidi.request("script.evaluate", {
        "expression": expression, "target": {"context": context}, "awaitPromise": True,
    })
    assert result["type"] == "success", str(result)[:1500]
    return result.get("result", {}).get("value")


def wait_text(bidi, context, text):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        body = evaluate(bidi, context, "document.body?.innerText ?? ''")
        if text in body:
            return
        time.sleep(.25)
    errors = [e["params"].get("text", "") for e in bidi.events if e.get("method") == "log.entryAdded"]
    pytest.fail(f"Missing {text!r}. Body: {body[:2500]}. Browser: {errors[-10:]}")


def wait_connected(bidi, context):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if not evaluate(bidi, context, "typeof window.__neurodeskT3Target === 'function'"):
            time.sleep(.25)
            continue
        connected = evaluate(bidi, context, """(async () => {
            const session = await (await fetch('/api/auth/session')).json();
            return session.authenticated === true &&
                window.__t3TestSockets.some(socket => socket.url.includes('/neurodesk-t3/ws') && socket.readyState === 1) &&
                !document.body.innerText.includes('Pair with this environment') &&
                document.body.innerText.trim().length > 0;
        })()""")
        if connected:
            return
        time.sleep(.25)
    pytest.fail("T3 did not reach a paired session with a connected WebSocket: " +
                evaluate(bidi, context, "document.body.innerText").strip()[:1800])



@pytest.mark.parametrize("base,hub_xsrf", [("/", False), ("/user/t3-test/", False), ("/user/t3-test/", True)])
def test_t3_web_in_jupyter(tmp_path: Path, base, hub_xsrf):
    server_port, t3_port, browser_port = _unused_port(), _unused_port(), _unused_port()
    origin = f"http://127.0.0.1:{server_port}"
    prefix = origin + base
    token = "t3-browser-test-jupyter"
    environment = {**os.environ, "HOME": str(tmp_path),
                   "NEURODESKTOP_T3_CODE_PORT": str(t3_port)}
    log_path = tmp_path / "jupyter.log"
    profile = tmp_path / "firefox-profile"
    profile.mkdir()
    config = tmp_path / "jupyter_server_config.py"
    config.write_text("""
from jupyterhub._xsrf_utils import _needs_check_xsrf
from jupyter_server.base.handlers import JupyterHandler
from neurodesk_t3_code.web import T3ProxyHandler
from tornado.web import RequestHandler

# Exercise Hub's cookie-authenticated GET policy with the actual browser module
# graph. Standalone Jupyter otherwise skips this check for all GET requests.
_check_xsrf = JupyterHandler.check_xsrf_cookie
def hub_check_xsrf(self):
    if self.request.method in {"GET", "HEAD"}:
        if _needs_check_xsrf(self):
            # Keep standalone Jupyter's token format; only borrow Hub's GET policy.
            RequestHandler.check_xsrf_cookie(self)
    else:
        _check_xsrf(self)
JupyterHandler.check_xsrf_cookie = hub_check_xsrf
_prepare = T3ProxyHandler.prepare
async def hub_prepare(self, *args, **kwargs):
    await _prepare(self, *args, **kwargs)
    if self.current_user and not self.token_authenticated:
        self.check_xsrf_cookie()
T3ProxyHandler.prepare = hub_prepare
""" if hub_xsrf else "")
    with log_path.open("w") as log, (tmp_path / "firefox.log").open("w") as browser_log:
        server = subprocess.Popen([
            "jupyter", "lab", "--no-browser", "--LabApp.expose_app_in_browser=True", f"--ServerApp.port={server_port}",
            "--ServerApp.port_retries=0", f"--ServerApp.base_url={base}",
            f"--config={config}",
            f"--ServerApp.root_dir={tmp_path}", f"--FileContentsManager.preferred_dir={tmp_path}", f"--IdentityProvider.token={token}",
            '--ServerApp.jpserver_extensions={"jupyterlab":True,"neurodesk_t3_code":True}',
        ], env=environment, stdout=log, stderr=subprocess.STDOUT)
        browser = subprocess.Popen([
            "/usr/bin/firefox", "--headless", "--profile", str(profile),
            f"--remote-debugging-port={browser_port}", "about:blank",
        ], env=environment, stdout=browser_log, stderr=subprocess.STDOUT)
        bidi = None
        try:
            _wait_for_server(prefix + "api/status?token=" + token, server, log_path)
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                with urllib.request.urlopen(prefix + "neurodesk-t3-status?token=" + token) as response:
                    state = json.load(response)
                if state["state"] == "ready":
                    break
                time.sleep(.2)
            assert state["state"] == "ready", state
            assert_jupyter_auth_required(prefix)
            bidi = _BidiSession(f"ws://127.0.0.1:{browser_port}/session", browser)
            bidi.request("session.new", {"capabilities": {}})
            bidi.request("session.subscribe", {"events": ["log.entryAdded"]})
            bidi.request("script.addPreloadScript", {"functionDeclaration": """() => {
                window.__t3TestSockets = [];
                const Socket = window.WebSocket;
                window.WebSocket = class extends Socket {
                    constructor(...args) { super(...args); window.__t3TestSockets.push(this); }
                };
            }"""})
            context = bidi.request("browsingContext.create", {"type": "tab"})["context"]
            parent_context = context
            bidi.request("browsingContext.navigate", {"context": context,
                "url": prefix + "lab?token=" + token, "wait": "interactive"})
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if evaluate(bidi, context, "Boolean(window.jupyterapp?.commands.hasCommand('neurodesk-launcher:open-t3-code'))"):
                    break
                time.sleep(.25)
            evaluate(bidi, context, "window.jupyterapp.commands.execute('neurodesk-launcher:open-t3-code')")
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                tree = bidi.request("browsingContext.getTree", {"root": parent_context})["contexts"][0]
                frames = [frame for frame in tree.get("children", []) if "/neurodesk-t3/" in frame["url"]]
                if frames:
                    context = frames[0]["context"]
                    break
                time.sleep(.2)
            assert context != parent_context, "T3 launcher did not open its iframe"
            # The launcher establishes the session before loading T3. No terminal
            # command, token, or form submission should be needed in this browser.
            wait_connected(bidi, context)
            # Reload reconstructs the app using the scoped session cookie.
            bidi.request("browsingContext.navigate", {"context": context,
                "url": prefix + "neurodesk-t3/", "wait": "interactive"})
            wait_connected(bidi, context)
            evaluate(bidi, parent_context, "window.jupyterapp.commands.execute('neurodesk-launcher:open-t3-code')")
            assert evaluate(bidi, parent_context, "document.querySelectorAll('iframe[title=\"scigent.ai\"]').length") == 1
            assert evaluate(bidi, parent_context,
                "fetch(" + json.dumps(base + "neurodesk-t3/api/auth/browser-session") +
                ",{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r => r.status)") == 403
            assert evaluate(bidi, parent_context,
                "fetch(" + json.dumps(base + "neurodesk-t3/_session") +
                ",{method:'POST'}).then(r => r.status)") == 403
            assert evaluate(bidi, parent_context,
                "fetch(" + json.dumps(base + "api/status") + ").then(r => r.status)") == 200
            evaluate(bidi, parent_context, "[...window.jupyterapp.shell.widgets('main')].find(widget => widget.id === 'neurodesk-t3-code').dispose()")
            with urllib.request.urlopen(prefix + "neurodesk-t3-status?token=" + token) as response:
                assert json.load(response)["state"] == "ready"
        finally:
            if bidi:
                bidi.close()
            _stop(browser)
            _stop(server)
