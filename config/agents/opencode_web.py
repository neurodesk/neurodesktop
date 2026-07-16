#!/usr/bin/env python3
"""OpenCode web launcher for Neurodesktop.

Started by Jupyter Server Proxy (the "OpenCode AI" launcher tile) or by the
desktop shortcut. It provides what the bare `opencode web` server cannot do
behind Neurodesktop's proxy setup:

1. Requires per-user credentials on every request. Jupyter Server Proxy
   injects them via `request_headers_override`, the desktop shortcut passes
   them once as `?auth=` and gets a cookie back, so other users on a shared
   host cannot reach the 127.0.0.1 port.
2. Walks first-time users through llm.neurodesk.org API key setup in the
   browser (the terminal wrapper does this interactively). The key is
   validated against the LiteLLM endpoint and persisted to ~/.bashrc in the
   exact format the terminal wrapper and nbi_setup.sh already read.
3. Starts `opencode web` through the /usr/local/sbin/opencode wrapper so the
   provider probing, opencode.json refresh, and Notebook Intelligence sync
   stay single-sourced, then reverse-proxies to it.
4. Rewrites root-absolute URLs in HTML/CSS/JS responses against the
   X-Forwarded-Prefix header, because the upstream web UI assumes it is
   served from `/` and breaks behind the /opencode/ proxy prefix.

Environment overrides (mainly for tests):
  OPENCODE_WEB_WRAPPER_BIN   backend command (default /usr/local/sbin/opencode)
  OPENCODE_WEB_SECRET_FILE   password file (default
                             ~/.neurodesk/secrets/opencode_server_password)
  NEURODESK_LLM_BASE_URL     key-validation endpoint base
                             (default https://llm.neurodesk.org/openai)
  OPENCODE_WEB_STARTUP_TIMEOUT  seconds to wait for the backend (default 180)
"""

import argparse
import base64
import collections
import hmac
import html
import http.client
import http.server
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_WRAPPER_BIN = "/usr/local/sbin/opencode"
DEFAULT_LLM_BASE_URL = "https://llm.neurodesk.org/openai"
BACKEND_USERNAME = "opencode"
AUTH_COOKIE_NAME = "neurodesk_opencode_auth"
SETUP_PATH = "/neurodesk-setup"
BASHRC_KEY_COMMENT = "# Neurodesk API key for OpenCode"
STREAM_CHUNK_SIZE = 65536

# Hop-by-hop headers must not be forwarded in either direction.
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

REWRITABLE_CONTENT_TYPES = (
    "text/html",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
)


def sanitize_header_value(value):
    """Strip CR/LF so request-derived data cannot split response headers."""
    return str(value).replace("\r", "").replace("\n", "")


def secret_file_path():
    return os.environ.get(
        "OPENCODE_WEB_SECRET_FILE",
        os.path.join(
            os.path.expanduser("~"), ".neurodesk", "secrets",
            "opencode_server_password",
        ),
    )


def load_or_create_password(path):
    """Read the shared per-user password, creating it 0600 when missing.

    The same file is read by jupyter_notebook_config.py to build the
    Authorization header Jupyter Server Proxy injects, so both sides always
    agree on the credential.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            existing = fh.read().strip()
        if existing:
            return existing
    except OSError:
        pass

    password = secrets.token_urlsafe(24)
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(password + "\n")
    return password


def sanitize_neurodesk_api_key(raw_key):
    """Mirror the terminal wrapper's key sanitizing (control chars, CRLF, trim)."""
    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", raw_key or "")
    sanitized = sanitized.replace("\r", "").replace("\n", "")
    return sanitized.strip()


BASHRC_EXPORT_PATTERNS = [
    re.compile(r"^\s*export\s+NEURODESK_API_KEY='([^']+)'\s*$"),
    re.compile(r'^\s*export\s+NEURODESK_API_KEY="([^"]+)"\s*$'),
    re.compile(r"^\s*export\s+NEURODESK_API_KEY=([^\s#]+)\s*$"),
]


