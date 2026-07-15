"""Tests for the OpenCode web launcher (config/agents/opencode_web.py).

Covers the pieces that make the JupyterLab "OpenCode AI" tile work: the
prefix rewriting for the upstream web UI, the browser-based
llm.neurodesk.org key setup (persisted in the same ~/.bashrc format the
terminal wrapper and nbi_setup.sh read), the per-user credential handling,
and the reverse proxy in front of `opencode web`.
"""

import base64
import http.client
import http.server
import importlib.util
import json
import os
import socket
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def first_existing_path(*candidates):
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    raise AssertionError(f"None of these paths exist: {candidates}")


def opencode_web_module_path():
    return first_existing_path(
        "/opt/neurodesktop/opencode_web.py",
        REPO_ROOT / "config/agents/opencode_web.py",
    )


def load_opencode_web():
    spec = importlib.util.spec_from_file_location(
        "opencode_web", opencode_web_module_path()
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ocw = load_opencode_web()


# --- URL rewriting -------------------------------------------------------------

PREFIX = "/user/alice/opencode"


def test_rewrite_html_prefixes_root_absolute_references():
    html = (
        '<script type="module" src="/assets/index.js"></script>'
        '<link rel="stylesheet" href="/assets/app.css">'
        '<form action="/submit">'
        '<img src="//cdn.example.org/logo.png">'
        '<a href="https://opencode.ai/docs">docs</a>'
        '<style>body { background: url(/assets/bg.png); }</style>'
        '<script>const f = "/assets/font.woff2";</script>'
    )
    rewritten = ocw.rewrite_html(html, PREFIX)
    assert f'src="{PREFIX}/assets/index.js"' in rewritten
    assert f'href="{PREFIX}/assets/app.css"' in rewritten
    assert f'action="{PREFIX}/submit"' in rewritten
    # Protocol-relative and absolute URLs stay untouched.
    assert 'src="//cdn.example.org/logo.png"' in rewritten
    assert 'href="https://opencode.ai/docs"' in rewritten
    assert f"url({PREFIX}/assets/bg.png)" in rewritten
    assert f'"{PREFIX}/assets/font.woff2"' in rewritten


def test_rewrite_css_prefixes_url_references():
    css = '@font-face { src: url("/assets/inter.woff2"); } .x { background: url(/assets/bg.svg); }'
    rewritten = ocw.rewrite_css(css, PREFIX)
    assert f'url("{PREFIX}/assets/inter.woff2")' in rewritten
    assert f"url({PREFIX}/assets/bg.svg)" in rewritten


def test_rewrite_js_prefixes_asset_and_api_strings_only():
    js = (
        'const font = "/assets/inter.woff2";\n'
        "const api = '/api/session';\n"
        "const tpl = `/assets/${name}`;\n"
        'const ratio = a / b; const other = "/session/history";\n'
    )
    rewritten = ocw.rewrite_js(js, PREFIX)
    assert f'"{PREFIX}/assets/inter.woff2"' in rewritten
    assert f"'{PREFIX}/api/session'" in rewritten
    assert f"`{PREFIX}/assets/" in rewritten
    # Division and unknown root paths are left alone.
    assert "a / b" in rewritten
    assert '"/session/history"' in rewritten


def test_rewrite_body_is_noop_without_prefix():
    html = '<script src="/assets/index.js"></script>'
    assert ocw.rewrite_body(html, "text/html", "") == html


# --- Key handling ---------------------------------------------------------------


def test_sanitize_neurodesk_api_key_strips_control_chars_and_whitespace():
    assert ocw.sanitize_neurodesk_api_key(" sk-abc\r\n") == "sk-abc"
    assert ocw.sanitize_neurodesk_api_key("sk-\x07a\x1bbc\x7f") == "sk-abc"
    assert ocw.sanitize_neurodesk_api_key(None) == ""


def test_persist_key_roundtrip_and_replaces_previous_block(tmp_path):
    bashrc = tmp_path / ".bashrc"
    bashrc.write_text(
        "alias ll='ls -l'\n"
        "# Neurodesk API key for OpenCode\n"
        "export NEURODESK_API_KEY='old-key'\n",
        encoding="utf-8",
    )

    ocw.persist_key_to_bashrc(str(bashrc), "new-key")

    contents = bashrc.read_text(encoding="utf-8")
    assert "alias ll='ls -l'" in contents
    assert "old-key" not in contents
    assert contents.count("# Neurodesk API key for OpenCode") == 1
    assert ocw.read_key_from_bashrc(str(bashrc)) == "new-key"


def test_persisted_key_with_quotes_survives_bash_sourcing(tmp_path):
    """Quote escaping matches the terminal wrapper: bash sources it back
    verbatim (the sed-style readers only support quote-free keys, as in the
    bash wrappers)."""
    bashrc = tmp_path / ".bashrc"
    ocw.persist_key_to_bashrc(str(bashrc), "new'key")

    result = subprocess.run(
        ["bash", "-c", f'source "{bashrc}"; printf %s "$NEURODESK_API_KEY"'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "new'key"


def test_persisted_key_is_readable_by_nbi_setup_sed_regexes(tmp_path):
    """nbi_setup.sh reads the key back with sed; the python writer must match."""
    bashrc = tmp_path / ".bashrc"
    ocw.persist_key_to_bashrc(str(bashrc), "shared-key-123")

    # Exact expressions from nbi_setup.sh's read_export_from_bashrc().
    sed_script = (
        "s/^[[:space:]]*export[[:space:]]+NEURODESK_API_KEY='([^']+)'[[:space:]]*$/\\1/p"
    )
    result = subprocess.run(
        ["sed", "-nE", "-e", sed_script, str(bashrc)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "shared-key-123"


def test_load_or_create_password_creates_0600_and_is_stable(tmp_path):
    secret_file = tmp_path / "secrets" / "opencode_server_password"
    password = ocw.load_or_create_password(str(secret_file))
    assert password
    mode = stat.S_IMODE(secret_file.stat().st_mode)
    assert mode == 0o600
    assert ocw.load_or_create_password(str(secret_file)) == password


# --- Static config expectations --------------------------------------------------


def jupyter_template_path():
    return first_existing_path(
        "/opt/neurodesktop/jupyter_notebook_config.py.template",
        REPO_ROOT / "config/jupyter/jupyter_notebook_config.py.template",
    )


def test_jupyter_template_defines_opencode_proxy_entry():
    template_text = jupyter_template_path().read_text(encoding="utf-8")
    # The template must stay valid python (webapp entries are appended at the
    # {{WEBAPP_SERVERS}} comment placeholder, so it compiles as-is).
    compile(template_text, "jupyter_notebook_config.py.template", "exec")

    assert "'opencode': {" in template_text
    assert "/opt/neurodesktop/opencode_web.py" in template_text
    assert "opencode_server_password" in template_text
    assert "_opencode_basic" in template_text
    assert "'title': 'OpenCode AI'" in template_text
    assert "'icon_path': '/opt/opencode_logo.svg'" in template_text


def test_opencode_default_config_disables_sharing():
    config_path = first_existing_path(
        "/opt/jovyan_defaults/.config/opencode/opencode.json",
        REPO_ROOT / "config/agents/opencode_config.json",
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["share"] == "disabled"
    assert config["autoupdate"] is False


def test_desktop_launcher_script_and_entry():
    script = first_existing_path(
        "/opt/neurodesktop/opencode_web_desktop.sh",
        REPO_ROOT / "config/agents/opencode_web_desktop.sh",
    )
    subprocess.run(["bash", "-n", str(script)], check=True)

    desktop_entry = first_existing_path(
        "/usr/share/applications/opencode-web.desktop",
        REPO_ROOT / "config/agents/opencode-web.desktop",
    ).read_text(encoding="utf-8")
    assert "Exec=/opt/neurodesktop/opencode_web_desktop.sh" in desktop_entry
    assert "Icon=/opt/opencode_logo.svg" in desktop_entry


# --- End-to-end: setup page, key validation, proxying ----------------------------


class _FakeLLMHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path != "/openai/models":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.headers.get("Authorization") == "Bearer good-key":
            payload = json.dumps({"data": [{"id": "model-alpha"}]}).encode()
            self.send_response(200)
        else:
            payload = b'{"error":{"message":"Authentication Error"}}'
            self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


FAKE_BACKEND_SOURCE = '''#!/usr/bin/env python3
"""Stand-in for `opencode web`: asset-heavy SPA with basic auth and SSE."""
import base64
import http.server
import json
import os
import sys

args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
password = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
expected = "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()

state_dir = os.environ["FAKE_BACKEND_STATE_DIR"]
with open(os.path.join(state_dir, "env.json"), "w") as fh:
    json.dump(
        {
            "argv": sys.argv[1:],
            "OPENCODE_MODEL_PROFILE": os.environ.get("OPENCODE_MODEL_PROFILE", ""),
            "NEURODESK_API_KEY": os.environ.get("NEURODESK_API_KEY", ""),
            "OPENCODE_SERVER_PASSWORD": password,
        },
        fh,
    )

PAGES = {
    "/": (
        "text/html",
        '<html><head><script type="module" src="/assets/index.js"></script>'
        '</head><body>opencode-fake</body></html>',
    ),
    "/assets/index.js": (
        "text/javascript",
        'const font = "/assets/inter.woff2"; export default font;',
    ),
    "/binary.bin": ("application/octet-stream", "\\x00binary\\x00"),
}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        content_type, body = PAGES.get(self.path, ("text/plain", "fallback"))
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"port {port} did not open in time")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _request(port, path, headers=None, data=None, method=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        response = _OPENER.open(request, timeout=15)
        status = response.status
    except urllib.error.HTTPError as exc:
        response = exc
        status = exc.code
    body = response.read().decode("utf-8", errors="replace")
    return status, dict(response.headers), body


@pytest.fixture()
def launcher(tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    fake_backend = tmp_path / "fake-opencode"
    fake_backend.write_text(FAKE_BACKEND_SOURCE, encoding="utf-8")
    fake_backend.chmod(0o755)

    llm_server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), _FakeLLMHandler
    )
    threading.Thread(target=llm_server.serve_forever, daemon=True).start()
    llm_port = llm_server.server_address[1]

    port = _free_port()
    secret_file = home_dir / ".neurodesk" / "secrets" / "opencode_server_password"
    env = {
        **os.environ,
        "HOME": str(home_dir),
        "OPENCODE_WEB_WRAPPER_BIN": str(fake_backend),
        "OPENCODE_WEB_STARTUP_TIMEOUT": "30",
        "NEURODESK_LLM_BASE_URL": f"http://127.0.0.1:{llm_port}/openai",
        "FAKE_BACKEND_STATE_DIR": str(state_dir),
    }
    env.pop("NEURODESK_API_KEY", None)
    env.pop("OPENCODE_MODEL_PROFILE", None)

    process = subprocess.Popen(
        [sys_executable(), str(opencode_web_module_path()), "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_port(port)
        deadline = time.monotonic() + 10
        while not secret_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        password = secret_file.read_text(encoding="utf-8").strip()
        auth_header = {
            "Authorization": "Basic "
            + base64.b64encode(f"opencode:{password}".encode()).decode()
        }
        yield {
            "port": port,
            "home": home_dir,
            "state": state_dir,
            "password": password,
            "auth": auth_header,
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        llm_server.shutdown()


def sys_executable():
    import sys

    return sys.executable


def _complete_key_setup(ctx, key=b"key=good-key"):
    status, headers, _body = _request(
        ctx["port"],
        "/neurodesk-setup",
        headers={**ctx["auth"],
                 "Content-Type": "application/x-www-form-urlencoded"},
        data=key,
    )
    return status, headers


def _wait_for_proxied_root(ctx, extra_headers=None, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _headers, body = _request(
            ctx["port"], "/", headers={**ctx["auth"], **(extra_headers or {})}
        )
        if status == 200 and "opencode-fake" in body:
            return body
        time.sleep(0.3)
    raise AssertionError("proxied opencode backend never became ready")


def test_requests_without_credentials_are_rejected(launcher):
    status, _headers, _body = _request(launcher["port"], "/")
    assert status == 401


def test_setup_page_shown_until_key_is_configured(launcher):
    status, _headers, body = _request(
        launcher["port"], "/", headers=launcher["auth"]
    )
    assert status == 200
    assert "llm.neurodesk.org" in body
    assert "API Keys" in body
    assert "Create new secret key" in body


def test_rejected_key_reprompts_and_does_not_persist(launcher):
    status, _headers = _complete_key_setup(launcher, b"key=wrong-key")
    assert status == 200
    _status, _h, body = _request(
        launcher["port"], "/", headers=launcher["auth"]
    )
    assert "rejected by llm.neurodesk.org" in body or "llm.neurodesk.org" in body
    assert not (launcher["home"] / ".bashrc").exists() or (
        "wrong-key"
        not in (launcher["home"] / ".bashrc").read_text(encoding="utf-8")
    )


def test_valid_key_persists_starts_backend_and_proxies_with_rewrite(launcher):
    status, _headers = _complete_key_setup(launcher)
    assert status == 303

    bashrc = (launcher["home"] / ".bashrc").read_text(encoding="utf-8")
    assert "# Neurodesk API key for OpenCode" in bashrc
    assert "export NEURODESK_API_KEY='good-key'" in bashrc

    prefix = "/user/alice/opencode"
    body = _wait_for_proxied_root(
        launcher, extra_headers={"X-Forwarded-Prefix": prefix}
    )
    # Root-absolute asset references are rewritten against the proxy prefix.
    assert f'src="{prefix}/assets/index.js"' in body

    status, _headers, js_body = _request(
        launcher["port"],
        "/assets/index.js",
        headers={**launcher["auth"], "X-Forwarded-Prefix": prefix},
    )
    assert status == 200
    assert f'"{prefix}/assets/inter.woff2"' in js_body

    # Without a prefix (desktop mode) bodies pass through untouched.
    status, _headers, plain_body = _request(
        launcher["port"], "/", headers=launcher["auth"]
    )
    assert 'src="/assets/index.js"' in plain_body

    # The backend saw the key, the shared password, and the wrapper args.
    backend_env = json.loads(
        (launcher["state"] / "env.json").read_text(encoding="utf-8")
    )
    assert backend_env["NEURODESK_API_KEY"] == "good-key"
    assert backend_env["OPENCODE_SERVER_PASSWORD"] == launcher["password"]
    assert backend_env["argv"][0] == "web"
    assert "--hostname" in backend_env["argv"]


def test_skip_starts_backend_without_key(launcher):
    status, _headers = _complete_key_setup(launcher, b"skip=1")
    assert status == 303
    _wait_for_proxied_root(launcher)
    backend_env = json.loads(
        (launcher["state"] / "env.json").read_text(encoding="utf-8")
    )
    assert backend_env["NEURODESK_API_KEY"] == ""


def test_auth_query_param_exchanges_for_cookie(launcher):
    quoted = urllib.parse.quote(launcher["password"])
    status, headers, _body = _request(
        launcher["port"], f"/?auth={quoted}"
    )
    assert status == 303
    cookie = headers.get("Set-Cookie", "")
    assert "neurodesk_opencode_auth=" in cookie
    assert "HttpOnly" in cookie

    cookie_value = cookie.split(";", 1)[0]
    status, _headers, body = _request(
        launcher["port"], "/", headers={"Cookie": cookie_value}
    )
    assert status == 200
    assert "llm.neurodesk.org" in body  # setup page, authorized via cookie


def test_existing_bashrc_key_skips_setup_and_warm_starts(tmp_path):
    """A key configured earlier (e.g. via the terminal wrapper) boots straight
    into the proxied UI without showing the setup page."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    (home_dir / ".bashrc").write_text(
        "# Neurodesk API key for OpenCode\n"
        "export NEURODESK_API_KEY='good-key'\n",
        encoding="utf-8",
    )
    # Model selection from a previous run must be preserved via
    # OPENCODE_MODEL_PROFILE so the wrapper does not reset the default.
    config_dir = home_dir / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    (config_dir / "opencode.json").write_text(
        json.dumps({"model": "neurodesk/model-alpha"}), encoding="utf-8"
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    fake_backend = tmp_path / "fake-opencode"
    fake_backend.write_text(FAKE_BACKEND_SOURCE, encoding="utf-8")
    fake_backend.chmod(0o755)

    port = _free_port()
    env = {
        **os.environ,
        "HOME": str(home_dir),
        "OPENCODE_WEB_WRAPPER_BIN": str(fake_backend),
        "OPENCODE_WEB_STARTUP_TIMEOUT": "30",
        "FAKE_BACKEND_STATE_DIR": str(state_dir),
    }
    env.pop("NEURODESK_API_KEY", None)
    env.pop("OPENCODE_MODEL_PROFILE", None)

    process = subprocess.Popen(
        [sys_executable(), str(opencode_web_module_path()), "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_port(port)
        secret_file = (
            home_dir / ".neurodesk" / "secrets" / "opencode_server_password"
        )
        deadline = time.monotonic() + 10
        while not secret_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        password = secret_file.read_text(encoding="utf-8").strip()
        auth = {
            "Authorization": "Basic "
            + base64.b64encode(f"opencode:{password}".encode()).decode()
        }

        deadline = time.monotonic() + 20
        body = ""
        while time.monotonic() < deadline:
            status, _headers, body = _request(port, "/", headers=auth)
            if status == 200 and "opencode-fake" in body:
                break
            time.sleep(0.3)
        assert "opencode-fake" in body, body

        backend_env = json.loads(
            (state_dir / "env.json").read_text(encoding="utf-8")
        )
        assert backend_env["NEURODESK_API_KEY"] == "good-key"
        assert backend_env["OPENCODE_MODEL_PROFILE"] == "neurodesk/model-alpha"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
