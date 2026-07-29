#!/usr/bin/env python3
"""OpenCode web launcher for Neurodesktop.

Started by Jupyter Server Proxy (the "Scigent.ai" launcher tile) or by the
desktop shortcut. It provides what the bare `opencode web` server cannot do
behind Neurodesktop's proxy setup:

1. Requires per-user credentials on every request. Jupyter Server Proxy
   injects them via `request_headers_override`; the desktop shortcut passes a
   single-use login token as `?auth=` and gets a cookie back. Other local
   users on a shared host can reach the 127.0.0.1 port but cannot
   authenticate without the credential.
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
5. Runs the backend from ~/opencode-work, then creates a unique
   ~/opencode-work/DATE_TIME Git project with a durable root commit for every
   ``POST /session`` and seeds it with /opt/AGENTS.md. The proxy records the
   returned session id and pins later requests for that session to its project.
   Both the ``?directory=`` parameter and ``x-opencode-directory`` header are
   enforced because
   OpenCode's client only puts the directory in the query for GET/HEAD; POSTs
   such as session creation and message send carry it in the header alone.
6. Previews the files an agent produced. The upstream changed-files list
   renders every non-text file as an unreadable binary diff, so a small
   injected script turns a click on a screenshot or a NIfTI volume into an
   overlay viewer, served from this proxy's own file endpoint and rendered
   with the NiiVue bundle vendored into the image.

Environment overrides (mainly for tests):
  OPENCODE_WEB_WRAPPER_BIN   backend command (default /usr/local/sbin/opencode)
  OPENCODE_WEB_SECRET_FILE   password file (default
                             ~/.neurodesk/secrets/opencode_server_password)
  NEURODESK_LLM_BASE_URL     key-validation endpoint base
                             (default https://llm.neurodesk.org/openai)
  OPENCODE_WEB_STARTUP_TIMEOUT  seconds to wait for the backend (default 180)
  OPENCODE_WEB_AGENTS_FILE    session instruction seed (default /opt/AGENTS.md)
  OPENCODE_WEB_NIIVUE_BUNDLE  NiiVue viewer bundle for volume previews
                              (default /opt/neurodesktop/vendor/niivue.js)
  OPENCODE_WEB_PREVIEW_MAX_BYTES  largest previewable file (default 512 MiB)
"""

import argparse
import base64
import collections
import hashlib
import hmac
import html
import http.client
import http.server
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_WRAPPER_BIN = "/usr/local/sbin/opencode"
DEFAULT_LLM_BASE_URL = "https://llm.neurodesk.org/openai"
DEFAULT_AGENTS_FILE = "/opt/AGENTS.md"
OPENCODE_BASH_ENV = "/opt/neurodesktop/opencode_bash_env.sh"
BACKEND_USERNAME = "opencode"
AUTH_COOKIE_NAME = "neurodesk_opencode_auth"
SETUP_PATH = "/neurodesk-setup"
PREFIX_BOOTSTRAP_PATH = "/neurodesk-prefix.js"
PREVIEW_BOOTSTRAP_PATH = "/neurodesk-preview.js"
NIIVUE_ASSET_PATH = "/neurodesk-niivue.js"
FILE_PREVIEW_PATH = "/neurodesk-file"
DEFAULT_NIIVUE_BUNDLE = "/opt/neurodesktop/vendor/niivue.js"
OPENCODE_DEFAULT_SERVER_STORAGE_KEY = (
    "opencode.settings.dat:defaultServerUrl"
)
OPENCODE_PREFIX_SERVER_GLOBAL = "__NEURODESK_OPENCODE_SERVER_URL__"
OPENCODE_PREFIX_ROUTER_GLOBAL = "__NEURODESK_OPENCODE_BASE_PATH__"
OPENCODE_WEB_ORIGIN_EXPRESSION = (
    'location.hostname.includes("opencode.ai")?'
    '"http://localhost:4096":location.origin'
)
OPENCODE_PREFIXED_WEB_ORIGIN_EXPRESSION = (
    'location.hostname.includes("opencode.ai")?'
    '"http://localhost:4096":'
    f"window.{OPENCODE_PREFIX_SERVER_GLOBAL}||location.origin"
)
OPENCODE_WEB_ROUTER_COMPONENT_EXPRESSION = (
    "get component(){return e.router??Epe},root:n=>"
)
OPENCODE_PREFIXED_WEB_ROUTER_COMPONENT_EXPRESSION = (
    "get component(){return e.router??Epe},"
    f"get base(){{return window.{OPENCODE_PREFIX_ROUTER_GLOBAL}||\"\"}},"
    "root:n=>"
)
OPENCODE_WEB_ROUTE_PARSER_EXPRESSION = (
    '=(e,t)=>{const n=e.split("/").filter(Boolean);'
    'if(n.length===0)return{type:"home"}'
)
OPENCODE_PREFIXED_WEB_ROUTE_PARSER_EXPRESSION = (
    "=(e,t)=>{"
    f'const _nb=window.{OPENCODE_PREFIX_ROUTER_GLOBAL}||"";'
    '_nb&&(e===_nb||e.startsWith(_nb+"/"))&&(e=e.slice(_nb.length));'
    'const n=e.split("/").filter(Boolean);'
    'if(n.length===0)return{type:"home"}'
)
OPENCODE_SSE_HEADERS_EXPRESSION = (
    "const b=u.headers instanceof Headers?u.headers:new Headers(u.headers);"
    'd!==void 0&&b.set("Last-Event-ID",d);'
)
OPENCODE_STREAMING_SSE_HEADERS_EXPRESSION = (
    "const b=u.headers instanceof Headers?u.headers:new Headers(u.headers);"
    'b.set("Accept","text/event-stream"),'
    'd!==void 0&&b.set("Last-Event-ID",d);'
)
BASHRC_KEY_COMMENT = "# Neurodesk API key for OpenCode"
STREAM_CHUNK_SIZE = 65536
OPENCODE_WORK_DIR_PARENT = "opencode-work"
# OpenCode carries the request directory in these query parameters (the
# bracketed form is used by its /api/ routes) and, for every non-GET/HEAD
# request, in this header instead.
OPENCODE_DIRECTORY_QUERY_KEYS = ("directory", "location[directory]")
OPENCODE_DIRECTORY_HEADER = "x-opencode-directory"
OPENCODE_WORK_DIR_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
OPENCODE_WEB_DEFAULT_MODEL_PROFILE = "neurodesk"
OPENCODE_SESSION_PATH_RE = re.compile(r"^/(?:api/)?session/(ses_[^/]+)(?:/|$)")

# Hop-by-hop headers must not be forwarded in either direction.
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",  # codespell:ignore te
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

# File previews. Only these extensions are served by the preview endpoint, so
# it can never be used as a general file-read API for the session project.
PREVIEW_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    # Rendered inside <img>, where scripts embedded in an SVG never execute.
    ".svg": "image/svg+xml",
}
# Volume formats NiiVue reads. Longest suffixes first so ".nii.gz" wins over
# a bare ".gz"-style match.
PREVIEW_VOLUME_SUFFIXES = (
    ".nii.gz",
    ".nii",
    ".mgz",
    ".mgh",
    ".mif.gz",
    ".mif",
    ".nrrd",
    ".nhdr",
    ".mha",
    ".mhd",
)
DEFAULT_PREVIEW_MAX_BYTES = 512 * 1024 * 1024
# Bounds for the basename fallback search (see open_preview_file).
PREVIEW_SEARCH_MAX_ENTRIES = 20000
PREVIEW_SEARCH_SKIP_DIRS = {".git", ".opencode", "node_modules", "__pycache__"}
_SAFE_PREVIEW_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._@+~-]+$")


