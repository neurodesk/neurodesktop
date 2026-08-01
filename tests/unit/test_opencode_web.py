"""Tests for the OpenCode web launcher (config/agents/opencode_web.py).

Covers the pieces that make the JupyterLab "OpenCode Web" tile work: the
prefix rewriting for the upstream web UI, the browser-based
llm.neurodesk.org key setup (persisted in the same ~/.bashrc format the
terminal wrapper and nbi_setup.sh read), the per-user credential handling,
and the reverse proxy in front of `opencode web`.
"""

import base64
import http.client
import http.server
import json
import os
import re
import shutil
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

from testlib import load_source_module, repo_path, resolve_source


def opencode_web_module_path():
    """Locate opencode_web.py in the image or the repo."""
    return resolve_source(
        "/opt/neurodesktop/opencode_web.py", "config/agents/opencode_web.py"
    )


def opencode_bash_env_path():
    """Locate the non-interactive OpenCode Bash initializer."""
    return resolve_source(
        "/opt/neurodesktop/opencode_bash_env.sh", "config/agents/opencode_bash_env.sh"
    )


ocw = load_source_module(
    "opencode_web", "/opt/neurodesktop/opencode_web.py", "config/agents/opencode_web.py"
)


# --- URL rewriting -------------------------------------------------------------

PREFIX = "/user/alice/opencode"


def test_rewrite_html_prefixes_root_absolute_references():
    """Root-absolute attribute/CSS/JS references gain the proxy prefix."""
    html = (
        '<html><head><script type="module" src="/assets/index.js"></script>'
        '<link rel="stylesheet" href="/assets/app.css">'
        '</head><body>'
        '<form action="/submit">'
        '<img src="//cdn.example.org/logo.png">'
        '<a href="https://opencode.ai/docs">docs</a>'
        '<style>body { background: url(/assets/bg.png); }</style>'
        '<script>const f = "/assets/font.woff2";</script>'
        '</body></html>'
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
    bootstrap = f'<script src="{PREFIX}/neurodesk-prefix.js"></script>'
    assert bootstrap in rewritten
    assert rewritten.index(bootstrap) < rewritten.index('type="module"')


def test_inject_after_head_targets_only_the_real_head_tag():
    """The bootstrap insertion scans linearly (no regex backtracking) and
    must skip lookalike tags such as <header>."""
    assert ocw._inject_after_head("<head><body>", "X") == "<head>X<body>"
    assert ocw._inject_after_head(
        '<head lang="en"><body>', "X"
    ) == '<head lang="en">X<body>'
    # <header> is not <head>.
    assert ocw._inject_after_head(
        "<header>h</header><head><b>", "X"
    ) == "<header>h</header><head>X<b>"
    # No head tag or unterminated tag: prepend.
    assert ocw._inject_after_head("<body>x</body>", "X") == "X<body>x</body>"
    assert ocw._inject_after_head("<head <head <head ", "X").startswith("X")


def test_prefix_bootstrap_sets_opencode_server_to_proxy_path():
    """The bootstrap points OpenCode's API client at the Jupyter proxy.

    OpenCode 1.18 uses root API routes such as /provider and /global/config.
    Setting its native default-server URL makes every current and future API
    route (including the model picker) stay below the forwarded prefix.
    """
    script = ocw.prefix_bootstrap_script(PREFIX)
    assert "opencode.settings.dat:defaultServerUrl" in script
    assert "window.location.origin" in script
    assert "window.__NEURODESK_OPENCODE_SERVER_URL__ = server" in script
    assert "window.__NEURODESK_OPENCODE_BASE_PATH__ = prefix" in script
    assert json.dumps(PREFIX) in script
    assert "/provider" not in script


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to execute the prefix bootstrap",
)
def test_prefix_bootstrap_migrates_stale_browser_server_state(tmp_path):
    """A pre-prefix server and its drafts must stay usable after an upgrade."""
    origin = "http://127.0.0.1:8888"
    server = origin + PREFIX
    external_server = "http://127.0.0.1:4096"
    scoped_session = origin + "\0session-1"
    initial_storage = {
        "opencode.settings.dat:defaultServerUrl": origin,
        # OpenCode imports these legacy keys into the namespaced stores during
        # hydration. They must be migrated before that import can recreate a
        # second, unprefixed server context.
        "server.v3": json.dumps(
            {
                "list": [
                    {"type": "http", "http": {"url": origin}},
                    {"type": "http", "http": {"url": external_server}},
                ],
                "projects": {origin: [{"worktree": "/legacy-work"}]},
                "lastProject": {origin: "/legacy-work"},
            }
        ),
        "home.servers.v1": json.dumps(
            {"collapsed": {origin: True, external_server: False}}
        ),
        "layout.v6": json.dumps(
            {
                "home": {"selection": {"server": origin}},
                "sessionTabs": {scoped_session: {"all": ["legacy"]}},
            }
        ),
        "opencode.global.dat:server": json.dumps(
            {
                "list": [
                    {"type": "http", "http": {"url": origin}},
                    {"type": "http", "http": {"url": external_server}},
                ],
                # Exercise a collision in the least convenient order: the
                # valid prefixed state exists before the stale origin state.
                "projects": {
                    server: [{"worktree": "/prefixed-work"}],
                    origin: [{"worktree": "/work"}],
                },
                "lastProject": {
                    server: "/prefixed-work",
                    origin: "/work",
                },
            }
        ),
        "opencode.global.dat:layout": json.dumps(
            {
                "home": {"selection": {"server": origin}},
                "sessionTabs": {scoped_session: {"all": ["review"]}},
            }
        ),
        "opencode.window.browser.dat:tabs": json.dumps(
            [
                {
                    "type": "draft",
                    "draftID": "draft-1",
                    "server": origin,
                    "directory": "/work",
                },
                {
                    "type": "draft",
                    "draftID": "external-draft",
                    "server": external_server,
                    "directory": "/external",
                },
            ]
        ),
        "opencode.window.browser.dat:tabs.closed": json.dumps(
            [
                {
                    "tab": {
                        "type": "session",
                        "server": origin,
                        "sessionId": "ses_1",
                    }
                }
            ]
        ),
        # State outside the routing stores must not be rewritten merely
        # because user-authored text contains the Jupyter origin.
        "opencode.global.dat:prompt-history": json.dumps(
            {"items": [f"Explain {origin}"]}
        ),
        # One corrupt state entry must not prevent the remaining migration.
        "opencode.window.browser.dat:tabs.info": "not-json",
    }
    harness = tmp_path / "prefix-bootstrap.js"
    harness.write_text(
        "const values = new Map(Object.entries("
        + json.dumps(initial_storage)
        + "));\n"
        "const localStorage = {\n"
        "  get length() { return values.size; },\n"
        "  key(index) { return [...values.keys()][index] ?? null; },\n"
        "  getItem(key) { return values.get(key) ?? null; },\n"
        "  setItem(key, value) { values.set(key, String(value)); },\n"
        "  removeItem(key) { values.delete(key); },\n"
        "};\n"
        f"global.window = {{ location: {{ origin: {json.dumps(origin)} }}, "
        "localStorage };\n"
        + ocw.prefix_bootstrap_script(PREFIX)
        + ocw.prefix_bootstrap_script(PREFIX)
        + "\nprocess.stdout.write(JSON.stringify(Object.fromEntries(values)));\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(harness)], check=True, capture_output=True, text=True
    )
    migrated = json.loads(result.stdout)

    assert migrated["opencode.settings.dat:defaultServerUrl"] == server

    legacy_server = json.loads(migrated["server.v3"])
    assert legacy_server["list"][0]["http"]["url"] == server
    assert legacy_server["list"][1]["http"]["url"] == external_server
    assert server in legacy_server["projects"]
    assert legacy_server["lastProject"][server] == "/legacy-work"
    legacy_home = json.loads(migrated["home.servers.v1"])
    assert legacy_home["collapsed"] == {
        server: True,
        external_server: False,
    }
    legacy_layout = json.loads(migrated["layout.v6"])
    assert legacy_layout["home"]["selection"]["server"] == server
    assert server + "\0session-1" in legacy_layout["sessionTabs"]

    server_state = json.loads(migrated["opencode.global.dat:server"])
    assert server_state["list"][0]["http"]["url"] == server
    assert server_state["list"][1]["http"]["url"] == external_server
    assert {item["worktree"] for item in server_state["projects"][server]} == {
        "/prefixed-work",
        "/work",
    }
    assert server_state["lastProject"][server] == "/prefixed-work"

    layout = json.loads(migrated["opencode.global.dat:layout"])
    assert layout["home"]["selection"]["server"] == server
    assert server + "\0session-1" in layout["sessionTabs"]

    tabs = json.loads(migrated["opencode.window.browser.dat:tabs"])
    assert tabs[0]["server"] == server
    assert tabs[1]["server"] == external_server
    closed = json.loads(
        migrated["opencode.window.browser.dat:tabs.closed"]
    )
    assert closed[0]["tab"]["server"] == server

    assert json.loads(
        migrated["opencode.global.dat:prompt-history"]
    ) == {"items": [f"Explain {origin}"]}
    assert migrated["opencode.window.browser.dat:tabs.info"] == "not-json"