def read_key_from_bashrc(bashrc_path):
    """Read NEURODESK_API_KEY from ~/.bashrc the same way the wrappers do."""
    try:
        with open(bashrc_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""

    value = ""
    for line in lines:
        for pattern in BASHRC_EXPORT_PATTERNS:
            match = pattern.match(line)
            if match:
                value = match.group(1)
                break
    return sanitize_neurodesk_api_key(value)


def persist_key_to_bashrc(bashrc_path, key):
    """Write the key block in the exact format the terminal wrapper uses."""
    escaped_key = key.replace("'", "'\"'\"'")

    try:
        with open(bashrc_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []

    export_re = re.compile(r"^\s*export\s+NEURODESK_API_KEY=")
    comment_re = re.compile(r"^\s*#\s*Neurodesk API key for OpenCode\s*$")
    kept = [
        line for line in lines
        if not export_re.match(line) and not comment_re.match(line)
    ]
    kept += ["", BASHRC_KEY_COMMENT, f"export NEURODESK_API_KEY='{escaped_key}'"]

    tmp_path = f"{bashrc_path}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(kept) + "\n")
    os.replace(tmp_path, bashrc_path)


def validate_neurodesk_api_key(key, base_url):
    """Validate a candidate key against the LiteLLM /models endpoint.

    Mirrors the terminal wrapper's acceptance rules: 200 and 404 (model list
    hidden) count as verified, 401/403 is a rejected key, and an unreachable
    endpoint accepts the key with a warning rather than locking the user out.
    Returns (accepted, verified, message).
    """
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return (
            True,
            False,
            "API key received, but llm.neurodesk.org could not be reached to "
            "verify it right now.",
        )

    if status in (401, 403):
        return (
            False,
            False,
            "That API key was rejected by llm.neurodesk.org. "
            "Please paste a correct key.",
        )
    return True, True, "API key verified with llm.neurodesk.org."


def current_opencode_model(home_dir):
    """Read the default model from ~/.config/opencode/opencode.json, if any."""
    config_path = os.path.join(home_dir, ".config", "opencode", "opencode.json")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return ""
    model = cfg.get("model")
    return model if isinstance(model, str) else ""


# --- Response body rewriting -------------------------------------------------
#
# The upstream web UI references its assets and API with root-absolute URLs
# ("/assets/...", url(/...)), which break behind the proxy prefix. Rewrites
# are anchored on quote/attribute context so protocol-relative URLs ("//x")
# and division operators in JS are never touched.

_HTML_ATTR_RE = re.compile(
    r"""(\b(?:href|src|action|poster|data-src)\s*=\s*)(["'])/(?!/)""",
    re.IGNORECASE,
)
_CSS_URL_RE = re.compile(r"""(\burl\(\s*)(["']?)/(?!/)""", re.IGNORECASE)
_JS_STRING_PATH_RE = re.compile(r"""(["'`])/(assets|api)/""")


def rewrite_html(body, prefix):
    body = _HTML_ATTR_RE.sub(rf"\g<1>\g<2>{prefix}/", body)
    body = _CSS_URL_RE.sub(rf"\g<1>\g<2>{prefix}/", body)
    body = _JS_STRING_PATH_RE.sub(rf"\g<1>{prefix}/\g<2>/", body)
    return body


def rewrite_css(body, prefix):
    return _CSS_URL_RE.sub(rf"\g<1>\g<2>{prefix}/", body)


def rewrite_js(body, prefix):
    return _JS_STRING_PATH_RE.sub(rf"\g<1>{prefix}/\g<2>/", body)


def rewrite_body(body, content_type, prefix):
    if not prefix:
        return body
    if content_type.startswith("text/html"):
        return rewrite_html(body, prefix)
    if content_type.startswith("text/css"):
        return rewrite_css(body, prefix)
    return rewrite_js(body, prefix)


# --- Backend process management ----------------------------------------------


class OpencodeBackend:
    """Owns the `opencode web` child process and its readiness state."""

    def __init__(self, wrapper_bin, startup_timeout, home_dir):
        self.wrapper_bin = wrapper_bin
        self.startup_timeout = startup_timeout
        self.home_dir = home_dir
        self.port = None
        self.process = None
        self.state = "not_started"  # not_started | starting | ready | failed
        self.log_tail = collections.deque(maxlen=100)
        self._lock = threading.Lock()
        self.backend_password = ""

    def start(self, backend_password):
        with self._lock:
            if self.state in ("starting", "ready"):
                return
            self.state = "starting"
            self.backend_password = backend_password

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]

        env = dict(os.environ)
        env["OPENCODE_SERVER_PASSWORD"] = backend_password
        # Preserve a model chosen earlier (terminal wrapper or a previous web
        # launch): the wrapper's non-interactive path would otherwise reset
        # the default to the first working provider on every launch.
        if not env.get("OPENCODE_MODEL_PROFILE"):
            model = current_opencode_model(self.home_dir)
            if model:
                env["OPENCODE_MODEL_PROFILE"] = model

        try:
            self.process = subprocess.Popen(
                [
                    self.wrapper_bin,
                    "web",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                ],
                cwd=self.home_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            self.log_tail.append(f"failed to start {self.wrapper_bin}: {exc}")
            self.state = "failed"
            return

        threading.Thread(target=self._pump_logs, daemon=True).start()
        threading.Thread(target=self._wait_ready, daemon=True).start()

    def _pump_logs(self):
        for raw_line in self.process.stdout:
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            self.log_tail.append(line)
            print(f"[opencode] {line}", flush=True)

    def _wait_ready(self):
        deadline = time.monotonic() + self.startup_timeout
        credentials = base64.b64encode(
            f"{BACKEND_USERNAME}:{self.backend_password}".encode()
        ).decode()
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.log_tail.append(
                    f"backend exited with status {self.process.returncode} "
                    "before becoming ready"
                )
                self.state = "failed"
                return
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
                conn.request("GET", "/", headers={
                    "Authorization": f"Basic {credentials}",
                })
                response = conn.getresponse()
                response.read()
                conn.close()
                if response.status < 500:
                    self.state = "ready"
                    return
            except OSError:
                pass
            time.sleep(0.5)
        self.log_tail.append("backend did not become ready in time")
        self.state = "failed"

    def terminate(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


# --- HTML pages ---------------------------------------------------------------

PAGE_STYLE = """
  :root { color-scheme: light dark; }
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0;
         display: flex; min-height: 100vh; align-items: center;
         justify-content: center;
         background: #f5f6fa; color: #1a202c; }
  @media (prefers-color-scheme: dark) {
    body { background: #14161f; color: #e6e8ef; }
    .card { background: #1d2030 !important; }
    input[type=password] { background: #14161f; color: #e6e8ef; }
  }
  .card { background: #fff; border-radius: 12px; padding: 2rem 2.5rem;
          max-width: 34rem; box-shadow: 0 4px 24px rgba(0,0,0,.12); }
  h1 { font-size: 1.3rem; margin-top: 0; }
  ol { padding-left: 1.2rem; line-height: 1.6; }
  input[type=password] { width: 100%; padding: .55rem .7rem; font-size: 1rem;
          border: 1px solid #8888; border-radius: 6px; box-sizing: border-box; }
  button { margin-top: .8rem; padding: .55rem 1.2rem; font-size: 1rem;
           border-radius: 6px; border: none; cursor: pointer; }
  .primary { background: #4f46e5; color: #fff; }
  .secondary { background: transparent; color: inherit; opacity: .7;
               text-decoration: underline; }
  .error { color: #dc2626; font-weight: 600; }
  .muted { opacity: .75; font-size: .9rem; }
  .spinner { width: 2.2rem; height: 2.2rem; border-radius: 50%;
             border: 4px solid #8884; border-top-color: #4f46e5;
             animation: spin 1s linear infinite; margin-bottom: 1rem; }
  @keyframes spin { to { transform: rotate(360deg); } }
  pre { overflow-x: auto; background: #0002; padding: .6rem;
        border-radius: 6px; font-size: .8rem; }
"""


def setup_page(prefix, error=""):
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    action = html.escape(f"{prefix}{SETUP_PATH}")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenCode - Neurodesk setup</title><style>{PAGE_STYLE}</style></head>
<body><div class="card">
<h1>Set up your Neurodesk LLM API key</h1>
<p>OpenCode uses the free <strong>llm.neurodesk.org</strong> service. This
one-time setup stores the key for OpenCode, Notebook Intelligence, and the
terminal agents.</p>
<ol>
<li>Open <a href="https://llm.neurodesk.org" target="_blank"
    rel="noopener">llm.neurodesk.org</a> and create an account (if needed).</li>
<li>Click your user avatar &rarr; <strong>Settings</strong> &rarr;
    <strong>Account</strong>.</li>
<li>Scroll to the <strong>API Keys</strong> section, then click
    <strong>Create new secret key</strong> / <strong>Show</strong>.</li>
<li>Paste the key below.</li>
</ol>
{error_html}
<form method="post" action="{action}">
<input type="password" name="key" placeholder="Paste your API key"
       autocomplete="off" autofocus>
<button class="primary" type="submit">Save and start OpenCode</button>
</form>
<form method="post" action="{action}">
<input type="hidden" name="skip" value="1">
<button class="secondary" type="submit">Continue without a key
(use other providers)</button>
</form>
<p class="muted">The key is saved to <code>~/.bashrc</code> as
<code>NEURODESK_API_KEY</code>, exactly like the terminal setup.</p>
</div></body></html>"""


def waiting_page():
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenCode is starting...</title><style>{PAGE_STYLE}</style></head>
<body><div class="card" style="text-align:center">
<div class="spinner" style="margin-left:auto;margin-right:auto"></div>
<h1>OpenCode is starting&hellip;</h1>
<p class="muted">Checking model providers and launching the web interface.
This page refreshes automatically.</p>
</div></body></html>"""


def failed_page(log_tail):
    log_html = html.escape("\n".join(log_tail)[-4000:])
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenCode failed to start</title><style>{PAGE_STYLE}</style></head>
<body><div class="card">
<h1>OpenCode failed to start</h1>
<p>The OpenCode web server did not come up. Recent output:</p>
<pre>{log_html}</pre>
<p class="muted">You can also run <code>opencode</code> in a terminal for the
interactive setup, then reload this page.</p>
</div></body></html>"""


# --- HTTP handler ---------------------------------------------------------------


class OpencodeWebHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NeurodeskOpencodeWeb"

    # Injected by serve():
    proxy_password = ""
    # Random per-process bearer for the desktop cookie flow; independent of
    # the password so no credential material is ever derived or stored in the
    # browser (a restart simply invalidates old cookies and the desktop
    # shortcut re-authenticates via ?auth=).
    cookie_token = ""
    backend = None
    llm_base_url = DEFAULT_LLM_BASE_URL
    home_dir = ""
    key_skipped = False

    # -- helpers --

    def log_message(self, fmt, *args):
        sys.stderr.write("opencode_web: %s\n" % (fmt % args))

    def external_prefix(self):
        for header in ("X-Forwarded-Prefix", "X-Forwarded-Context",
                       "X-ProxyContextPath"):
            value = self.headers.get(header)
            if value:
                value = sanitize_header_value(value).rstrip("/")
                if value and not value.startswith("/"):
                    value = "/" + value
                return value
        return ""

    def expected_authorization(self):
        credentials = base64.b64encode(
            f"{BACKEND_USERNAME}:{self.proxy_password}".encode()
        ).decode()
        return f"Basic {credentials}"

    def is_authorized(self):
        supplied = self.headers.get("Authorization", "")
        if supplied and hmac.compare_digest(
            supplied, self.expected_authorization()
        ):
            return True

        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            name, _, value = part.strip().partition("=")
            if name == AUTH_COOKIE_NAME and hmac.compare_digest(
                value, self.cookie_token
            ):
                return True
        return False

    def send_page(self, status, body, extra_headers=None):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or []):
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def redirect(self, location, extra_headers=None):
        # Same-origin redirects only: normalize to a single-slash-rooted path
        # so request-derived values can neither split headers nor turn into a
        # protocol-relative ("//host") redirect.
        location = sanitize_header_value(location)
        location = "/" + location.lstrip("/")
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for name, value in (extra_headers or []):
            self.send_header(name, sanitize_header_value(value))
        self.end_headers()

    # -- request routing --

    def handle_any(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        # Desktop flow: a one-time ?auth=<password> exchange for a cookie, so
        # the URL in the browser bar stops carrying the credential.
        auth_values = query.pop("auth", [])
        if auth_values and hmac.compare_digest(
            auth_values[0], self.proxy_password
        ):
            clean_query = urllib.parse.urlencode(query, doseq=True)
            location = parsed.path + (f"?{clean_query}" if clean_query else "")
            cookie = (
                f"{AUTH_COOKIE_NAME}={self.cookie_token}; Path=/; "
                "HttpOnly; SameSite=Lax"
            )
            self.redirect(location or "/", [("Set-Cookie", cookie)])
            return

        if not self.is_authorized():
            self.send_page(
                401,
                "<h1>401 Unauthorized</h1><p>Open OpenCode through the "
                "Neurodesk launcher or desktop shortcut.</p>",
            )
            return

        if parsed.path == SETUP_PATH and self.command == "POST":
            self.handle_setup_post()
            return

        cls = type(self)
        if cls.backend.state == "not_started":
            key = os.environ.get("NEURODESK_API_KEY") or read_key_from_bashrc(
                os.path.join(self.home_dir, ".bashrc")
            )
            if key or cls.key_skipped:
                if key:
                    os.environ["NEURODESK_API_KEY"] = key
                cls.backend.start(self.proxy_password)
            else:
                self.send_page(200, setup_page(self.external_prefix()))
                return

        if cls.backend.state == "starting":
            self.send_page(200, waiting_page())
            return
        if cls.backend.state == "failed":
            self.send_page(502, failed_page(list(cls.backend.log_tail)))
            return

        self.proxy_request(parsed)

    def handle_setup_post(self):
        cls = type(self)
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length else b""
        form = urllib.parse.parse_qs(raw_body.decode("utf-8", errors="replace"))
        prefix = self.external_prefix()

        if form.get("skip", [""])[0]:
            cls.key_skipped = True
            cls.backend.start(self.proxy_password)
            self.redirect(f"{prefix}/" if prefix else "/")
            return

        key = sanitize_neurodesk_api_key(form.get("key", [""])[0])
        if not key:
            self.send_page(
                200, setup_page(prefix, "API key cannot be empty.")
            )
            return

        accepted, _verified, message = validate_neurodesk_api_key(
            key, self.llm_base_url
        )
        if not accepted:
            self.send_page(200, setup_page(prefix, message))
            return

        bashrc_path = os.path.join(self.home_dir, ".bashrc")
        try:
            persist_key_to_bashrc(bashrc_path, key)
        except OSError as exc:
            self.log_message("failed to persist key to %s: %s", bashrc_path, exc)
        os.environ["NEURODESK_API_KEY"] = key
        self.log_message("neurodesk key setup: %s", message)

        cls.backend.start(self.proxy_password)
        self.redirect(f"{prefix}/" if prefix else "/")

    # -- reverse proxy --

    def proxy_request(self, parsed):
        backend = type(self).backend
        body = None
        length = self.headers.get("Content-Length")
        if length:
            body = self.rfile.read(int(length))

        headers = {}
        for name, value in self.headers.items():
            lowered = name.lower()
            if lowered in HOP_BY_HOP_HEADERS or lowered in (
                "host", "authorization", "accept-encoding",
            ):
                continue
            if lowered == "cookie":
                value = "; ".join(
                    part.strip() for part in value.split(";")
                    if part.strip().partition("=")[0] != AUTH_COOKIE_NAME
                )
                if not value:
                    continue
            headers[name] = value
        headers["Host"] = f"127.0.0.1:{backend.port}"
        credentials = base64.b64encode(
            f"{BACKEND_USERNAME}:{backend.backend_password}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {credentials}"
        # Ask for identity encoding so body rewriting sees plain text.
        headers["Accept-Encoding"] = "identity"

        target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        try:
            conn = http.client.HTTPConnection(
                "127.0.0.1", backend.port, timeout=300
            )
            conn.request(self.command, target, body=body, headers=headers)
            response = conn.getresponse()
        except OSError as exc:
            self.send_page(502, f"<h1>502</h1><p>OpenCode backend error: "
                                f"{html.escape(str(exc))}</p>")
            return

        content_type = response.getheader("Content-Type", "")
        prefix = self.external_prefix()
        rewritable = prefix and any(
            content_type.startswith(ct) for ct in REWRITABLE_CONTENT_TYPES
        )

        try:
            if rewritable:
                raw = response.read()
                text = rewrite_body(
                    raw.decode("utf-8", errors="replace"), content_type, prefix
                )
                payload = text.encode("utf-8")
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() in HOP_BY_HOP_HEADERS or name.lower() in (
                        "content-length", "content-encoding",
                    ):
                        continue
                    self.send_header(name, sanitize_header_value(value))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)
                return

            self.send_response(response.status)
            has_length = False
            for name, value in response.getheaders():
                if name.lower() in HOP_BY_HOP_HEADERS:
                    continue
                if name.lower() == "content-length":
                    has_length = True
                self.send_header(name, sanitize_header_value(value))
            if not has_length:
                # Chunked/streaming upstream (e.g. the SSE /event feed):
                # delimit our response by connection close instead.
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command == "HEAD":
                return
            while True:
                chunk = response.read1(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()

    do_GET = handle_any
    do_POST = handle_any
    do_PUT = handle_any
    do_DELETE = handle_any
    do_PATCH = handle_any
    do_HEAD = handle_any
    do_OPTIONS = handle_any


class ThreadingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True,
                        help="port to listen on (127.0.0.1)")
    args = parser.parse_args(argv)

    home_dir = os.path.expanduser("~")
    password = load_or_create_password(secret_file_path())
    backend = OpencodeBackend(
        wrapper_bin=os.environ.get(
            "OPENCODE_WEB_WRAPPER_BIN", DEFAULT_WRAPPER_BIN
        ),
        startup_timeout=int(
            os.environ.get("OPENCODE_WEB_STARTUP_TIMEOUT", "180")
        ),
        home_dir=home_dir,
    )

    OpencodeWebHandler.proxy_password = password
    OpencodeWebHandler.cookie_token = secrets.token_urlsafe(32)
    OpencodeWebHandler.backend = backend
    OpencodeWebHandler.llm_base_url = os.environ.get(
        "NEURODESK_LLM_BASE_URL", DEFAULT_LLM_BASE_URL
    )
    OpencodeWebHandler.home_dir = home_dir

    # Warm start: when the key is already configured, boot the backend before
    # the first request arrives so the tab lands on a ready UI sooner.
    existing_key = os.environ.get("NEURODESK_API_KEY") or read_key_from_bashrc(
        os.path.join(home_dir, ".bashrc")
    )
    if existing_key:
        os.environ["NEURODESK_API_KEY"] = existing_key
        backend.start(password)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), OpencodeWebHandler)

    def shutdown(_signum, _frame):
        backend.terminate()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print(f"opencode_web: listening on 127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        backend.terminate()


if __name__ == "__main__":
    serve()