def sanitize_header_value(value):
    """Strip CR/LF so request-derived data cannot split response headers."""
    return str(value).replace("\r", "").replace("\n", "")


def redact_auth_params(text):
    """Blank out ?auth=... credentials before request lines reach the logs."""
    return re.sub(r"([?&]auth=)[^\s&]*", r"\1REDACTED", str(text))


# Forwarded prefixes are interpolated into rewritten HTML/CSS/JS bodies, so
# only canonical root-relative paths are accepted: no quotes, markup, spaces,
# control characters, query strings, fragments, or backslashes.
_SAFE_PREFIX_RE = re.compile(r"^/[A-Za-z0-9._~%/@:+-]*$")


def safe_forwarded_prefix(value):
    """Normalize an X-Forwarded-Prefix value to a safe root-relative path.

    Returns the trimmed prefix, or "" when the value is absent, is the root,
    or contains anything that could break out of a quoted URL context in the
    rewritten response bodies.
    """
    value = sanitize_header_value(value or "").strip().rstrip("/")
    if not value:
        return ""
    if not _SAFE_PREFIX_RE.match(value):
        return ""
    return value


def secret_file_path():
    """Return the shared password file path (env-overridable for tests)."""
    return os.environ.get(
        "OPENCODE_WEB_SECRET_FILE",
        os.path.join(
            os.path.expanduser("~"), ".neurodesk", "secrets",
            "opencode_server_password",
        ),
    )


def login_token_file_path(port):
    """Return the per-instance single-use login token file path."""
    return os.environ.get(
        "OPENCODE_WEB_LOGIN_TOKEN_FILE",
        os.path.join(
            os.path.expanduser("~"), ".neurodesk", "secrets",
            f"opencode_web_login_token.{port}",
        ),
    )


def load_or_create_password(path):
    """Read the shared per-user password, creating it atomically when missing.

    The same file is read by jupyter_notebook_config.py (which imports this
    helper) to build the Authorization header Jupyter Server Proxy injects.
    O_EXCL creation makes concurrent starters (Jupyter config load, the
    desktop shortcut, the proxy launcher) agree on a single credential: the
    loser of the create race re-reads the winner's file. Raises OSError when
    no credential can be obtained so callers fail closed.
    """
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    for _ in range(40):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                existing = fh.read().strip()
            if existing:
                return existing
            # Empty file: never delete it (another process may own it) -
            # keep re-reading and fail closed below if it never fills.
        except OSError:
            pass

        # Publish atomically: write a private temp file, then hard-link it
        # into place. link() fails if the path exists, so readers can never
        # observe a partially written credential and losers re-read the
        # winner's file.
        password = secrets.token_urlsafe(24)
        tmp_path = f"{path}.tmp.{os.getpid()}.{secrets.token_hex(4)}"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(password + "\n")
        try:
            os.link(tmp_path, path)
            return password
        except FileExistsError:
            time.sleep(0.05)
        finally:
            os.unlink(tmp_path)
    raise OSError(f"could not obtain the OpenCode web credential at {path}")


def rotate_login_token(path):
    """Write a fresh single-use browser login token (0600) and return it.

    The desktop shortcut reads this file and passes the token as ?auth=; the
    handler rotates it on every successful exchange, so a token that leaked
    through browser history or logs cannot be replayed.
    """
    token = secrets.token_urlsafe(24)
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")
    return token


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
    if status in (200, 404):
        return True, True, "API key verified with llm.neurodesk.org."
    return (
        True,
        False,
        f"API key received, but llm.neurodesk.org returned HTTP {status}; "
        "continuing without verification.",
    )


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
_JS_STRING_ASSET_PATH_RE = re.compile(r"""(["'`])/?assets/""")
# The identifier quantifiers are possessive (*+): the leading character
# class is a subset of the repeated one, so a backtracking star would rescan
# quadratically on adversarial inputs such as long '$' runs (CodeQL
# py/polynomial-redos). Identifiers never contain the delimiters that follow
# them, so refusing to backtrack cannot change what matches.
_JS_HOME_TOGGLE_RE = re.compile(
    r"(?P<binding>[A-Za-z_$][A-Za-z0-9_$]*+)=\(\)=>"
    r"(?P<tabs>[A-Za-z_$][A-Za-z0-9_$]*+)\.toggleHome\(\{home:"
    r"(?P<layout>[A-Za-z_$][A-Za-z0-9_$]*+)\.route\(\)\.type===\"home\","
    r"current:(?P<current>[A-Za-z_$][A-Za-z0-9_$]*+)\(\)\}\)"
)


def _inject_after_head(body, snippet):
    """Insert ``snippet`` right after the opening <head> tag.

    Plain linear string scanning instead of a regex: a backtracking pattern
    over the whole response body would be quadratic on adversarial input
    (CodeQL py/polynomial-redos). Falls back to prepending when no <head>
    tag is found.
    """
    lower = body.lower()
    search_pos = 0
    while True:
        idx = lower.find("<head", search_pos)
        if idx == -1:
            return snippet + body
        after = idx + len("<head")
        # Reject longer tag names such as <header>.
        if after < len(body) and body[after] not in ">/ \t\r\n":
            search_pos = after
            continue
        close = body.find(">", after)
        if close == -1:
            return snippet + body
        return body[:close + 1] + snippet + body[close + 1:]


def prefix_bootstrap_script(prefix):
    """Point OpenCode's API client and SPA router at the proxy prefix.

    OpenCode 1.18 builds API URLs from a default server stored in localStorage.
    Without this bootstrap it uses ``location.origin``, so root routes such as
    /provider and /global/config escape Jupyter's /opencode proxy. Loading this
    same-origin script before the module bundle keeps the full API (including
    the native model picker) under the validated forwarded prefix. The router
    also needs the prefix as its base; otherwise it decodes the first URL
    segment (``opencode``) as a base64 project directory.
    """
    safe_prefix = safe_forwarded_prefix(prefix)
    prefix_json = json.dumps(safe_prefix)
    key_json = json.dumps(OPENCODE_DEFAULT_SERVER_STORAGE_KEY)
    return f"""(() => {{
  const prefix = {prefix_json};
  const server = window.location.origin + prefix;
  window.{OPENCODE_PREFIX_SERVER_GLOBAL} = server;
  window.{OPENCODE_PREFIX_ROUTER_GLOBAL} = prefix;
  try {{
    window.localStorage.setItem({key_json}, server);
  }} catch (_error) {{
    // Private browsing or a locked-down browser may disable localStorage.
  }}
}})();
"""


# --- File previews -------------------------------------------------------------


def niivue_bundle_path():
    """Return the vendored NiiVue bundle path (env-overridable for tests)."""
    return os.environ.get("OPENCODE_WEB_NIIVUE_BUNDLE", DEFAULT_NIIVUE_BUNDLE)


_NIIVUE_VERSION_CACHE = {}
_NIIVUE_VERSION_LOCK = threading.Lock()


def niivue_bundle_version(path=None):
    """Return a content token for the vendored bundle ("" when missing).

    The bundle is served ``immutable`` for a year, so its URL has to change
    when the image ships a different NiiVue. Hashing the bytes (rather than
    stamping the pinned version) also covers a rebuilt or hand-patched
    bundle. The digest is computed once per file identity and reused; the
    previewer that carries the token is never cached.
    """
    path = path or niivue_bundle_path()
    try:
        info = os.stat(path)
    except OSError:
        return ""
    key = (path, info.st_size, info.st_mtime_ns)
    with _NIIVUE_VERSION_LOCK:
        cached = _NIIVUE_VERSION_CACHE.get(key)
    if cached:
        return cached

    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(STREAM_CHUNK_SIZE), b""):
                digest.update(chunk)
    except OSError:
        return ""
    token = digest.hexdigest()[:16]
    with _NIIVUE_VERSION_LOCK:
        # Only one bundle identity is ever live; never grow this cache.
        _NIIVUE_VERSION_CACHE.clear()
        _NIIVUE_VERSION_CACHE[key] = token
    return token