def test_rewrite_html_injects_the_file_previewer_after_the_bootstrap():
    """Both injected scripts land in <head>, prefix bootstrap first."""
    rewritten = ocw.rewrite_html("<html><head></head><body></body></html>", PREFIX)
    bootstrap = f'<script src="{PREFIX}/neurodesk-prefix.js"></script>'
    previewer = f'<script defer src="{PREFIX}/neurodesk-preview.js"></script>'
    assert bootstrap in rewritten
    assert previewer in rewritten
    assert rewritten.index(bootstrap) < rewritten.index(previewer)
    # A body that already references the previewer keeps exactly one copy.
    already = ocw.rewrite_html(
        '<html><head><script defer src="/neurodesk-preview.js"></script>'
        "</head><body></body></html>",
        PREFIX,
    )
    assert already.count("neurodesk-preview.js") == 1


def test_preview_bootstrap_is_bound_to_the_proxy_prefix_and_endpoints():
    """The previewer must address this proxy's own routes, not the SPA's."""
    script = ocw.preview_bootstrap_script(PREFIX)
    assert f'"prefix": "{PREFIX}"' in script
    assert '"filePath": "/neurodesk-file"' in script
    assert '"niivuePath": "/neurodesk-niivue.js"' in script
    # The hook must stay passive: OpenCode's own markup is only read.
    assert "appendChild(overlay)" in script
    assert "insertBefore" not in script


@pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to parse the previewer"
)
def test_preview_bootstrap_is_valid_javascript(tmp_path):
    """The previewer ships as a Python string; keep it syntactically valid."""
    script = tmp_path / "preview.js"
    script.write_text(ocw.preview_bootstrap_script(PREFIX), encoding="utf-8")
    subprocess.run(
        ["node", "--check", str(script)], check=True, capture_output=True
    )


# --- File previews -------------------------------------------------------------


def test_preview_kind_recognizes_images_and_volumes():
    """Only viewer-backed formats are previewable, case-insensitively."""
    assert ocw.preview_kind("qc_bet_sub01.png") == "image"
    assert ocw.preview_kind("QC.PNG") == "image"
    assert ocw.preview_kind("figure.svg") == "image"
    assert ocw.preview_kind("sub-01_T1w_brain_mask.nii.gz") == "volume"
    assert ocw.preview_kind("brain.nii") == "volume"
    assert ocw.preview_kind("aseg.mgz") == "volume"
    assert ocw.preview_kind("analysis.sh") == ""
    assert ocw.preview_kind("archive.gz") == ""
    assert ocw.preview_kind("") == ""


def test_preview_content_type_keeps_compressed_volumes_compressed():
    """NiiVue inflates .nii.gz itself, so the transport must not."""
    assert ocw.preview_content_type("brain.nii.gz") == "application/gzip"
    assert ocw.preview_content_type("brain.nii") == "application/octet-stream"
    assert ocw.preview_content_type("qc.png") == "image/png"
    assert ocw.preview_content_type("plot.svg") == "image/svg+xml"


def _read_preview(work_dir, rel_path, **kwargs):
    """Open and read a preview request through the production confinement API."""
    parts = ocw.safe_preview_path_parts(rel_path)
    handle = ocw.open_preview_file(work_dir, parts, **kwargs)
    if handle is None:
        return None
    with handle:
        return handle.read()


def test_open_preview_file_finds_files_inside_the_session_project(tmp_path):
    """An exact relative path opens; so does a unique basename."""
    work_dir = tmp_path / "20260727_101500"
    (work_dir / "derivatives" / "bet").mkdir(parents=True)
    volume = work_dir / "derivatives" / "bet" / "sub-01_brain.nii.gz"
    volume.write_bytes(b"volume")
    (work_dir / "qc.png").write_bytes(b"image")

    assert _read_preview(
        work_dir, "derivatives/bet/sub-01_brain.nii.gz"
    ) == b"volume"
    # The changed-files list often yields only a suffix of the real path.
    assert _read_preview(work_dir, "sub-01_brain.nii.gz") == b"volume"
    assert _read_preview(work_dir, "bet/sub-01_brain.nii.gz") == b"volume"
    assert _read_preview(work_dir, "qc.png") == b"image"


def test_open_preview_file_refuses_to_leave_the_session_project(tmp_path):
    """Traversal, absolute paths, and symlinked escapes must all fail."""
    work_dir = tmp_path / "20260727_101500"
    work_dir.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"secret")
    (work_dir / "link.png").symlink_to(outside)

    assert _read_preview(work_dir, "../secret.png") is None
    assert _read_preview(work_dir, str(outside)) is None
    assert _read_preview(work_dir, "link.png") is None
    # Unsupported types are rejected before the filesystem is touched.
    (work_dir / "notes.txt").write_text("x", encoding="utf-8")
    assert _read_preview(work_dir, "notes.txt") is None
    assert _read_preview("", "qc.png") is None


def test_open_preview_file_rejects_noncanonical_request_paths(tmp_path):
    """Only the preview hook's strict relative-path alphabet reaches open()."""
    work_dir = tmp_path / "20260727_101500"
    work_dir.mkdir()
    (work_dir / "qc.png").write_bytes(b"image")

    for rel_path in (
        "/qc.png", "./qc.png", "nested//qc.png", "nested/../qc.png",
        "nested\\qc.png", "qc image.png", "qc%2fimage.png", "qc\x00.png",
    ):
        assert ocw.safe_preview_path_parts(rel_path) == ()
        assert _read_preview(work_dir, rel_path) is None


def test_open_preview_file_pins_validated_descriptor_across_path_swap(tmp_path):
    """Replacing a validated name cannot redirect an already-open preview."""
    work_dir = tmp_path / "20260727_101500"
    work_dir.mkdir()
    preview = work_dir / "qc.png"
    preview.write_bytes(b"session image")
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"private")

    handle = ocw.open_preview_file(
        work_dir, ocw.safe_preview_path_parts("qc.png")
    )
    assert handle is not None
    preview.rename(work_dir / "original.png")
    preview.symlink_to(outside)
    with handle:
        assert handle.read() == b"session image"


def test_open_preview_file_refuses_ambiguous_and_bounded_searches(tmp_path):
    """Two candidates named alike must not be guessed between."""
    work_dir = tmp_path / "20260727_101500"
    (work_dir / "run-1").mkdir(parents=True)
    (work_dir / "run-2").mkdir(parents=True)
    (work_dir / "run-1" / "qc.png").write_bytes(b"a")
    (work_dir / "run-2" / "qc.png").write_bytes(b"b")
    assert _read_preview(work_dir, "qc.png") is None
    # The distinguishing prefix resolves it again.
    assert _read_preview(work_dir, "run-2/qc.png") == b"b"
    # The walk is bounded so a huge project cannot stall the proxy.
    assert _read_preview(work_dir, "qc.png", max_entries=1) is None


def test_open_preview_file_matches_on_a_path_boundary(tmp_path):
    """A name suffix must not match a longer, unrelated file name."""
    work_dir = tmp_path / "20260727_101500"
    work_dir.mkdir()
    (work_dir / "extra_qc.png").write_bytes(b"a")
    assert _read_preview(work_dir, "qc.png") is None


def test_niivue_bundle_version_tracks_content(tmp_path, monkeypatch):
    """The immutable bundle URL must change when the bundle does."""
    bundle = tmp_path / "niivue.js"
    bundle.write_text("export const Niivue = class {};\n", encoding="utf-8")
    monkeypatch.setenv("OPENCODE_WEB_NIIVUE_BUNDLE", str(bundle))

    first = ocw.niivue_bundle_version()
    assert first and len(first) == 16
    assert ocw.niivue_bundle_version() == first  # cached, still stable

    bundle.write_text("export const Niivue = class { v2() {} };\n", encoding="utf-8")
    os.utime(bundle, (0, 0))  # a rebuilt image also restamps the file
    assert ocw.niivue_bundle_version() != first

    bundle.unlink()
    assert ocw.niivue_bundle_version() == ""


def test_preview_bootstrap_requests_a_versioned_bundle_url(tmp_path, monkeypatch):
    """The previewer carries the token; it is itself served no-store."""
    bundle = tmp_path / "niivue.js"
    bundle.write_text("export const Niivue = class {};\n", encoding="utf-8")
    monkeypatch.setenv("OPENCODE_WEB_NIIVUE_BUNDLE", str(bundle))

    script = ocw.preview_bootstrap_script(PREFIX)
    assert f'"niivueVersion": "{ocw.niivue_bundle_version()}"' in script
    assert 'CONFIG.niivuePath +' in script
    assert '"?v=" + CONFIG.niivueVersion' in script


def test_preview_size_limit_falls_back_on_unusable_overrides(monkeypatch):
    """A malformed override must not disable the cap."""
    monkeypatch.setenv("OPENCODE_WEB_PREVIEW_MAX_BYTES", "4096")
    assert ocw.preview_size_limit() == 4096
    monkeypatch.setenv("OPENCODE_WEB_PREVIEW_MAX_BYTES", "not-a-number")
    assert ocw.preview_size_limit() == ocw.DEFAULT_PREVIEW_MAX_BYTES
    monkeypatch.setenv("OPENCODE_WEB_PREVIEW_MAX_BYTES", "0")
    assert ocw.preview_size_limit() == ocw.DEFAULT_PREVIEW_MAX_BYTES


def test_rewrite_css_prefixes_url_references():
    """CSS url(...) references gain the proxy prefix."""
    css = '@font-face { src: url("/assets/inter.woff2"); } .x { background: url(/assets/bg.svg); }'
    rewritten = ocw.rewrite_css(css, PREFIX)
    assert f'url("{PREFIX}/assets/inter.woff2")' in rewritten
    assert f"url({PREFIX}/assets/bg.svg)" in rewritten


def test_rewrite_js_prefixes_assets_without_double_prefixing_sdk_api_paths():
    """Static assets gain the prefix; SDK routes use the prefixed base URL."""
    js = (
        'const font = "/assets/inter.woff2";\n'
        'const chunk = "assets/home.js";\n'
        "const api = '/api/session';\n"
        "const tpl = `/assets/${name}`;\n"
        'const ratio = a / b; const other = "/session/history";\n'
    )
    rewritten = ocw.rewrite_js(js, PREFIX)
    assert f'"{PREFIX}/assets/inter.woff2"' in rewritten
    assert f'"{PREFIX}/assets/home.js"' in rewritten
    assert "'/api/session'" in rewritten
    assert f"'{PREFIX}/api/session'" not in rewritten
    assert f"`{PREFIX}/assets/" in rewritten
    # Division and unknown root paths are left alone.
    assert "a / b" in rewritten
    assert '"/session/history"' in rewritten


def test_rewrite_js_registers_the_prefixed_server_used_for_permissions():
    """The selected default server must also exist in OpenCode's registry."""
    canonical_origin = (
        'location.hostname.includes("opencode.ai")?'
        '"http://localhost:4096":location.origin'
    )
    js = f"const canonical=()=>{canonical_origin};"

    rewritten = ocw.rewrite_js(js, PREFIX)

    assert canonical_origin not in rewritten
    assert "window.__NEURODESK_OPENCODE_SERVER_URL__" in rewritten


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to execute the rewritten URL constructors",
)
def test_rewrite_js_preserves_server_base_path_for_protocol_and_v2_requests(
    tmp_path,
):
    """Root-absolute SDK paths must not discard the Jupyter proxy prefix."""
    server = f"http://127.0.0.1:8888{PREFIX}"
    bundle_fragment = (
        f'const e={{url:{json.dumps(server)},baseUrl:{json.dumps(server)}}};'
        'const n="/global/health",a={path:"/api/event"};'
        "process.stdout.write(JSON.stringify(["
        "new URL(n,e.url).pathname,"
        "new URL(a.path,e.baseUrl).pathname"
        "]));"
    )
    harness = tmp_path / "server-relative-urls.js"
    harness.write_text(
        ocw.rewrite_js(bundle_fragment, PREFIX), encoding="utf-8"
    )

    result = subprocess.run(
        ["node", str(harness)], check=True, capture_output=True, text=True
    )

    assert json.loads(result.stdout) == [
        f"{PREFIX}/global/health",
        f"{PREFIX}/api/event",
    ]


@pytest.mark.parametrize("router_identifier", ["Epe", "T" + "ye"])
def test_rewrite_js_configures_the_spa_router_with_the_proxy_base_path(
    router_identifier,
):
    """The browser must not decode the proxy name as a project directory."""
    router_component = (
        f"get component(){{return e.router??{router_identifier}}},root:n=>"
    )

    rewritten = ocw.rewrite_js(router_component, PREFIX)

    assert router_component not in rewritten
    assert "window.__NEURODESK_OPENCODE_BASE_PATH__" in rewritten


def test_rewrite_js_strips_the_proxy_prefix_in_the_layout_route_parser():
    """The layout's route parser sees the raw pathname, not the router base.

    Without stripping, ``/opencode/server/<b64>/session/<id>`` parses as
    ``{type:"home"}`` and the titlebar Home button becomes a no-op.
    """
    parser = (
        'Xke=(e,t)=>{const n=e.split("/").filter(Boolean);'
        'if(n.length===0)return{type:"home"};'
        'if(n[0]==="server"&&n[2]==="session"&&n[3])'
        'return{type:"session",sessionId:n[3],server:qo(n[1])};'
    )

    rewritten = ocw.rewrite_js(parser, PREFIX)

    assert 'const n=e.split("/").filter(Boolean)' in rewritten
    assert (
        'const _nb=window.__NEURODESK_OPENCODE_BASE_PATH__||"";'
        '_nb&&(e===_nb||e.startsWith(_nb+"/"))&&(e=e.slice(_nb.length));'
    ) in rewritten
    assert rewritten.index("_nb") < rewritten.index('e.split("/")')


def test_rewrite_js_forces_home_navigation_through_the_proxy_prefix():
    """The in-session Home button must load the known-good prefixed root."""
    js = (
        'const Ie=()=>ce.toggleHome({home:pe.route().type==="home",'
        'current:J()});'
    )

    rewritten = ocw.rewrite_js(js, PREFIX)

    assert js not in rewritten
    assert 'pe.route().type==="home"?ce.toggleHome' in rewritten
    assert f'location.assign("{PREFIX}/")' in rewritten


def test_rewrite_js_home_toggle_regex_is_linear_on_adversarial_input():
    """The identifier quantifiers are possessive, so a long '$' run cannot
    trigger quadratic backtracking (CodeQL py/polynomial-redos)."""
    adversarial = "$" * 50000
    started = time.monotonic()
    rewritten = ocw.rewrite_js(adversarial, PREFIX)
    assert time.monotonic() - started < 2.0
    assert rewritten == adversarial


def test_rewrite_js_marks_fetch_based_sse_requests_for_jupyter_streaming():
    """Jupyter Server Proxy streams only explicit event-stream requests."""
    sse_headers = (
        "const b=u.headers instanceof Headers?u.headers:new Headers(u.headers);"
        'd!==void 0&&b.set("Last-Event-ID",d);'
    )

    rewritten = ocw.rewrite_js(sse_headers, PREFIX)

    assert sse_headers not in rewritten
    assert 'b.set("Accept","text/event-stream")' in rewritten


def test_rewrite_body_is_noop_without_prefix():
    """No prefix (desktop mode) must leave bodies byte-identical."""
    html = '<script src="/assets/index.js"></script>'
    assert ocw.rewrite_body(html, "text/html", "") == html


def test_safe_forwarded_prefix_accepts_only_canonical_paths():
    """Prefixes are interpolated into rewritten bodies, so only clean
    root-relative paths may pass; anything else must collapse to ""."""
    assert ocw.safe_forwarded_prefix("/user/alice/opencode/") == (
        "/user/alice/opencode"
    )
    assert ocw.safe_forwarded_prefix("/opencode") == "/opencode"
    assert ocw.safe_forwarded_prefix("") == ""
    assert ocw.safe_forwarded_prefix(None) == ""
    assert ocw.safe_forwarded_prefix("/") == ""
    assert ocw.safe_forwarded_prefix('/x" onmouseover="alert(1)') == ""
    assert ocw.safe_forwarded_prefix("/x<svg onload=alert(1)>") == ""
    assert ocw.safe_forwarded_prefix("/x?y=1") == ""
    assert ocw.safe_forwarded_prefix("/x#frag") == ""
    assert ocw.safe_forwarded_prefix("no-leading-slash") == ""
    assert ocw.safe_forwarded_prefix("/x\r\nInjected: 1") == ""


def test_redact_auth_params_hides_login_tokens():
    """Request-line logging must never contain ?auth= credentials."""
    line = "GET /?auth=super-secret&x=1 HTTP/1.1"
    redacted = ocw.redact_auth_params(line)
    assert "super-secret" not in redacted
    assert "auth=REDACTED" in redacted
    assert "x=1" in redacted
    assert ocw.redact_auth_params("GET / HTTP/1.1") == "GET / HTTP/1.1"


# --- Working directory ---------------------------------------------------------


def test_create_opencode_work_dir_uses_timestamp_and_avoids_collisions(tmp_path):
    """Every session gets a new project below ~/opencode-work."""
    home = tmp_path / "home"
    home.mkdir()

    first = Path(ocw.create_opencode_work_dir(home, "20260721_203001"))
    second = Path(ocw.create_opencode_work_dir(home, "20260721_203001"))

    assert first == home / "opencode-work" / "20260721_203001"
    assert second == home / "opencode-work" / "20260721_203001_2"
    assert first.is_dir()
    assert second.is_dir()