def preview_size_limit():
    """Return the largest file the preview endpoint will serve."""
    try:
        limit = int(os.environ.get("OPENCODE_WEB_PREVIEW_MAX_BYTES", "") or 0)
    except ValueError:
        limit = 0
    return limit if limit > 0 else DEFAULT_PREVIEW_MAX_BYTES


def preview_kind(name):
    """Classify a file name as ``image``, ``volume``, or unsupported ("")."""
    lowered = os.path.basename(str(name or "")).lower()
    if any(lowered.endswith(suffix) for suffix in PREVIEW_VOLUME_SUFFIXES):
        return "volume"
    _root, extension = os.path.splitext(lowered)
    if extension in PREVIEW_IMAGE_CONTENT_TYPES:
        return "image"
    return ""


def preview_content_type(name):
    """Return the Content-Type a previewable file must be served with.

    Compressed volumes are labelled application/gzip rather than being sent
    with Content-Encoding: gzip: NiiVue decompresses .nii.gz itself, and a
    transport-level encoding would hand it an already-inflated stream whose
    name still promises gzip.
    """
    lowered = os.path.basename(str(name or "")).lower()
    if preview_kind(lowered) == "volume":
        return "application/gzip" if lowered.endswith(".gz") else (
            "application/octet-stream"
        )
    _root, extension = os.path.splitext(lowered)
    return PREVIEW_IMAGE_CONTENT_TYPES.get(extension, "application/octet-stream")


def safe_preview_path_parts(rel_path):
    """Return allow-listed path components for a preview request, or ``()``.

    OpenCode's preview hook only emits this portable filename alphabet. The
    server enforces the same contract before touching the filesystem and
    rejects absolute paths, empty/dot components, backslashes, and controls.
    """
    if not isinstance(rel_path, str) or not rel_path or "\x00" in rel_path:
        return ()
    if rel_path.startswith("/") or "\\" in rel_path:
        return ()
    parts = tuple(rel_path.split("/"))
    if any(
        part in ("", ".", "..") or not _SAFE_PREVIEW_COMPONENT_RE.fullmatch(part)
        for part in parts
    ):
        return ()
    return parts if preview_kind(parts[-1]) else ()


def _preview_open_flags(directory=False):
    """Return flags for a non-following, non-blocking descriptor open."""
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _listed_entry(dir_fd, requested):
    """Return the filesystem-owned spelling of ``requested`` in ``dir_fd``."""
    try:
        for entry in os.listdir(dir_fd):
            if entry == requested:
                return entry
    except OSError:
        pass
    return ""