def _git_toplevel(directory):
    """Return what OpenCode's worktree probe resolves ``directory`` to."""
    return subprocess.run(
        ["git", "-C", directory, "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_create_opencode_work_dir_makes_the_launch_dir_a_git_project(tmp_path):
    """The session directory itself must be the Git worktree root.

    OpenCode resolves a request's directory to the enclosing ``git rev-parse
    --show-toplevel``. The nested session root must outrank the backend's
    ``opencode-work`` parent project so artifacts do not mix there.
    """
    home = tmp_path / "home"
    home.mkdir()

    work_dir = Path(ocw.create_opencode_work_dir(home, "20260721_203001"))

    assert work_dir.parent == home / "opencode-work"
    assert (work_dir / ".git").is_dir()
    assert _git_toplevel(work_dir) == str(work_dir)


def test_create_opencode_work_dir_seeds_editable_agents_file(tmp_path):
    """Each session project gets its own editable Neurodesk instructions."""
    home = tmp_path / "home"
    home.mkdir()
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("# Session instructions\n", encoding="utf-8")

    work_dir = Path(
        ocw.create_opencode_work_dir(
            home, "20260721_203001", agents_file=agents_file
        )
    )

    seeded = work_dir / "AGENTS.md"
    assert seeded.read_bytes() == agents_file.read_bytes()
    seeded.write_text("# User-edited instructions\n", encoding="utf-8")
    assert agents_file.read_text(encoding="utf-8") == "# Session instructions\n"


def test_create_opencode_work_dir_outranks_a_legacy_parent_repo(tmp_path):
    """A pre-existing ``~/opencode-work/.git`` must not capture new launches."""
    home = tmp_path / "home"
    home.mkdir()
    legacy_root = home / "opencode-work"
    legacy_root.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", str(legacy_root)],
        check=True,
        capture_output=True,
    )

    work_dir = Path(ocw.create_opencode_work_dir(home, "20260721_203001"))

    assert _git_toplevel(work_dir) == str(work_dir)


def test_project_dir_validation_confines_to_home(tmp_path):
    """Project directories are real directories strictly inside home.

    Browsing may additionally name home itself, but neither home nor the
    shared ``~/opencode-work`` root may ever hold a session: sessions running
    there would mix their artifacts across projects.
    """
    home = tmp_path / "home"
    study = home / "projects" / "study-a"
    study.mkdir(parents=True)
    dated = Path(ocw.create_opencode_work_dir(home, "20260721_203001"))
    outside = tmp_path / "outside"
    outside.mkdir()
    escape = home / "escape"
    escape.symlink_to(outside)

    assert ocw.is_opencode_project_dir(str(study), home)
    assert ocw.is_opencode_project_dir(str(dated), home)
    assert ocw.is_opencode_home_confined_dir(str(home), home)
    assert not ocw.is_opencode_project_dir(str(home), home)
    assert not ocw.is_opencode_project_dir(str(home / "opencode-work"), home)
    assert not ocw.is_opencode_project_dir(str(outside), home)
    assert not ocw.is_opencode_home_confined_dir(str(outside), home)
    assert not ocw.is_opencode_project_dir(str(escape), home)
    assert not ocw.is_opencode_home_confined_dir(str(escape), home)
    assert not ocw.is_opencode_project_dir(str(home / "missing"), home)
    assert not ocw.is_opencode_project_dir("", home)


def test_force_directory_query_overrides_selected_directory():
    """A UI-selected directory is rewritten to the seeded work dir."""
    forced = ocw.force_directory_query(
        "directory=%2Fhome%2Fjovyan%2Fdemo", "/home/jovyan/opencode-work/D"
    )
    assert dict(urllib.parse.parse_qsl(forced)) == {
        "directory": "/home/jovyan/opencode-work/D"
    }


def test_force_directory_query_preserves_other_params_and_order():
    """Only ``directory`` changes; sibling parameters survive untouched."""
    forced = ocw.force_directory_query(
        "a=1&directory=%2Fother&z=9", "/seed"
    )
    assert urllib.parse.parse_qsl(forced) == [
        ("a", "1"), ("directory", "/seed"), ("z", "9")
    ]


def test_force_directory_query_leaves_requests_without_directory():
    """Requests that omit ``directory`` fall back to the backend cwd."""
    assert ocw.force_directory_query("", "/seed") == ""
    assert ocw.force_directory_query("query=hi", "/seed") == "query=hi"


def test_force_directory_query_noop_without_a_work_dir():
    """Before the work dir exists the query is passed through verbatim."""
    assert ocw.force_directory_query("directory=%2Fx", "") == "directory=%2Fx"
    assert ocw.force_directory_query("directory=%2Fx", None) == "directory=%2Fx"


# --- Key handling ---------------------------------------------------------------


def test_sanitize_neurodesk_api_key_strips_control_chars_and_whitespace():
    """Key sanitizing mirrors the terminal wrapper rules."""
    assert ocw.sanitize_neurodesk_api_key(" sk-abc\r\n") == "sk-abc"
    assert ocw.sanitize_neurodesk_api_key("sk-\x07a\x1bbc\x7f") == "sk-abc"
    assert ocw.sanitize_neurodesk_api_key(None) == ""


def test_persist_key_roundtrip_and_replaces_previous_block(tmp_path):
    """Persisting a key replaces the previous managed block in ~/.bashrc."""
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
    """The shared credential is created 0600 and re-read verbatim."""
    secret_file = tmp_path / "secrets" / "opencode_server_password"
    password = ocw.load_or_create_password(str(secret_file))
    assert password
    mode = stat.S_IMODE(secret_file.stat().st_mode)
    assert mode == 0o600
    assert ocw.load_or_create_password(str(secret_file)) == password


def test_load_or_create_password_is_atomic_across_racers(tmp_path):
    """Concurrent starters (Jupyter config load, desktop shortcut, proxy
    launcher) must all end up with the same credential."""
    secret_file = tmp_path / "secrets" / "opencode_server_password"
    results = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                ocw.load_or_create_password(str(secret_file))
            )
        )
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 8
    assert len(set(results)) == 1


# --- Static config expectations --------------------------------------------------


def jupyter_template_path():
    """Locate the Jupyter notebook config template."""
    return resolve_source(
        "/opt/neurodesktop/jupyter_notebook_config.py.template",
        "config/jupyter/jupyter_notebook_config.py.template",
    )


def test_jupyter_template_defines_opencode_proxy_entry():
    """The Jupyter config template ships the OpenCode proxy tile."""
    template_text = jupyter_template_path().read_text(encoding="utf-8")
    # The template must stay valid python (webapp entries are appended at the
    # {{WEBAPP_SERVERS}} comment placeholder, so it compiles as-is).
    compile(template_text, "jupyter_notebook_config.py.template", "exec")

    assert "'opencode': {" in template_text
    assert "/opt/neurodesktop/opencode_web.py" in template_text
    assert "opencode_server_password" in template_text
    assert "_opencode_basic" in template_text
    assert "'title': 'OpenCode Web'" in template_text
    assert "'enabled': bool(_opencode_pass)" in template_text
    assert "'icon_path': '/opt/opencode_logo.svg'" in template_text


def test_opencode_default_config_disables_sharing():
    """Session sharing is opt-in: the shipped config disables it."""
    config_path = resolve_source(
        "/opt/jovyan_defaults/.config/opencode/opencode.json",
        "config/agents/opencode_config.json",
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["share"] == "disabled"
    assert config["autoupdate"] is False


def test_opencode_default_config_pins_no_global_agents_md():
    """Neurodesk guidance comes from the editable per-project AGENTS.md only.

    A global ``instructions`` entry for the read-only /opt/AGENTS.md would put
    a second, uneditable copy into every prompt, so a user's edits to the
    AGENTS.md in their working directory could never take effect.
    """
    config_path = resolve_source(
        "/opt/jovyan_defaults/.config/opencode/opencode.json",
        "config/agents/opencode_config.json",
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "/opt/AGENTS.md" not in config.get("instructions", [])


def test_desktop_launcher_script_and_entry():
    """The desktop script parses and the menu entry points at it."""
    script = resolve_source(
        "/opt/neurodesktop/opencode_web_desktop.sh",
        "config/agents/opencode_web_desktop.sh",
    )
    subprocess.run(["bash", "-n", str(script)], check=True)

    desktop_entry = resolve_source(
        "/usr/share/applications/opencode-web.desktop",
        "config/agents/opencode-web.desktop",
    ).read_text(encoding="utf-8")
    assert "Exec=/opt/neurodesktop/opencode_web_desktop.sh" in desktop_entry
    assert "Icon=/opt/opencode_logo.svg" in desktop_entry


def test_opencode_bash_env_is_valid_and_installed_by_the_image():
    """The BASH_ENV hook parses and is copied into the production image."""
    script = opencode_bash_env_path()
    subprocess.run(["bash", "-n", str(script)], check=True)
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    assert (
        "/tmp/agents/opencode_bash_env.sh "
        "/opt/neurodesktop/opencode_bash_env.sh"
    ) in dockerfile


# --- End-to-end: setup page, key validation, proxying ----------------------------


class _FakeLLMHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        """Keep the fake LLM server quiet."""

    def do_GET(self):
        """Serve /openai/models with auth-dependent responses."""
        if self.path != "/openai/models":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.headers.get("Authorization") == "Bearer good-key":
            payload = json.dumps({"data": [{"id": "model-alpha"}]}).encode()
            self.send_response(200)
        elif self.headers.get("Authorization") == "Bearer flaky-key":
            # Unexpected server error: the key must be accepted unverified.
            payload = b'{"error":{"message":"upstream exploded"}}'
            self.send_response(500)
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
import re
import sys
import urllib.parse

args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
password = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
expected = "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()

state_dir = os.environ["FAKE_BACKEND_STATE_DIR"]
session_counter = 0
with open(os.path.join(state_dir, "env.json"), "w") as fh:
    json.dump(
        {
            "argv": sys.argv[1:],
            "cwd": os.getcwd(),
            "OPENCODE_DISABLE_FFF": os.environ.get("OPENCODE_DISABLE_FFF", ""),
            "OPENCODE_MODEL_PROFILE": os.environ.get("OPENCODE_MODEL_PROFILE", ""),
            "NEURODESK_API_KEY": os.environ.get("NEURODESK_API_KEY", ""),
            "OPENCODE_SERVER_PASSWORD": password,
            "BASH_ENV": os.environ.get("BASH_ENV", ""),
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
        'const font = "/assets/inter.woff2"; '
        'const provider = "/provider"; '
        'const config = "/global/config"; '
        'const canonical=()=>location.hostname.includes("opencode.ai")?'
        '"http://localhost:4096":location.origin; '
        'const router={get component(){return e.router??Epe},root:n=>n}; '
        'const sse=()=>{const b=u.headers instanceof Headers?'
        'u.headers:new Headers(u.headers);d!==void 0&&'
        'b.set("Last-Event-ID",d);return b}; '
        'export { font, provider, config, canonical, router };',
    ),
    "/provider": (
        "application/json",
        '{"all":[{"id":"neurodesk","models":{"model-alpha":{}}}]}',
    ),
    "/global/config": (
        "application/json",
        '{"model":"neurodesk/model-alpha"}',
    ),
    "/global/health": ("application/json", '{"healthy":true}'),
    "/binary.bin": ("application/octet-stream", "\\x00binary\\x00"),
}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def request_directory(self, parsed):
        # Mirror how OpenCode resolves a request directory: the query
        # parameter first, then the x-opencode-directory header (which its
        # client percent-encodes and only mirrors into the query for
        # GET/HEAD), then the backend process cwd.
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        header = self.headers.get("x-opencode-directory", "")
        return (
            query.get("directory", [""])[0]
            or query.get("location[directory]", [""])[0]
            or (urllib.parse.unquote(header) if header else "")
            or os.getcwd()
        )

    def send_json(self, value):
        payload = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def echo_directory(self, parsed):
        self.send_json({"directory": self.request_directory(parsed)})

    def do_POST(self):
        global session_counter
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/session":
            session_counter += 1
            session_id = f"ses_test_{session_counter}"
            directory = self.request_directory(parsed)
            self.send_json({"id": session_id, "directory": directory})
            return
        if re.fullmatch(r"/session/ses_test_\\d+/prompt_async", parsed.path):
            self.echo_directory(parsed)
            return
        if parsed.path == "/neurodesk-echo-directory":
            self.echo_directory(parsed)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/global/event":
            payload = b'data: {"type":"server.connected"}\\n\\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(f"{len(payload):X}\\r\\n".encode())
            self.wfile.write(payload + b"\\r\\n0\\r\\n\\r\\n")
            self.wfile.flush()
            return
        if parsed.path == "/neurodesk-echo-directory":
            self.echo_directory(parsed)
            return
        if parsed.path == "/api/fs/list":
            directory = self.request_directory(parsed)
            entries = []
            try:
                for name in sorted(os.listdir(directory)):
                    is_dir = os.path.isdir(os.path.join(directory, name))
                    entries.append({
                        "path": name + ("/" if is_dir else ""),
                        "type": "directory" if is_dir else "file",
                    })
            except OSError:
                pass
            self.send_json(
                {"location": {"directory": directory}, "data": entries}
            )
            return
        if parsed.path == "/api/fs/find":
            # OpenCode returns [] for an empty query; a typed query returns
            # search results. The marker lets tests prove pass-through.
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            results = [] if not query.get("query", [""])[0] else [
                {"path": "backend-search-result/", "type": "directory"}
            ]
            self.send_json({
                "location": {"directory": self.request_directory(parsed)},
                "data": results,
            })
            return
        content_type, body = PAGES.get(parsed.path, ("text/plain", "fallback"))
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


def _free_port():
    """Pick an ephemeral localhost port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port, timeout=20):
    """Block until the port accepts connections or fail the test."""
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
        """Never follow redirects; tests assert on them directly."""
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _request(port, path, headers=None, data=None, method=None, timeout=15):
    """HTTP request helper that surfaces redirects instead of following."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        response = _OPENER.open(request, timeout=timeout)
        status = response.status
    except urllib.error.HTTPError as exc:
        response = exc
        status = exc.code
    body = response.read().decode("utf-8", errors="replace")
    return status, dict(response.headers), body


@pytest.fixture()
def launcher(tmp_path):
    """Spawn opencode_web.py against fake backend and LLM servers."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("# Per-session Neurodesk instructions\n", encoding="utf-8")

    fake_backend = tmp_path / "fake-opencode"
    fake_backend.write_text(FAKE_BACKEND_SOURCE, encoding="utf-8")
    fake_backend.chmod(0o755)

    llm_server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), _FakeLLMHandler
    )
    threading.Thread(target=llm_server.serve_forever, daemon=True).start()
    llm_port = llm_server.server_address[1]

    niivue_bundle = tmp_path / "niivue.min.js"
    niivue_bundle.write_text("export const Niivue = class {};\n", encoding="utf-8")

    port = _free_port()
    secret_file = home_dir / ".neurodesk" / "secrets" / "opencode_server_password"
    env = {
        **os.environ,
        "HOME": str(home_dir),
        "OPENCODE_WEB_WRAPPER_BIN": str(fake_backend),
        "OPENCODE_WEB_STARTUP_TIMEOUT": "30",
        "NEURODESK_LLM_BASE_URL": f"http://127.0.0.1:{llm_port}/openai",
        "FAKE_BACKEND_STATE_DIR": str(state_dir),
        "OPENCODE_WEB_AGENTS_FILE": str(agents_file),
        "OPENCODE_WEB_NIIVUE_BUNDLE": str(niivue_bundle),
    }
    env.pop("NEURODESK_API_KEY", None)
    env.pop("OPENCODE_MODEL_PROFILE", None)

    process = subprocess.Popen(
        [sys_executable(), str(opencode_web_module_path()), "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    token_file = (
        home_dir / ".neurodesk" / "secrets"
        / f"opencode_web_login_token.{port}"
    )
    try:
        _wait_for_port(port)
        deadline = time.monotonic() + 10
        while (
            not (secret_file.exists() and token_file.exists())
            and time.monotonic() < deadline
        ):
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
            "token_file": token_file,
            "agents_file": agents_file,
            "niivue_bundle": niivue_bundle,
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        llm_server.shutdown()


def sys_executable():
    """Return the running python executable path."""
    import sys

    return sys.executable


def _complete_key_setup(ctx, key=b"key=good-key"):
    """POST the setup form and return (status, headers)."""
    status, headers, _body = _request(
        ctx["port"],
        "/neurodesk-setup",
        headers={**ctx["auth"],
                 "Content-Type": "application/x-www-form-urlencoded"},
        data=key,
    )
    return status, headers


def _wait_for_proxied_root(ctx, extra_headers=None, timeout=20):
    """Poll / until the fake opencode backend is served through the proxy."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _headers, body = _request(
            ctx["port"], "/", headers={**ctx["auth"], **(extra_headers or {})}
        )
        if status == 200 and "opencode-fake" in body:
            return body
        time.sleep(0.3)
    raise AssertionError("proxied opencode backend never became ready")


def test_proxy_pins_untrusted_directory_to_backend_work_root(launcher):
    """An arbitrary UI directory cannot escape the managed work root."""
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)

    backend_env = json.loads(
        (launcher["state"] / "env.json").read_text(encoding="utf-8")
    )
    work_dir = backend_env["cwd"]
    assert Path(work_dir) == launcher["home"] / "opencode-work"

    status, _headers, body = _request(
        launcher["port"],
        "/neurodesk-echo-directory?directory=/home/jovyan/demo",
        headers=launcher["auth"],
    )
    assert status == 200
    assert json.loads(body)["directory"] == work_dir