def _regular_file_handle(dir_fd, entry):
    """Open one listed entry without following symlinks, returning a handle."""
    try:
        file_fd = os.open(entry, _preview_open_flags(), dir_fd=dir_fd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            os.close(file_fd)
            return None
        return os.fdopen(file_fd, "rb")
    except Exception:
        os.close(file_fd)
        raise


def _open_exact_preview_file(root_fd, parts):
    """Open ``parts`` relative to ``root_fd`` without following any symlink."""
    current_fd = os.dup(root_fd)
    try:
        for requested in parts[:-1]:
            entry = _listed_entry(current_fd, requested)
            if not entry:
                return None
            try:
                next_fd = os.open(
                    entry, _preview_open_flags(directory=True), dir_fd=current_fd
                )
            except OSError:
                return None
            os.close(current_fd)
            current_fd = next_fd
        entry = _listed_entry(current_fd, parts[-1])
        return _regular_file_handle(current_fd, entry) if entry else None
    finally:
        os.close(current_fd)


def _find_preview_file(root_fd, parts, max_entries):
    """Open the unique regular file below ``root_fd`` ending in ``parts``."""
    match = None
    seen = 0
    walker = None
    try:
        walker = os.fwalk(
            ".", topdown=True, follow_symlinks=False, dir_fd=root_fd
        )
        for dirpath, dirnames, filenames, dir_fd in walker:
            dirnames[:] = [
                name for name in dirnames
                if name not in PREVIEW_SEARCH_SKIP_DIRS
            ]
            # Directories count too, so a wide, mostly empty tree is bounded as
            # tightly as a deep one.
            seen += len(dirnames)
            if seen > max_entries:
                break
            parent_parts = tuple(
                part for part in dirpath.split(os.sep) if part not in ("", ".")
            )
            for filename in filenames:
                seen += 1
                if seen > max_entries:
                    break
                candidate_parts = parent_parts + (filename,)
                if len(candidate_parts) < len(parts) or (
                    candidate_parts[-len(parts):] != parts
                ):
                    continue
                candidate = _regular_file_handle(dir_fd, filename)
                if candidate is None:
                    continue
                if match is not None:
                    candidate.close()
                    match.close()
                    return None  # ambiguous: refuse to guess
                match = candidate
            if seen > max_entries:
                break
    except OSError:
        if match is not None:
            match.close()
        return None
    finally:
        if walker is not None:
            walker.close()
    if seen > max_entries and match is not None:
        match.close()
        return None
    return match


def open_preview_file(work_dir, parts, max_entries=PREVIEW_SEARCH_MAX_ENTRIES):
    """Open one confined preview file and return its pinned binary handle.

    ``parts`` must come from :func:`safe_preview_path_parts`. The session root
    is opened once, every descendant is selected from a directory listing and
    opened relative to its parent descriptor with ``O_NOFOLLOW``, and the
    returned regular-file descriptor is the object later sized and streamed.
    This removes both path injection and validation/open race windows.
    """
    if not work_dir or not parts or tuple(parts) != parts:
        return None
    root = os.path.realpath(os.fspath(work_dir))
    try:
        root_fd = os.open(root, _preview_open_flags(directory=True))
    except OSError:
        return None
    try:
        exact = _open_exact_preview_file(root_fd, parts)
        if exact is not None:
            return exact
        return _find_preview_file(root_fd, parts, max_entries)
    finally:
        os.close(root_fd)


# The previewer never inserts nodes into OpenCode's own DOM: it listens for
# clicks in the capture phase and renders its overlay under <body>. That keeps
# it independent of the minified markup, which changes with every upstream
# release, and makes a failed hook cost only the preview - never the UI.
PREVIEW_BOOTSTRAP_TEMPLATE = """(() => {
  "use strict";
  const CONFIG = __NEURODESK_PREVIEW_CONFIG__;
  const OVERLAY_ID = "neurodesk-preview-overlay";
  const IMAGE_RE = /\\.(?:png|jpe?g|gif|webp|bmp|svg)$/i;
  const VOLUME_RE =
    /\\.(?:nii(?:\\.gz)?|mgz|mgh|mif(?:\\.gz)?|nrrd|nhdr|mha|mhd)$/i;
  // A previewable label is a bare path token: rows that also carry diff
  // counts, prose, or code never match.
  const TOKEN_RE = /^[A-Za-z0-9._@+~/-]+$/;
  const PATH_CHAR_RE = /[A-Za-z0-9._@+~/-]/;

  const kindOf = (name) =>
    IMAGE_RE.test(name) ? "image" : VOLUME_RE.test(name) ? "volume" : "";

  // OpenCode's client sends the active project directory on every backend
  // call. Observing it costs nothing and gives the preview endpoint the same
  // directory context the session itself runs in; the server still validates
  // it against ~/opencode-work before reading anything.
  let lastDirectory = "";
  const nativeFetch = window.fetch;
  if (typeof nativeFetch === "function") {
    window.fetch = function (input, init) {
      try {
        const source =
          (init && init.headers) || (input && input.headers) || undefined;
        const directory = new Headers(source).get("x-opencode-directory");
        if (directory) {
          lastDirectory = decodeURIComponent(directory);
        } else {
          const url = new URL(
            typeof input === "string" ? input : input.url,
            location.href
          );
          const fromQuery =
            url.searchParams.get("directory") ||
            url.searchParams.get("location[directory]");
          if (fromQuery) lastDirectory = fromQuery;
        }
      } catch (_error) {
        // Never let preview bookkeeping break an OpenCode request.
      }
      return nativeFetch.apply(this, arguments);
    };
  }

  const sessionId = () => {
    const match = /ses_[A-Za-z0-9_-]+/.exec(location.pathname);
    return match ? match[0] : "";
  };

  const fileUrl = (relPath) => {
    // Path-style so the URL still ends in the real extension: viewers that
    // sniff the type from the URL get the answer the file name gives.
    const encoded = relPath
      .split("/")
      .filter(Boolean)
      .map(encodeURIComponent)
      .join("/");
    const params = new URLSearchParams();
    const session = sessionId();
    if (session) params.set("session", session);
    if (lastDirectory) params.set("dir", lastDirectory);
    const query = params.toString();
    return (
      CONFIG.prefix + CONFIG.filePath + "/" + encoded +
      (query ? "?" + query : "")
    );
  };

  // Cache-busted: the bundle is served immutable, so a NIIVUE_VERSION bump
  // has to reach browsers through a different URL.
  const niivueUrl = () =>
    CONFIG.prefix + CONFIG.niivuePath +
    (CONFIG.niivueVersion ? "?v=" + CONFIG.niivueVersion : "");

  const relativePathFor = (element, name) => {
    // The directory prefix and the file name are separate nodes; read the
    // row's text backwards from the name to recover as much of the path as
    // the markup preserved. The server resolves what is left.
    const row =
      (element.closest && element.closest("a,li,tr,div,section")) || element;
    const text = row.textContent || "";
    const end = text.lastIndexOf(name);
    if (end < 0) return name;
    let start = end;
    while (start > 0 && PATH_CHAR_RE.test(text.charAt(start - 1))) start -= 1;
    return text.slice(start, end + name.length);
  };

  const labelOf = (element) => {
    if (!element || !element.textContent) return "";
    const text = element.textContent.trim();
    if (!text || text.length > 512 || !TOKEN_RE.test(text)) return "";
    return kindOf(text) ? text : "";
  };

  // Places where a file name is an input value rather than a result: the
  // prompt box, the file-mention autocomplete, and any listbox option. A
  // click there belongs to OpenCode.
  const INTERACTIVE = "input,textarea,[contenteditable],[role=combobox]," +
    "[role=listbox],[role=option],[role=menu],[role=menuitem]";

  const previewTargetFrom = (node) => {
    let element = node instanceof Element ? node : node && node.parentElement;
    if (!element || !element.closest) return null;
    if (element.closest("#" + OVERLAY_ID) || element.closest(INTERACTIVE)) {
      return null;
    }
    const direct = labelOf(element);
    if (direct) {
      return {
        element,
        name: direct,
        path: relativePathFor(element, direct),
        kind: kindOf(direct),
      };
    }
    // Clicking anywhere on a row still previews, as long as the row names
    // exactly one previewable file.
    for (let depth = 0; depth < 3 && element; depth += 1) {
      const matches = [];
      element.querySelectorAll("*").forEach((child) => {
        if (child.children.length === 0 && labelOf(child)) matches.push(child);
      });
      if (matches.length === 1) {
        const name = labelOf(matches[0]);
        return {
          element: matches[0],
          name,
          path: relativePathFor(matches[0], name),
          kind: kindOf(name),
        };
      }
      if (matches.length > 1) return null;
      element = element.parentElement;
    }
    return null;
  };

  const STYLE = `
#${OVERLAY_ID} { position: fixed; inset: 0; z-index: 2147483000;
  background: rgba(0,0,0,.82); display: flex; flex-direction: column;
  font: 13px/1.5 system-ui, sans-serif; color: #e6e6e6; }
#${OVERLAY_ID} header { display: flex; align-items: center; gap: 12px;
  padding: 10px 14px; background: #16161a; border-bottom: 1px solid #2a2a30; }
#${OVERLAY_ID} .neurodesk-preview-name { flex: 1; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace,
  monospace; }
#${OVERLAY_ID} button { background: #2a2a30; color: inherit; border: 0;
  border-radius: 6px; padding: 6px 12px; cursor: pointer; font: inherit; }
#${OVERLAY_ID} button:hover { background: #3a3a44; }
#${OVERLAY_ID} .neurodesk-preview-body { flex: 1; display: flex;
  align-items: center; justify-content: center; overflow: auto; padding: 16px; }
#${OVERLAY_ID} img { max-width: 100%; max-height: 100%;
  background: #fff; box-shadow: 0 0 0 1px #2a2a30; }
#${OVERLAY_ID} canvas { width: 100%; height: 100%; }
#${OVERLAY_ID} .neurodesk-preview-canvas-wrap { width: 100%; height: 100%; }
#${OVERLAY_ID} .neurodesk-preview-message { max-width: 60ch; text-align: center;
  color: #b9b9c0; }
`;

  const ensureStyle = () => {
    if (document.getElementById("neurodesk-preview-style")) return;
    const style = document.createElement("style");
    style.id = "neurodesk-preview-style";
    style.textContent = STYLE;
    document.head.appendChild(style);
  };

  let closeCurrent = null;

  const openPreview = (relPath, kind) => {
    ensureStyle();
    if (closeCurrent) closeCurrent();

    const overlay = document.createElement("div");
    overlay.id = OVERLAY_ID;
    const header = document.createElement("header");
    const name = document.createElement("span");
    name.className = "neurodesk-preview-name";
    name.textContent = relPath;
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "Close";
    header.appendChild(name);
    header.appendChild(close);
    const body = document.createElement("div");
    body.className = "neurodesk-preview-body";
    overlay.appendChild(header);
    overlay.appendChild(body);

    const message = (text) => {
      body.textContent = "";
      const paragraph = document.createElement("p");
      paragraph.className = "neurodesk-preview-message";
      paragraph.textContent = text;
      body.appendChild(paragraph);
    };

    // NiiVue attaches directly to the canvas, so its own removal observer
    // never fires when an ancestor is removed. Without an explicit cleanup()
    // every preview would leak its listeners, observers, and WebGL context -
    // and browsers hand out only a handful of contexts.
    let viewer = null;
    let dismissed = false;

    const releaseViewer = () => {
      if (!viewer) return;
      try {
        viewer.cleanup();
      } catch (_error) {
        // A viewer that failed to initialize has nothing to release.
      }
      viewer = null;
    };

    const dismiss = () => {
      dismissed = true;
      releaseViewer();
      document.removeEventListener("keydown", onKey, true);
      overlay.remove();
      closeCurrent = null;
    };
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        dismiss();
      }
    };
    close.addEventListener("click", dismiss);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) dismiss();
    });
    document.addEventListener("keydown", onKey, true);
    closeCurrent = dismiss;
    document.body.appendChild(overlay);

    const url = fileUrl(relPath);
    if (kind === "image") {
      const image = document.createElement("img");
      image.alt = relPath;
      image.addEventListener("error", () => {
        message("Could not load " + relPath + " from this session's project.");
      });
      image.src = url;
      body.appendChild(image);
      return;
    }

    message("Loading " + relPath + " ...");
    const wrap = document.createElement("div");
    wrap.className = "neurodesk-preview-canvas-wrap";
    const canvas = document.createElement("canvas");
    wrap.appendChild(canvas);
    import(niivueUrl())
      .then((module) => {
        // The overlay may already be gone: closing during a 3 MB import or a
        // slow volume load must not attach a viewer nobody can dismiss.
        if (dismissed) return undefined;
        body.textContent = "";
        body.appendChild(wrap);
        viewer = new module.Niivue({
          backColor: [0, 0, 0, 1],
          dragAndDropEnabled: false,
          show3Dcrosshair: true,
        });
        viewer.attachToCanvas(canvas);
        return viewer.loadVolumes([
          { url, name: relPath.split("/").pop() },
        ]).then(() => {
          if (dismissed) releaseViewer();
        });
      })
      .catch((error) => {
        releaseViewer();
        if (dismissed) return;
        message(
          "Could not render " + relPath + " with NiiVue: " +
          (error && error.message ? error.message : String(error))
        );
      });
  };

  document.addEventListener(
    "click",
    (event) => {
      if (
        event.button !== 0 || event.defaultPrevented || event.metaKey ||
        event.ctrlKey || event.shiftKey || event.altKey
      ) {
        return;
      }
      const target = previewTargetFrom(event.target);
      if (!target) return;
      event.preventDefault();
      event.stopPropagation();
      openPreview(target.path, target.kind);
    },
    true
  );

  document.addEventListener(
    "mouseover",
    (event) => {
      const target = previewTargetFrom(event.target);
      if (!target || !target.element.dataset) return;
      if (target.element.dataset.neurodeskPreview) return;
      target.element.dataset.neurodeskPreview = target.kind;
      target.element.style.cursor = "zoom-in";
      if (!target.element.title) target.element.title = "Preview " + target.path;
    },
    true
  );
})();
"""


def preview_bootstrap_script(prefix):
    """Return the injected previewer, bound to this request's proxy prefix."""
    config = json.dumps({
        "prefix": safe_forwarded_prefix(prefix),
        "filePath": FILE_PREVIEW_PATH,
        "niivuePath": NIIVUE_ASSET_PATH,
        "niivueVersion": niivue_bundle_version(),
    })
    return PREVIEW_BOOTSTRAP_TEMPLATE.replace(
        "__NEURODESK_PREVIEW_CONFIG__", config
    )


def rewrite_html(body, prefix):
    """Prefix root-absolute attribute, CSS, and JS asset URLs in HTML."""
    body = _HTML_ATTR_RE.sub(rf"\g<1>\g<2>{prefix}/", body)
    body = _CSS_URL_RE.sub(rf"\g<1>\g<2>{prefix}/", body)
    body = _JS_STRING_ASSET_PATH_RE.sub(rf"\g<1>{prefix}/assets/", body)
    scripts = ""
    if PREFIX_BOOTSTRAP_PATH not in body:
        scripts += f'<script src="{prefix}{PREFIX_BOOTSTRAP_PATH}"></script>'
    if PREVIEW_BOOTSTRAP_PATH not in body:
        # Deferred: the previewer only needs a parsed document, and must never
        # delay the bootstrap the SPA itself depends on.
        scripts += (
            f'<script defer src="{prefix}{PREVIEW_BOOTSTRAP_PATH}"></script>'
        )
    if scripts:
        body = _inject_after_head(body, scripts)
    return body


def rewrite_css(body, prefix):
    """Prefix root-absolute url(...) references in CSS."""
    return _CSS_URL_RE.sub(rf"\g<1>\g<2>{prefix}/", body)


def rewrite_js(body, prefix):
    """Keep OpenCode's assets, API client, registry, and router prefixed.

    OpenCode 1.18's web entry registers ``location.origin`` as its only server.
    Merely storing the prefixed URL as the selected default therefore makes the
    permission provider reject it as unknown. The exact pinned-bundle origin
    expression is rewritten to the bootstrap URL so the selected default and
    registered canonical server have the same key. The SPA router must receive
    the forwarded prefix as its base so it strips that prefix before matching
    ``/:dir`` and adds it back to generated browser-history URLs.

    The router base only affects route matching: ``useLocation().pathname``
    keeps the raw browser path. OpenCode's layout feeds that raw path to its
    own route parser, which reads the first segment (``opencode``) as a
    project directory and misclassifies every session URL as ``home`` — the
    titlebar Home button then silently re-selects the current session instead
    of navigating. The parser rewrite strips the forwarded prefix first.
    """
    body = _JS_STRING_ASSET_PATH_RE.sub(rf"\g<1>{prefix}/assets/", body)
    home_url = json.dumps(f"{prefix}/")

    def rewrite_home_toggle(match):
        groups = match.groupdict()
        return (
            f'{groups["binding"]}=()=>'
            f'{groups["layout"]}.route().type==="home"?'
            f'{groups["tabs"]}.toggleHome({{home:!0,current:'
            f'{groups["current"]}()}}):location.assign({home_url})'
        )

    # Linear prefilter: the possessive quantifiers stop per-position
    # backtracking, but an unanchored scan over a long identifier-character
    # run would still cost O(n^2). Bodies without the literal cannot match.
    if ".toggleHome({home:" in body:
        body = _JS_HOME_TOGGLE_RE.sub(rewrite_home_toggle, body)
    body = body.replace(
        OPENCODE_WEB_ORIGIN_EXPRESSION,
        OPENCODE_PREFIXED_WEB_ORIGIN_EXPRESSION,
    )
    body = body.replace(
        OPENCODE_WEB_ROUTER_COMPONENT_EXPRESSION,
        OPENCODE_PREFIXED_WEB_ROUTER_COMPONENT_EXPRESSION,
    )
    body = body.replace(
        OPENCODE_WEB_ROUTE_PARSER_EXPRESSION,
        OPENCODE_PREFIXED_WEB_ROUTE_PARSER_EXPRESSION,
    )
    return body.replace(
        OPENCODE_SSE_HEADERS_EXPRESSION,
        OPENCODE_STREAMING_SSE_HEADERS_EXPRESSION,
    )


def rewrite_body(body, content_type, prefix):
    """Dispatch body rewriting by content type; no-op without a prefix."""
    if not prefix:
        return body
    if content_type.startswith("text/html"):
        return rewrite_html(body, prefix)
    if content_type.startswith("text/css"):
        return rewrite_css(body, prefix)
    return rewrite_js(body, prefix)


# --- Backend process management ----------------------------------------------


def initialize_git_project(directory):
    """Make ``directory`` a Git worktree root, including legacy directories."""
    try:
        subprocess.run(
            ["git", "init", "--quiet", os.fspath(directory)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise OSError(
            f"could not initialize OpenCode project in {directory}: "
            f"{detail.strip()}"
        ) from exc


def commit_opencode_project_identity(directory):
    """Give a new session worktree a unique, durable OpenCode project id.

    OpenCode identifies a Git project by its remote, cached id, or root commit.
    A freshly initialized repository has none of those and therefore collapses
    to the shared ``global`` project. The unique directory name in this initial
    commit message guarantees a distinct root commit even when two otherwise
    identical workspaces are created during the same second.
    """
    name = os.path.basename(os.path.normpath(os.fspath(directory)))
    try:
        subprocess.run(
            [
                "git",
                "-C",
                os.fspath(directory),
                "-c",
                "user.name=Neurodesk",
                "-c",
                "user.email=neurodesk@localhost",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                "add",
                "--all",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                os.fspath(directory),
                "-c",
                "user.name=Neurodesk",
                "-c",
                "user.email=neurodesk@localhost",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "--quiet",
                "--allow-empty",
                "-m",
                f"Initialize OpenCode session workspace {name}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise OSError(
            f"could not commit OpenCode project identity in {directory}: "
            f"{detail.strip()}"
        ) from exc


def create_opencode_work_root(home_dir):
    """Create the stable backend cwd above all per-session projects."""
    parent = os.path.join(os.fspath(home_dir), OPENCODE_WORK_DIR_PARENT)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    initialize_git_project(parent)
    return parent


def create_opencode_work_dir(home_dir, timestamp=None, agents_file=None):
    """Create and seed one unique ~/opencode-work/DATE_TIME session project.

    The session directory itself must be the Git worktree root. OpenCode
    resolves a request's ``directory`` to a project by walking up for ``.git``
    and running ``git rev-parse --show-toplevel``, then uses that worktree as
    the session directory and tool cwd. When only the ``~/opencode-work``
    parent was the only repository, every session directory below it resolved up
    to the parent, so sessions ran in the shared parent, wrote their outputs
    there, and never saw the ``AGENTS.md`` intended for that session.

    ``git init`` on the session directory keeps it its own worktree even when a
    legacy ``~/opencode-work/.git`` from an earlier release still exists: the
    upward walk stops at the nearest ``.git``. A unique initial commit prevents
    empty repositories from collapsing to OpenCode's shared ``global`` project
    identity. ``agents_file`` is copied only into the newly-created project and
    is never used to overwrite user edits.
    """
    parent = create_opencode_work_root(home_dir)
    timestamp = timestamp or time.strftime(OPENCODE_WORK_DIR_TIMESTAMP_FORMAT)
    work_dir = None
    for sequence in range(1, 1001):
        name = timestamp if sequence == 1 else f"{timestamp}_{sequence}"
        candidate = os.path.join(parent, name)
        try:
            os.mkdir(candidate, mode=0o700)
            work_dir = candidate
            break
        except FileExistsError:
            continue
    if work_dir is None:
        raise OSError(
            f"could not create a unique OpenCode work directory in {parent}"
        )

    initialize_git_project(work_dir)
    if agents_file:
        try:
            shutil.copy2(
                os.fspath(agents_file), os.path.join(work_dir, "AGENTS.md")
            )
        except OSError as exc:
            raise OSError(
                f"could not seed OpenCode project {work_dir} from "
                f"{agents_file}: {exc}"
            ) from exc
    commit_opencode_project_identity(work_dir)
    return work_dir


def requested_opencode_directory(query, header):
    """Decode the directory context supplied by OpenCode's browser client."""
    for key, value in urllib.parse.parse_qsl(query or "", keep_blank_values=True):
        if key in OPENCODE_DIRECTORY_QUERY_KEYS and value:
            return value
    return urllib.parse.unquote(header) if header else ""


def is_opencode_session_work_dir(directory, home_dir):
    """Return whether ``directory`` is an existing direct session project."""
    if not directory:
        return False
    parent = os.path.realpath(
        os.path.join(os.fspath(home_dir), OPENCODE_WORK_DIR_PARENT)
    )
    candidate = os.path.realpath(os.fspath(directory))
    return (
        os.path.dirname(candidate) == parent
        and os.path.isdir(candidate)
        and os.path.isdir(os.path.join(candidate, ".git"))
    )


def session_id_from_path(path):
    """Extract a concrete OpenCode session id from an API path, if present."""
    match = OPENCODE_SESSION_PATH_RE.match(path)
    return match.group(1) if match else ""


def is_session_creation_request(method, path):
    """Recognize the finite API call that creates a top-level UI session."""
    return method == "POST" and path.rstrip("/") in ("/session", "/api/session")


def force_directory_query(query, directory):
    """Pin a backend request's directory query parameters to ``directory``.

    OpenCode's web API takes the session/workspace directory as a
    ``?directory=`` query parameter (``?location[directory]=`` on its /api/
    routes). Rewriting that value to the directory selected by the proxy keeps
    each session, file, pty, and search operation inside its own seeded
    ~/opencode-work/DATE_TIME project.

    Requests that carry no directory parameter are returned unchanged: the
    backend then falls back to the ``x-opencode-directory`` header (pinned by
    ``force_directory_header``) and finally to its process cwd. Passing an
    empty ``directory`` leaves the query untouched.
    """
    if not directory or not query:
        return query
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    if not any(key in OPENCODE_DIRECTORY_QUERY_KEYS for key, _ in pairs):
        return query
    forced = [
        (key, directory if key in OPENCODE_DIRECTORY_QUERY_KEYS else value)
        for key, value in pairs
    ]
    return urllib.parse.urlencode(forced)


def force_directory_header(value, directory):
    """Pin the ``x-opencode-directory`` request header to ``directory``.

    The query parameter alone does not pin anything that matters. OpenCode's
    web client only mirrors its directory into the query string for GET and
    HEAD requests; for every other method it travels *solely* in the
    ``x-opencode-directory`` header, and the server resolves the request
    directory as ``?directory=`` -> that header -> ``process.cwd()``. So
    ``POST /session`` and ``POST /session/:id/message`` -- the calls that
    decide where a session lives and where its tools run -- bypassed the query
    rewrite entirely and ran in whatever directory the SPA happened to hold.

    The value is percent-encoded because that is what OpenCode's own client
    sends (``encodeURIComponent(directory)``) and what its header-reading path
    decodes. An empty ``directory`` leaves the header untouched.
    """
    if not directory:
        return value
    return urllib.parse.quote(directory, safe="")


class OpencodeBackend:
    """Owns the `opencode web` child process and its readiness state."""

    def __init__(self, wrapper_bin, startup_timeout, home_dir, agents_file):
        self.wrapper_bin = wrapper_bin
        self.startup_timeout = startup_timeout
        self.home_dir = home_dir
        self.agents_file = agents_file
        self.work_dir = None
        self.port = None
        self.process = None
        self.state = "not_started"  # not_started | starting | ready | failed
        self.log_tail = collections.deque(maxlen=100)
        self._lock = threading.Lock()
        self._session_lock = threading.Lock()
        self.session_work_dirs = {}
        self.backend_password = ""

    def create_session_work_dir(self):
        """Allocate a fresh seeded project for one POST /session request."""
        return create_opencode_work_dir(
            self.home_dir, agents_file=self.agents_file
        )

    def remember_session_work_dir(self, session_id, directory):
        """Pin ``session_id`` to a validated per-session project directory."""
        if not session_id or not is_opencode_session_work_dir(
            directory, self.home_dir
        ):
            return False
        canonical = os.path.realpath(directory)
        with self._session_lock:
            self.session_work_dirs[session_id] = canonical
        return True

    def work_dir_for_request(self, path, query, directory_header):
        """Resolve a request to its recorded or validated session project."""
        session_id = session_id_from_path(path)
        if session_id:
            with self._session_lock:
                recorded = self.session_work_dirs.get(session_id, "")
            if is_opencode_session_work_dir(recorded, self.home_dir):
                return recorded

        requested = requested_opencode_directory(query, directory_header)
        if is_opencode_session_work_dir(requested, self.home_dir):
            canonical = os.path.realpath(requested)
            if session_id:
                self.remember_session_work_dir(session_id, canonical)
            return canonical
        return self.work_dir

    def preview_work_dir(self, session_id, directory):
        """Resolve the project a file preview may be read from, or "".

        Fails closed: only a validated ``~/opencode-work/DATE_TIME`` project is
        ever returned, never the shared parent. Falling back to the parent
        would widen the preview lookup across every session, so a stale or
        unknown session id could surface a uniquely named artifact belonging
        to a different one. A request that names a session must resolve to
        that session; a request that names none is only answered when exactly
        one session is known and therefore unambiguous.
        """
        query = urllib.parse.urlencode(
            {"directory": directory}
        ) if directory else ""
        if session_id:
            path = f"/session/{session_id}"
            if not session_id_from_path(path):
                return ""
            resolved = self.work_dir_for_request(path, query, "")
            return resolved if is_opencode_session_work_dir(
                resolved, self.home_dir
            ) else ""

        resolved = self.work_dir_for_request("", query, "")
        if is_opencode_session_work_dir(resolved, self.home_dir):
            return resolved
        with self._session_lock:
            known = sorted(set(self.session_work_dirs.values()))
        return known[0] if len(known) == 1 else ""

    def remember_created_session(self, payload, directory):
        """Record the session id returned by a successful creation response."""
        try:
            session = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(session, dict):
            return False
        session_id = session.get("id", "")
        returned_directory = session.get("directory", "")
        if returned_directory and os.path.realpath(returned_directory) != (
            os.path.realpath(directory)
        ):
            return False
        return self.remember_session_work_dir(session_id, directory)

    def start(self, backend_password):
        """Spawn `opencode web` via the terminal wrapper (idempotent)."""
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
        # OpenCode executes agent tools with non-interactive ``bash -c``
        # shells, which do not read ~/.bashrc and therefore lack Lmod's
        # ``module`` function. BASH_ENV is Bash's supported initialization
        # hook for these shells. The dedicated file also refreshes MODULEPATH
        # so a CVMFS mount that completed after Jupyter startup is visible.
        env["BASH_ENV"] = OPENCODE_BASH_ENV
        # OpenCode 1.18.x's native FFF indexer refuses to initialize when its
        # workspace is a filesystem root or the user's home directory. The
        # web launcher intentionally starts in HOME so users can choose any
        # project below it; when FFF fails, OpenCode installs an empty search
        # service and the Add Project dialog cannot discover directories.
        # Force OpenCode's supported ripgrep search backend for this process.
        env["OPENCODE_DISABLE_FFF"] = "1"
        # The web launcher defaults to Neurodesk independently of a model
        # selected in terminal OpenCode. An explicit environment override is
        # still respected.
        if not env.get("OPENCODE_MODEL_PROFILE"):
            env["OPENCODE_MODEL_PROFILE"] = OPENCODE_WEB_DEFAULT_MODEL_PROFILE

        try:
            if self.work_dir is None:
                self.work_dir = create_opencode_work_root(self.home_dir)
            self.process = subprocess.Popen(
                [
                    self.wrapper_bin,
                    "web",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                ],
                cwd=self.work_dir,
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
        """Mirror backend output to our stdout and keep a tail for errors."""
        for raw_line in self.process.stdout:
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            self.log_tail.append(line)
            print(f"[opencode] {line}", flush=True)

    def _wait_ready(self):
        """Poll the backend until it answers HTTP or the timeout expires."""
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
        """Stop the backend process, escalating to SIGKILL if needed."""
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
    """Render the llm.neurodesk.org API key setup page."""
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
<p>After OpenCode opens, use the <strong>model picker</strong> in the prompt
toolbar to choose any available model from Neurodesk, local Ollama, or
JetStream. You can change the model again for each prompt.</p>
<p class="muted">The key is saved to <code>~/.bashrc</code> as
<code>NEURODESK_API_KEY</code>, exactly like the terminal setup.</p>
</div></body></html>"""


def waiting_page():
    """Render the auto-refreshing backend-startup page."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scigent.ai starting up...</title><style>{PAGE_STYLE}</style></head>
<body><div class="card" style="text-align:center">
<div class="spinner" style="margin-left:auto;margin-right:auto"></div>
<h1>Scigent.ai starting up&hellip;</h1>
<p class="muted">Checking model providers and launching the web interface.
This page refreshes automatically.</p>
</div></body></html>"""


def failed_page(log_tail):
    """Render the backend-failure page with recent log output."""
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
    # Current single-use ?auth= login token; rotated on every successful use.
    # The lock makes compare-and-rotate atomic so concurrent requests cannot
    # both consume the same token under the threading server.
    login_token = ""
    login_token_file = ""
    login_token_lock = threading.Lock()
    backend = None
    llm_base_url = DEFAULT_LLM_BASE_URL
    home_dir = ""
    key_skipped = False

    # -- helpers --

    def log_message(self, fmt, *args):
        """Write handler log lines to stderr with the launcher's tag."""
        sys.stderr.write("opencode_web: %s\n" % (fmt % args))

    def log_request(self, code="-", size="-"):
        """Log the request line with ?auth= credentials redacted."""
        self.log_message(
            '"%s" %s %s', redact_auth_params(self.requestline),
            str(code), str(size),
        )

    def external_prefix(self):
        """Return the validated external proxy prefix for this request."""
        for header in ("X-Forwarded-Prefix", "X-Forwarded-Context",
                       "X-ProxyContextPath"):
            value = self.headers.get(header)
            if value:
                return safe_forwarded_prefix(value)
        return ""

    def expected_authorization(self):
        """Return the Authorization header value the proxy must inject."""
        credentials = base64.b64encode(
            f"{BACKEND_USERNAME}:{self.proxy_password}".encode()
        ).decode()
        return f"Basic {credentials}"

    def is_authorized(self):
        """Check the injected Authorization header or the session cookie."""
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
        """Send a complete HTML page with no-store caching."""
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

    def send_javascript(self, status, body):
        """Send a generated same-origin JavaScript response without caching."""
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def redirect(self, location, extra_headers=None):
        """Send a 303 to a normalized same-origin path."""
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

    # -- file previews --

    def send_open_file(self, handle, content_type, cache_control, size, etag=""):
        """Stream an already-open regular file and close it afterwards."""
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", cache_control)
            if etag:
                self.send_header("ETag", f'"{etag}"')
            # Previewed bytes are user data: never let a browser sniff them
            # into something executable, and never let them frame the desktop.
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Disposition", "inline")
            self.end_headers()
            if self.command != "HEAD":
                shutil.copyfileobj(handle, self.wfile, STREAM_CHUNK_SIZE)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            handle.close()

    def send_niivue_bundle(self):
        """Serve the NiiVue viewer vendored into the image at build time."""
        path = niivue_bundle_path()
        handle = None
        try:
            handle = open(path, "rb")
            size = os.fstat(handle.fileno()).st_size
        except OSError:
            if handle is not None:
                handle.close()
            self.send_page(
                404,
                "<h1>404</h1><p>The NiiVue viewer bundle is not installed in "
                "this image.</p>",
            )
            return
        # Cached hard, but only ever requested through the content-versioned
        # URL the previewer builds, so a bundle upgrade is picked up at once.
        self.send_open_file(
            handle,
            "text/javascript; charset=utf-8",
            "public, max-age=31536000, immutable",
            size,
            etag=niivue_bundle_version(path),
        )

    def send_preview_file(self, parsed, query):
        """Serve one previewable file from the requesting session's project."""
        backend = type(self).backend
        encoded_path = parsed.path[len(FILE_PREVIEW_PATH):]
        rel_path = urllib.parse.unquote(encoded_path[1:]) if (
            encoded_path.startswith("/")
        ) else ""
        if not preview_kind(rel_path):
            self.send_page(
                415,
                "<h1>415</h1><p>Only image and volume files can be "
                "previewed.</p>",
            )
            return

        parts = safe_preview_path_parts(rel_path)
        if not parts:
            self.send_page(
                404,
                "<h1>404</h1><p>No single file matching that name exists in "
                "this session's project.</p>",
            )
            return

        work_dir = backend.preview_work_dir(
            query.get("session", [""])[0], query.get("dir", [""])[0]
        ) if backend else ""
        handle = open_preview_file(work_dir, parts)
        if handle is None:
            self.send_page(
                404,
                "<h1>404</h1><p>No single file matching that name exists in "
                "this session's project.</p>",
            )
            return

        try:
            size = os.fstat(handle.fileno()).st_size
        except OSError:
            handle.close()
            self.send_page(
                404,
                "<h1>404</h1><p>No single file matching that name exists in "
                "this session's project.</p>",
            )
            return
        limit = preview_size_limit()
        if size > limit:
            handle.close()
            self.send_page(
                413,
                f"<h1>413</h1><p>This file is {size} bytes; the preview limit "
                f"is {limit} bytes.</p>",
            )
            return
        # Agents rewrite their outputs in place, so a preview must always show
        # the current bytes.
        self.send_open_file(
            handle, preview_content_type(parts[-1]), "no-store", size
        )

    # -- request routing --

    def handle_any(self):
        """Route a request: login exchange, auth check, setup, or proxy."""
        cls = type(self)
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        # Desktop flow: exchange the single-use ?auth=<login token> for a
        # cookie. Compare-and-rotate happens under a lock so exactly one
        # request can consume a token, and a URL that leaked via browser
        # history or logs cannot be replayed.
        auth_values = query.pop("auth", [])
        consumed_token = False
        if auth_values:
            with cls.login_token_lock:
                if cls.login_token and hmac.compare_digest(
                    auth_values[0], cls.login_token
                ):
                    consumed_token = True
                    try:
                        cls.login_token = rotate_login_token(
                            cls.login_token_file
                        )
                    except OSError as exc:
                        cls.login_token = ""
                        self.log_message(
                            "failed to rotate login token: %s", exc
                        )
        if consumed_token:
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

        if parsed.path == PREFIX_BOOTSTRAP_PATH and self.command in (
            "GET", "HEAD"
        ):
            self.send_javascript(
                200, prefix_bootstrap_script(self.external_prefix())
            )
            return

        if parsed.path == PREVIEW_BOOTSTRAP_PATH and self.command in (
            "GET", "HEAD"
        ):
            self.send_javascript(
                200, preview_bootstrap_script(self.external_prefix())
            )
            return

        if parsed.path == NIIVUE_ASSET_PATH and self.command in ("GET", "HEAD"):
            self.send_niivue_bundle()
            return

        if self.command in ("GET", "HEAD") and (
            parsed.path == FILE_PREVIEW_PATH
            or parsed.path.startswith(FILE_PREVIEW_PATH + "/")
        ):
            self.send_preview_file(parsed, query)
            return

        if parsed.path == SETUP_PATH and self.command == "POST":
            self.handle_setup_post()
            return

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
        """Validate and persist a submitted key, or record a skip."""
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
            # Without persistence the key would silently vanish on the next
            # restart; surface the failure instead of starting the backend.
            self.log_message("failed to persist key to %s: %s", bashrc_path, exc)
            self.send_page(
                200,
                setup_page(
                    prefix,
                    f"The key could not be saved to {bashrc_path} ({exc}). "
                    "Please fix the home directory permissions and try again.",
                ),
            )
            return
        os.environ["NEURODESK_API_KEY"] = key
        self.log_message("neurodesk key setup: %s", message)

        cls.backend.start(self.proxy_password)
        self.redirect(f"{prefix}/" if prefix else "/")

    # -- reverse proxy --

    def send_upstream_payload(self, response, payload):
        """Forward one finite upstream response with deterministic framing."""
        self.send_response(response.status)
        for name, value in response.getheaders():
            if name.lower() in HOP_BY_HOP_HEADERS or name.lower() == (
                "content-length"
            ):
                continue
            self.send_header(
                sanitize_header_value(name), sanitize_header_value(value)
            )
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def proxy_request(self, parsed):
        """Forward the request to `opencode web`, rewriting text bodies."""
        backend = type(self).backend
        creates_session = is_session_creation_request(self.command, parsed.path)
        try:
            if creates_session:
                request_work_dir = backend.create_session_work_dir()
            else:
                request_work_dir = backend.work_dir_for_request(
                    parsed.path,
                    parsed.query,
                    self.headers.get(OPENCODE_DIRECTORY_HEADER, ""),
                )
        except OSError as exc:
            self.send_page(
                500,
                "<h1>500</h1><p>Could not create the OpenCode session "
                f"workspace: {html.escape(str(exc))}</p>",
            )
            return

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
            if lowered == OPENCODE_DIRECTORY_HEADER:
                continue
            headers[name] = value
        session_id = session_id_from_path(parsed.path)
        carries_directory = (
            creates_session
            or bool(session_id)
            or bool(self.headers.get(OPENCODE_DIRECTORY_HEADER))
            or any(
                key in OPENCODE_DIRECTORY_QUERY_KEYS
                for key, _value in urllib.parse.parse_qsl(
                    parsed.query, keep_blank_values=True
                )
            )
        )
        if carries_directory:
            headers[OPENCODE_DIRECTORY_HEADER] = force_directory_header(
                "", request_work_dir
            )
        headers["Host"] = f"127.0.0.1:{backend.port}"
        credentials = base64.b64encode(
            f"{BACKEND_USERNAME}:{backend.backend_password}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {credentials}"
        # Ask for identity encoding so body rewriting sees plain text.
        headers["Accept-Encoding"] = "identity"

        forced_query = force_directory_query(parsed.query, request_work_dir)
        target = parsed.path + (f"?{forced_query}" if forced_query else "")
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
        event_stream = content_type.partition(";")[0].strip().lower() == (
            "text/event-stream"
        )
        prefix = self.external_prefix()
        rewritable = prefix and any(
            content_type.startswith(ct) for ct in REWRITABLE_CONTENT_TYPES
        )

        try:
            if creates_session:
                payload = response.read()
                if 200 <= response.status < 300 and not (
                    backend.remember_created_session(payload, request_work_dir)
                ):
                    self.log_message(
                        "OpenCode session response did not preserve allocated "
                        "workspace %s", request_work_dir,
                    )
                self.send_upstream_payload(response, payload)
                return

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
                    self.send_header(
                        sanitize_header_value(name),
                        sanitize_header_value(value),
                    )
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
                self.send_header(
                    sanitize_header_value(name),
                    sanitize_header_value(value),
                )
            downstream_chunked = event_stream and not has_length
            if downstream_chunked:
                # Jupyter Server Proxy progressively flushes explicit SSE
                # requests, but its HTTP client still needs valid message
                # framing from this intermediary. Re-frame the decoded
                # upstream chunks instead of turning an unbounded event feed
                # into a close-delimited response that Jupyter buffers.
                self.send_header("Transfer-Encoding", "chunked")
            elif not has_length:
                # Other unbounded upstream responses retain close-delimited
                # framing; SSE is explicitly re-chunked above.
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command == "HEAD":
                return
            while True:
                chunk = response.read1(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                if downstream_chunked:
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                else:
                    self.wfile.write(chunk)
                self.wfile.flush()
            if downstream_chunked:
                self.wfile.write(b"0\r\n\r\n")
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
    """Threaded server so streaming responses do not block other requests."""

    daemon_threads = True
    allow_reuse_address = True


def serve(argv=None):
    """Entry point: prepare credentials, warm-start, and serve the proxy."""
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
        agents_file=os.environ.get(
            "OPENCODE_WEB_AGENTS_FILE", DEFAULT_AGENTS_FILE
        ),
    )

    OpencodeWebHandler.proxy_password = password
    OpencodeWebHandler.cookie_token = secrets.token_urlsafe(32)
    OpencodeWebHandler.login_token_file = login_token_file_path(args.port)
    OpencodeWebHandler.login_token = rotate_login_token(
        OpencodeWebHandler.login_token_file
    )
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
        """Terminate the backend and stop serving on SIGTERM/SIGINT."""
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