def test_proxy_pins_the_directory_header_on_non_get_requests(launcher):
    """A POST carrying only ``x-opencode-directory`` must still be pinned.

    OpenCode's web client mirrors the directory into the query string only for
    GET/HEAD; ``POST /session`` and ``POST /session/:id/message`` send it in
    the header alone. Pinning just the query therefore left the two calls that
    decide where a session lives and where its tools run free to point at any
    directory the SPA held -- which is how sessions ended up writing to the
    shared ``~/opencode-work`` parent instead of a seeded session directory.
    """
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)

    backend_env = json.loads(
        (launcher["state"] / "env.json").read_text(encoding="utf-8")
    )
    work_dir = backend_env["cwd"]

    status, _headers, body = _request(
        launcher["port"],
        "/neurodesk-echo-directory",
        method="POST",
        data=b"{}",
        headers={
            **launcher["auth"],
            "Content-Type": "application/json",
            "x-opencode-directory": urllib.parse.quote(
                "/home/jovyan/opencode-work", safe=""
            ),
        },
    )
    assert status == 200
    assert json.loads(body)["directory"] == work_dir


def test_each_created_session_gets_a_distinct_seeded_work_directory(launcher):
    """Every POST /session creates and remains pinned to its own project."""
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)

    sessions = []
    for selected_directory in ("/home/jovyan/demo-a", "/home/jovyan/demo-b"):
        status, _headers, body = _request(
            launcher["port"],
            "/session",
            method="POST",
            data=b"{}",
            headers={
                **launcher["auth"],
                "Content-Type": "application/json",
                "x-opencode-directory": urllib.parse.quote(
                    selected_directory, safe=""
                ),
            },
        )
        assert status == 200
        sessions.append(json.loads(body))

    first, second = sessions
    first_dir = Path(first["directory"])
    second_dir = Path(second["directory"])
    work_root = launcher["home"] / "opencode-work"

    assert first_dir != second_dir
    assert first_dir.parent == work_root
    assert second_dir.parent == work_root
    assert re.fullmatch(r"\d{8}_\d{6}(?:_\d+)?", first_dir.name)
    assert re.fullmatch(r"\d{8}_\d{6}(?:_\d+)?", second_dir.name)
    for directory in (first_dir, second_dir):
        assert (directory / ".git").is_dir()
        assert (directory / "AGENTS.md").read_bytes() == launcher[
            "agents_file"
        ].read_bytes()

    # A stale or incorrect browser header must not move an existing session
    # into another session's project after the proxy has recorded its mapping.
    status, _headers, body = _request(
        launcher["port"],
        f"/session/{first['id']}/prompt_async",
        method="POST",
        data=b"{}",
        headers={
            **launcher["auth"],
            "Content-Type": "application/json",
            "x-opencode-directory": urllib.parse.quote(str(second_dir), safe=""),
        },
    )
    assert status == 200
    assert json.loads(body)["directory"] == str(first_dir)


def test_session_created_in_user_chosen_home_project(launcher):
    """POST /session naming an existing home directory runs there untouched.

    This is the web UI's Add-project flow: the picker records a directory and
    the next session creation names it. The user's directory is honored as
    the session project and never git-initialized or seeded, and the proxy
    keeps later requests for that session pinned to it against stale headers.
    """
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)

    study = launcher["home"] / "projects" / "study-a"
    study.mkdir(parents=True)
    (study / "notes.txt").write_text("data\n", encoding="utf-8")

    status, _headers, body = _request(
        launcher["port"],
        "/session",
        method="POST",
        data=b"{}",
        headers={
            **launcher["auth"],
            "Content-Type": "application/json",
            "x-opencode-directory": urllib.parse.quote(str(study), safe=""),
        },
    )
    assert status == 200
    session = json.loads(body)
    assert Path(session["directory"]) == study.resolve()
    assert not (study / ".git").exists()
    assert not (study / "AGENTS.md").exists()
    assert sorted(entry.name for entry in study.iterdir()) == ["notes.txt"]

    other = launcher["home"] / "projects" / "study-b"
    other.mkdir()
    status, _headers, body = _request(
        launcher["port"],
        f"/session/{session['id']}/prompt_async",
        method="POST",
        data=b"{}",
        headers={
            **launcher["auth"],
            "Content-Type": "application/json",
            "x-opencode-directory": urllib.parse.quote(str(other), safe=""),
        },
    )
    assert status == 200
    assert Path(json.loads(body)["directory"]) == study.resolve()


def test_session_creation_in_home_or_work_root_gets_a_dated_project(launcher):
    """Naming home or the shared work root still allocates a fresh project.

    The SPA's default draft directory is the backend cwd (the shared work
    root), so an unchosen directory must keep the isolated dated-workspace
    behavior rather than running sessions in a shared directory.
    """
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)

    work_root = launcher["home"] / "opencode-work"
    for named in (launcher["home"], work_root):
        status, _headers, body = _request(
            launcher["port"],
            "/session",
            method="POST",
            data=b"{}",
            headers={
                **launcher["auth"],
                "Content-Type": "application/json",
                "x-opencode-directory": urllib.parse.quote(
                    str(named), safe=""
                ),
            },
        )
        assert status == 200
        directory = Path(json.loads(body)["directory"])
        assert directory.parent == work_root
        assert re.fullmatch(r"\d{8}_\d{6}(?:_\d+)?", directory.name)


def test_browse_requests_are_confined_to_home(launcher):
    """Directory browsing may name anything inside home, and only that.

    The Add-project picker lists directories with ``fs/list``/``fs/find``
    requests that carry the browsed path; forcing those to the work root made
    the picker unable to surface any real folder. Home and its descendants
    pass through; anything else still falls back to the managed root.
    """
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)

    study = launcher["home"] / "projects" / "study-a"
    study.mkdir(parents=True)
    work_root = launcher["home"] / "opencode-work"

    def browsed(directory):
        status, _headers, body = _request(
            launcher["port"],
            "/neurodesk-echo-directory?"
            + urllib.parse.urlencode({"directory": str(directory)}),
            headers=launcher["auth"],
        )
        assert status == 200
        return Path(json.loads(body)["directory"])

    assert browsed(launcher["home"]) == launcher["home"].resolve()
    assert browsed(study) == study.resolve()
    assert browsed(launcher["home"] / "missing") == work_root
    assert browsed("/somewhere/else") == work_root


def test_empty_directory_find_request_recognition():
    """Only the picker's empty-query directory search is special-cased."""
    is_empty = ocw.is_empty_directory_find_request
    query = "location%5Bdirectory%5D=%2Fhome&query=&type=directory&limit=50"
    assert is_empty("GET", "/api/fs/find", query)
    assert is_empty("GET", "/api/fs/find", "type=directory")
    assert not is_empty("GET", "/api/fs/find", "query=neuro&type=directory")
    assert not is_empty("GET", "/api/fs/find", "query=&type=file")
    assert not is_empty("GET", "/api/fs/list", "query=&type=directory")
    assert not is_empty("POST", "/api/fs/find", "query=&type=directory")


def test_directory_listing_entries_keeps_visible_directories():
    """The synthesized picker listing hides files and dot-directories."""
    payload = json.dumps({
        "location": {"directory": "/home/jovyan"},
        "data": [
            {"path": "projects/", "type": "directory"},
            {"path": ".git/", "type": "directory"},
            {"path": "notes.txt", "type": "file"},
            "garbage",
        ],
    }).encode("utf-8")
    listing = ocw.directory_listing_entries(payload)
    assert listing == {
        "location": {"directory": "/home/jovyan"},
        "data": [{"path": "projects/", "type": "directory"}],
    }
    assert ocw.directory_listing_entries(b"not json") is None
    assert ocw.directory_listing_entries(b'{"data": "nope"}') is None


def test_add_project_picker_gets_a_listing_for_its_empty_query(launcher):
    """The picker's initial empty search shows the browsed folders.

    OpenCode's own ``fs/find`` answers an empty query with ``[]``, so the
    Add-project dialog always opened onto "No folders found". The proxy
    answers that one request with the browsed directory's visible child
    directories instead, and leaves typed queries to the backend.
    """
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)

    (launcher["home"] / "projects").mkdir()
    (launcher["home"] / ".hidden").mkdir()
    (launcher["home"] / "notes.txt").write_text("data\n", encoding="utf-8")

    home_query = urllib.parse.urlencode(
        {"location[directory]": str(launcher["home"])}
    )
    status, _headers, body = _request(
        launcher["port"],
        f"/api/fs/find?{home_query}&query=&type=directory&limit=50",
        headers=launcher["auth"],
    )
    assert status == 200
    listing = json.loads(body)
    names = [entry["path"] for entry in listing["data"]]
    assert "projects/" in names
    assert "opencode-work/" in names
    assert ".hidden/" not in names
    assert "notes.txt" not in names

    # A typed query still reaches the backend's own search.
    status, _headers, body = _request(
        launcher["port"],
        f"/api/fs/find?{home_query}&query=proj&type=directory&limit=50",
        headers=launcher["auth"],
    )
    assert status == 200
    assert json.loads(body)["data"] == [
        {"path": "backend-search-result/", "type": "directory"}
    ]


def _request_bytes(port, path, headers=None, timeout=15):
    """HTTP GET helper that keeps the response body binary."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers=headers or {}
    )
    try:
        response = _OPENER.open(request, timeout=timeout)
        status = response.status
    except urllib.error.HTTPError as exc:
        response = exc
        status = exc.code
    return status, dict(response.headers), response.read()


def _session_with_files(ctx):
    """Create a session and populate its project the way an agent would."""
    status, _headers, body = _request(
        ctx["port"],
        "/session",
        method="POST",
        data=b"{}",
        headers={**ctx["auth"], "Content-Type": "application/json"},
    )
    assert status == 200
    session = json.loads(body)
    directory = Path(session["directory"])
    (directory / "qc_bet_sub01.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    nested = directory / "derivatives" / "bet" / "sub-01" / "anat"
    nested.mkdir(parents=True)
    (nested / "sub-01_T1w_brain_mask.nii.gz").write_bytes(b"\x1f\x8bfake-volume")
    (directory / "analysis_01_brain_extraction.sh").write_text(
        "#!/bin/bash\n", encoding="utf-8"
    )
    return session, directory


def test_preview_serves_session_images_and_volumes(launcher):
    """Files an agent wrote are previewable from their own session project."""
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)
    session, _directory = _session_with_files(launcher)
    query = f"?session={session['id']}"

    status, headers, body = _request_bytes(
        launcher["port"], f"/neurodesk-file/qc_bet_sub01.png{query}",
        headers=launcher["auth"],
    )
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"
    assert body == b"\x89PNG\r\n\x1a\nfake"

    # The full relative path the changed-files list shows.
    status, headers, body = _request_bytes(
        launcher["port"],
        "/neurodesk-file/derivatives/bet/sub-01/anat/"
        f"sub-01_T1w_brain_mask.nii.gz{query}",
        headers=launcher["auth"],
    )
    assert status == 200
    # Compressed volumes must reach NiiVue still compressed.
    assert headers["Content-Type"] == "application/gzip"
    assert "Content-Encoding" not in headers
    assert body == b"\x1f\x8bfake-volume"

    # Only the basename survives when the markup separates the directory.
    status, _headers, body = _request_bytes(
        launcher["port"],
        f"/neurodesk-file/sub-01_T1w_brain_mask.nii.gz{query}",
        headers=launcher["auth"],
    )
    assert status == 200
    assert body == b"\x1f\x8bfake-volume"


def test_preview_serves_user_chosen_projects_but_never_home(launcher):
    """Previews follow a session into a user-chosen project, nothing wider.

    A session pinned to an Add-project directory must preview its files, but
    the ``dir`` fallback naming home (or any non-project directory) must not
    turn the endpoint into a home-wide file reader.
    """
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)

    study = launcher["home"] / "projects" / "study-c"
    study.mkdir(parents=True)
    (study / "qc_overlay.png").write_bytes(b"\x89PNG\r\n\x1a\nstudy")
    (launcher["home"] / "home_only.png").write_bytes(b"home-secret")

    status, _headers, body = _request(
        launcher["port"],
        "/session",
        method="POST",
        data=b"{}",
        headers={
            **launcher["auth"],
            "Content-Type": "application/json",
            "x-opencode-directory": urllib.parse.quote(str(study), safe=""),
        },
    )
    assert status == 200
    session = json.loads(body)

    status, headers, body = _request_bytes(
        launcher["port"],
        f"/neurodesk-file/qc_overlay.png?session={session['id']}",
        headers=launcher["auth"],
    )
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert body == b"\x89PNG\r\n\x1a\nstudy"

    # Naming home via the dir fallback resolves to the single known session
    # project, so a file that exists only in home stays unreachable.
    home_query = urllib.parse.urlencode({"dir": str(launcher["home"])})
    status, _headers, _body = _request_bytes(
        launcher["port"],
        f"/neurodesk-file/home_only.png?{home_query}",
        headers=launcher["auth"],
    )
    assert status == 404


def test_preview_endpoint_is_credentialed_and_confined(launcher):
    """The preview route is neither anonymous nor a general file reader."""
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)
    session, directory = _session_with_files(launcher)
    query = f"?session={session['id']}"

    escape = launcher["home"] / "private.png"
    escape.write_bytes(b"private")

    status, _headers, _body = _request_bytes(
        launcher["port"], f"/neurodesk-file/qc_bet_sub01.png{query}"
    )
    assert status == 401

    # Percent-encoded traversal out of the session project.
    status, _headers, _body = _request_bytes(
        launcher["port"],
        f"/neurodesk-file/%2e%2e%2f%2e%2e%2fprivate.png{query}",
        headers=launcher["auth"],
    )
    assert status == 404

    # Text files stay with the diff view; the endpoint refuses them outright.
    status, _headers, _body = _request_bytes(
        launcher["port"],
        f"/neurodesk-file/analysis_01_brain_extraction.sh{query}",
        headers=launcher["auth"],
    )
    assert status == 415

    # One session cannot read another session's files, even by exact name.
    (directory / "only_in_first_session.png").write_bytes(b"first")
    other, other_directory = _session_with_files(launcher)
    assert other_directory != directory
    status, _headers, _body = _request_bytes(
        launcher["port"],
        f"/neurodesk-file/only_in_first_session.png?session={other['id']}",
        headers=launcher["auth"],
    )
    assert status == 404

    # A name that exists nowhere in the project is a plain miss.
    status, _headers, _body = _request_bytes(
        launcher["port"], f"/neurodesk-file/missing.png{query}",
        headers=launcher["auth"],
    )
    assert status == 404

    # An unknown or stale session must fail closed rather than widen the
    # lookup to the shared ~/opencode-work parent, where the unique name
    # would resolve to another session's file.
    for unknown in ("ses_does_not_exist", "not-a-session-id"):
        status, _headers, _body = _request_bytes(
            launcher["port"],
            f"/neurodesk-file/only_in_first_session.png?session={unknown}",
            headers=launcher["auth"],
        )
        assert status == 404


def test_niivue_bundle_is_served_and_degrades_when_absent(launcher):
    """The vendored viewer is cacheable; a missing bundle is a plain 404."""
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)

    status, _headers, script = _request(
        launcher["port"], "/neurodesk-preview.js", headers=launcher["auth"]
    )
    assert status == 200
    version = re.search(r'"niivueVersion": "([0-9a-f]+)"', script).group(1)

    status, headers, body = _request_bytes(
        launcher["port"], f"/neurodesk-niivue.js?v={version}",
        headers=launcher["auth"],
    )
    assert status == 200
    assert headers["Content-Type"].startswith("text/javascript")
    # Cached hard, but only ever reached through the content-versioned URL.
    assert "immutable" in headers["Cache-Control"]
    assert headers["ETag"] == f'"{version}"'
    assert b"Niivue" in body

    launcher["niivue_bundle"].unlink()
    status, _headers, _body = _request_bytes(
        launcher["port"], "/neurodesk-niivue.js", headers=launcher["auth"]
    )
    assert status == 404


def test_previewer_script_is_served_with_the_forwarded_prefix(launcher):
    """The injected previewer addresses the proxy under Jupyter's prefix."""
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)

    status, _headers, body = _request(
        launcher["port"],
        "/neurodesk-preview.js",
        headers={**launcher["auth"], "X-Forwarded-Prefix": "/user/alice/opencode"},
    )
    assert status == 200
    assert '"prefix": "/user/alice/opencode"' in body


def test_force_directory_header_pins_and_encodes_like_opencodes_client():
    """The header is rewritten percent-encoded, as OpenCode's client sends it."""
    forced = ocw.force_directory_header(
        urllib.parse.quote("/home/jovyan/demo", safe=""), "/seed/work dir"
    )
    assert forced == "%2Fseed%2Fwork%20dir"
    assert urllib.parse.unquote(forced) == "/seed/work dir"


def test_force_directory_header_without_a_work_dir_is_a_no_op():
    """Before the backend has a work dir the header passes through unchanged."""
    assert ocw.force_directory_header("%2Fhome%2Fjovyan", "") == "%2Fhome%2Fjovyan"


def test_force_directory_query_pins_the_bracketed_api_route_parameter():
    """OpenCode's /api/ routes carry the directory as ``location[directory]``."""
    forced = ocw.force_directory_query(
        "location%5Bdirectory%5D=%2Fhome%2Fjovyan%2Fdemo&a=1", "/seed"
    )
    assert urllib.parse.parse_qsl(forced) == [
        ("location[directory]", "/seed"), ("a", "1")
    ]


def test_requests_without_credentials_are_rejected(launcher):
    """Every request needs the injected credential or a session cookie."""
    status, _headers, _body = _request(launcher["port"], "/")
    assert status == 401


def test_setup_page_shown_until_key_is_configured(launcher):
    """First launch renders the llm.neurodesk.org key setup page."""
    status, _headers, body = _request(
        launcher["port"], "/", headers=launcher["auth"]
    )
    assert status == 200
    assert "Set up your Neurodesk LLM API key" in body
    assert "API Keys" in body
    assert "Create new secret key" in body
    assert "model picker" in body
    assert "choose any available model" in body


def test_rejected_key_reprompts_and_does_not_persist(launcher):
    """A 401-rejected key reprompts and never lands in ~/.bashrc."""
    status, _headers, body = _request(
        launcher["port"],
        "/neurodesk-setup",
        headers={**launcher["auth"],
                 "Content-Type": "application/x-www-form-urlencoded"},
        data=b"key=wrong-key",
    )
    assert status == 200
    assert "That API key was rejected" in body

    # The setup page is shown again and nothing was persisted.
    _status, _h, body = _request(
        launcher["port"], "/", headers=launcher["auth"]
    )
    assert "Set up your Neurodesk LLM API key" in body
    assert not (launcher["home"] / ".bashrc").exists() or (
        "wrong-key"
        not in (launcher["home"] / ".bashrc").read_text(encoding="utf-8")
    )


def test_valid_key_persists_starts_backend_and_proxies_with_rewrite(launcher):
    """The happy path: key saved, backend started, bodies rewritten."""
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
    bootstrap_tag = f'<script src="{prefix}/neurodesk-prefix.js"></script>'
    assert bootstrap_tag in body
    assert body.index(bootstrap_tag) < body.index('type="module"')

    status, headers, bootstrap = _request(
        launcher["port"],
        "/neurodesk-prefix.js",
        headers={**launcher["auth"], "X-Forwarded-Prefix": prefix},
    )
    assert status == 200
    assert headers["Content-Type"].startswith("text/javascript")
    assert "opencode.settings.dat:defaultServerUrl" in bootstrap
    assert json.dumps(prefix) in bootstrap

    status, _headers, js_body = _request(
        launcher["port"],
        "/assets/index.js",
        headers={**launcher["auth"], "X-Forwarded-Prefix": prefix},
    )
    assert status == 200
    assert f'"{prefix}/assets/inter.woff2"' in js_body
    assert "window.__NEURODESK_OPENCODE_SERVER_URL__" in js_body
    assert "window.__NEURODESK_OPENCODE_BASE_PATH__" in js_body

    # OpenCode's native model picker obtains its provider/model catalogue from
    # this route. The browser bootstrap makes the real client request it below
    # {prefix} instead of escaping to Jupyter's root.
    status, _headers, provider_body = _request(
        launcher["port"], "/provider", headers=launcher["auth"]
    )
    assert status == 200
    assert "model-alpha" in provider_body

    # Without a prefix (desktop mode) bodies pass through untouched.
    status, _headers, plain_body = _request(
        launcher["port"], "/", headers=launcher["auth"]
    )
    assert 'src="/assets/index.js"' in plain_body
    assert "neurodesk-prefix.js" not in plain_body

    # The backend saw the key, the shared password, and the wrapper args.
    backend_env = json.loads(
        (launcher["state"] / "env.json").read_text(encoding="utf-8")
    )
    assert backend_env["NEURODESK_API_KEY"] == "good-key"
    assert backend_env["OPENCODE_SERVER_PASSWORD"] == launcher["password"]
    assert backend_env["OPENCODE_DISABLE_FFF"] == "1"
    assert backend_env["OPENCODE_MODEL_PROFILE"] == "neurodesk"
    assert backend_env["BASH_ENV"] == ocw.DEFAULT_BASH_ENV
    work_dir = Path(backend_env["cwd"])
    assert work_dir == launcher["home"] / "opencode-work"
    assert (work_dir / ".git").is_dir()
    assert backend_env["argv"][0] == "web"
    assert "--hostname" in backend_env["argv"]


def test_sse_response_remains_chunked_through_launcher_proxy(launcher):
    """Jupyter's progressive proxy must receive framed SSE chunks."""
    status, _headers = _complete_key_setup(launcher)
    assert status == 303
    _wait_for_proxied_root(launcher)

    conn = http.client.HTTPConnection(
        "127.0.0.1", launcher["port"], timeout=5
    )
    conn.request("GET", "/global/event", headers=launcher["auth"])
    response = conn.getresponse()
    try:
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/event-stream"
        assert response.getheader("Transfer-Encoding") == "chunked"
        assert response.getheader("Connection") != "close"
        assert b"server.connected" in response.read()
    finally:
        conn.close()


def test_skip_starts_backend_without_key(launcher):
    """Skipping key setup still starts the backend (other providers)."""
    status, _headers = _complete_key_setup(launcher, b"skip=1")
    assert status == 303
    _wait_for_proxied_root(launcher)
    backend_env = json.loads(
        (launcher["state"] / "env.json").read_text(encoding="utf-8")
    )
    assert backend_env["NEURODESK_API_KEY"] == ""


def test_sanitize_header_value_strips_crlf():
    """Header values built from request data must lose CR/LF."""
    assert ocw.sanitize_header_value("/x\r\nInjected: 1") == "/xInjected: 1"
    assert ocw.sanitize_header_value("clean") == "clean"


def _read_login_token(ctx):
    """Read the current single-use login token the launcher wrote to disk."""
    return ctx["token_file"].read_text(encoding="utf-8").strip()


def test_auth_redirect_cannot_become_protocol_relative(launcher):
    """A crafted //host path must not turn the cookie exchange into an open
    redirect: the Location header is normalized to a single-slash path."""
    quoted = urllib.parse.quote(_read_login_token(launcher))
    status, headers, _body = _request(
        launcher["port"], f"//evil.example/?auth={quoted}"
    )
    assert status == 303
    location = headers.get("Location", "")
    assert location.startswith("/")
    assert not location.startswith("//")


def test_login_token_exchanges_for_cookie_and_cannot_be_replayed(launcher):
    """The desktop ?auth= login token is single-use: it yields a session
    cookie and is rotated immediately, so a leaked URL cannot be replayed.
    The password itself is never accepted in the URL."""
    token = _read_login_token(launcher)
    quoted = urllib.parse.quote(token)
    status, headers, _body = _request(launcher["port"], f"/?auth={quoted}")
    assert status == 303
    cookie = headers.get("Set-Cookie", "")
    assert "neurodesk_opencode_auth=" in cookie
    assert "HttpOnly" in cookie

    cookie_value = cookie.split(";", 1)[0]
    status, _headers, body = _request(
        launcher["port"], "/", headers={"Cookie": cookie_value}
    )
    assert status == 200
    # Setup page renders, i.e. the cookie authorized the request.
    assert "Set up your Neurodesk LLM API key" in body

    # Replaying the consumed token is rejected, and the file now holds a
    # fresh one.
    status, _headers, _body = _request(launcher["port"], f"/?auth={quoted}")
    assert status == 401
    assert _read_login_token(launcher) != token

    # The reusable proxy password is not accepted via the URL at all.
    quoted_password = urllib.parse.quote(launcher["password"])
    status, _headers, _body = _request(
        launcher["port"], f"/?auth={quoted_password}"
    )
    assert status == 401


def test_login_token_consumed_by_exactly_one_concurrent_request(launcher):
    """Two parallel requests with the same token must not both authenticate:
    compare-and-rotate is atomic, so exactly one gets the cookie."""
    token = _read_login_token(launcher)
    quoted = urllib.parse.quote(token)
    results = []

    def attempt():
        """Try the exchange and record the resulting status code."""
        status, headers, _body = _request(launcher["port"], f"/?auth={quoted}")
        results.append((status, "Set-Cookie" in headers))

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(status for status, _cookie in results)
    assert statuses.count(303) == 1, results
    assert all(status == 401 for status in statuses if status != 303), results


def test_unexpected_validation_status_accepts_key_unverified(launcher):
    """A 5xx from llm.neurodesk.org must not reject the key (or claim it was
    verified) - setup completes and the key is persisted."""
    status, _headers = _complete_key_setup(launcher, b"key=flaky-key")
    assert status == 303
    bashrc = (launcher["home"] / ".bashrc").read_text(encoding="utf-8")
    assert "export NEURODESK_API_KEY='flaky-key'" in bashrc


def test_existing_bashrc_key_skips_setup_and_web_prefers_neurodesk(tmp_path):
    """A key configured earlier (e.g. via the terminal wrapper) boots straight
    into the proxied UI without showing the setup page."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    (home_dir / ".bashrc").write_text(
        "# Neurodesk API key for OpenCode\n"
        "export NEURODESK_API_KEY='good-key'\n",
        encoding="utf-8",
    )
    # Web launches use Neurodesk even when a terminal run selected Ollama.
    config_dir = home_dir / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    (config_dir / "opencode.json").write_text(
        json.dumps({"model": "ollama/model-alpha"}), encoding="utf-8"
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
        assert backend_env["OPENCODE_MODEL_PROFILE"] == "neurodesk"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
