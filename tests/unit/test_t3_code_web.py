"""T3 base-path compatibility, credential isolation and browser transport."""
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from testlib import reload_browsing_context, repo_path

sys.path.insert(0, str(repo_path("extensions/t3-code-server")))
pytest.importorskip("jupyter_server_proxy")
from neurodesk_t3_code.web import rewrite_client, T3ProxyHandler
from tornado.httputil import HTTPHeaders


def test_browser_reload_commits_without_waiting_for_page_readiness():
    """The T3 readiness poll, rather than BiDi, owns reload completion."""
    requests = []

    class Bidi:
        def request(self, method, params):
            requests.append((method, params))
            return {"navigation": "reload", "url": "http://localhost/neurodesk-t3/"}

    reload_browsing_context(Bidi(), "t3-frame")

    assert requests == [(
        "browsingContext.reload",
        {"context": "t3-frame", "wait": "none"},
    )]


@pytest.mark.parametrize("method,site,path,allowed", [
    ("GET", "same-origin", "assets/index-a.js", True),
    ("GET", "same-origin", "assets/pullRequestDetail.logic-BVaUUc1S.js", True),
    ("GET", "same-origin", "assets/BranchToolbar.logic-QOG-LgPV.js", True),
    ("HEAD", "same-origin", "assets/main-a.js", True),
    ("GET", "same-origin", "assets/style-a.css", True),
    ("GET", "same-origin", "assets/worker-a.wasm", True),
    ("GET", "same-origin", "manifest.webmanifest", True),
    ("POST", "same-origin", "assets/index-a.js", False),
    ("GET", "cross-site", "assets/index-a.js", False),
    ("GET", "same-site", "assets/index-a.js", False),
    ("GET", None, "assets/index-a.js", False),
    ("GET", "same-origin", "api/session", False),
    ("GET", "same-origin", "assets/preview.html", False),
    ("GET", "same-origin", "assets/../api/file.js", False),
])
@pytest.mark.parametrize("prefix", ["/neurodesk-t3/", "/user/alice/neurodesk-t3/"])
def test_hub_module_requests_keep_xsrf_checks_for_api_writes_and_other_origins(
    monkeypatch, method, site, path, allowed, prefix
):
    from neurodesk_t3_code.web import JupyterHandler
    from tornado.web import HTTPError

    def require_token(handler):
        raise HTTPError(403, "missing XSRF token")

    monkeypatch.setattr(JupyterHandler, "check_xsrf_cookie", require_token)
    handler = object.__new__(T3ProxyHandler)
    handler.prefix = prefix
    headers = HTTPHeaders({"Sec-Fetch-Mode": "cors"})
    if site:
        headers["Sec-Fetch-Site"] = site
    handler.request = SimpleNamespace(method=method, path=prefix + path, headers=headers)
    if allowed:
        handler.check_xsrf_cookie()
    else:
        with pytest.raises(HTTPError):
            handler.check_xsrf_cookie()


@pytest.mark.parametrize("prefix", ["/neurodesk-t3/", "/user/alice/neurodesk-t3/"])
def test_shell_and_chunks_stay_under_jupyter_base(prefix):
    shell = b'<head></head><div id="root"><div id="boot-shell"></div><script src="/assets/index-a.js"></script>'
    rewritten = rewrite_client(shell, "text/html", "/", prefix).decode()
    assert f'src="{prefix}_adapter.js"' in rewritten
    assert f'src="{prefix}assets/index-a.js"' in rewritten
    router = b'createRouter({routeTree:r,history:h,context:{},defaultPreload:`intent`})'
    assert f'basepath:{json.dumps(prefix[:-1])}' in rewrite_client(router, "text/javascript", "/assets/main-a.js", prefix).decode()
    loader = b'function(e){return`/`+e};new Worker(`/assets/worker-a.js`)'
    result = rewrite_client(loader, "text/javascript", "/assets/index-a.js", prefix).decode()
    assert f'return{json.dumps(prefix)}+e' in result
    assert f'new Worker(`{prefix}assets/worker-a.js`)' in result


@pytest.mark.parametrize("crossorigin", ["", ' crossorigin', ' crossorigin="anonymous"'])
def test_manifest_uses_jupyter_login_cookie(crossorigin):
    shell = (
        '<head><link rel="manifest"' + crossorigin + ' href="/manifest.webmanifest"></head>'
        '<div id="root"><div id="boot-shell"></div></div>'
    ).encode()
    result = rewrite_client(shell, "text/html", "/", "/user/alice/neurodesk-t3/").decode()
    assert '<link crossorigin="use-credentials" rel="manifest" href="/user/alice/neurodesk-t3/manifest.webmanifest">' in result
    assert result.count("crossorigin") == 1


def test_upstream_drift_fails_instead_of_serving_broken_ui():
    for name in ("main", "index"):
        with pytest.raises(ValueError, match="changed"):
            rewrite_client(b"changed upstream", "text/javascript", f"/assets/{name}-new.js", "/neurodesk-t3/")


def test_api_and_preview_payloads_are_never_rewritten():
    for path, mime in [("/api/file", "application/json"), ("/assets/signed-preview", "text/html")]:
        body = b'<head><div id="root"><div id="boot-shell">/assets/file</div>'
        assert rewrite_client(body, mime, path, "/neurodesk-t3/") == body


def test_proxy_drops_jupyter_credentials_and_preserves_t3_session():
    handler = object.__new__(T3ProxyHandler)
    handler.request = SimpleNamespace(headers=HTTPHeaders({
        "Authorization": "token jupyter-secret", "X-XSRFToken": "xsrf-secret",
        "Cookie": "username=secret; _xsrf=secret; t3_session_3773=value",
        "Accept-Encoding": "gzip",
    }))
    headers = handler.proxy_request_headers()
    assert "Authorization" not in headers
    assert "X-XSRFToken" not in headers
    assert headers["Cookie"] == "t3_session_3773=value"
    assert headers["Accept-Encoding"] == "identity"


def test_browser_transport_preserves_requests_and_external_endpoints(tmp_path):
    adapter = repo_path("extensions/t3-code-server/neurodesk_t3_code/web_adapter.js").read_text()
    script = '''
const assert = require('node:assert/strict');
global.location = new URL('https://jupyter.example/user/alice/neurodesk-t3/');
global.document = {cookie: '_xsrf=test-xsrf'};
let sent;
class Socket { constructor(url, protocols) { this.url=url; this.protocols=protocols; } }
global.window = {fetch: async (r, init) => { sent = new Request(r, init); return new Response('ok'); }, WebSocket: Socket};
''' + adapter.replace("__T3_PREFIX__", json.dumps("/user/alice/neurodesk-t3/")) + '''
(async () => {
await window.fetch('https://jupyter.example/api/test', {method:'POST', body:'payload', headers:{'X-Test':'yes'}});
assert.equal(sent.url, 'https://jupyter.example/user/alice/neurodesk-t3/api/test');
assert.equal(sent.method, 'POST');
assert.equal(await sent.text(), 'payload');
assert.equal(sent.headers.get('X-Test'), 'yes');
assert.equal(sent.headers.get('X-XSRFToken'), 'test-xsrf');
await window.fetch(new Request('https://jupyter.example/api/test', {method:'POST', body:'request-body'}));
assert.equal(await sent.text(), 'request-body');
await window.fetch('http://jupyter.example/api/test');
assert.equal(sent.url, 'http://jupyter.example/api/test');
assert.equal(sent.headers.get('X-XSRFToken'), null);
await window.fetch('https://remote.example/api/test', {headers:{Authorization:'DPoP secret'}});
assert.equal(sent.url, 'https://remote.example/api/test');
assert.equal(sent.headers.get('Authorization'), 'DPoP secret');
assert.equal(sent.headers.get('X-XSRFToken'), null);
await window.fetch('https://jupyter.example/user/alice/neurodesk-t3/api/test');
assert.equal(sent.url, 'https://jupyter.example/user/alice/neurodesk-t3/api/test');
const ws = new window.WebSocket('wss://jupyter.example/ws', ['protocol']);
assert.equal(ws.url, 'wss://jupyter.example/user/alice/neurodesk-t3/ws');
assert.deepEqual(ws.protocols, ['protocol']);
assert.equal(new window.WebSocket('wss://remote.example/ws').url, 'wss://remote.example/ws');
})().catch(error => { console.error(error); process.exit(1); });
'''
    target = tmp_path / "transport.cjs"
    target.write_text(script)
    subprocess.run(["node", str(target)], check=True, capture_output=True, text=True)


def test_signed_file_urls_are_adapted_before_images_or_previews_use_them():
    source = b'function resolve(e,t){try{return new URL(t,e).toString()}catch{return null}}'
    result = rewrite_client(source, "text/javascript", "/assets/assets-abc.js", "/user/alice/neurodesk-t3/")
    assert b'window.__neurodeskT3Target(new URL(t,e).toString()).url' in result
    with pytest.raises(ValueError, match="resolver changed"):
        rewrite_client(b"upstream changed", "text/javascript", "/assets/assets-new.js", "/neurodesk-t3/")


def test_token_query_is_not_forwarded_to_t3():
    handler = object.__new__(T3ProxyHandler)
    handler.request = SimpleNamespace(query="token=jupyter-secret&path=a%2Fb&empty=")
    handler.absolute_url = False
    handler.unix_socket = None
    uri = handler.get_client_uri("http", "127.0.0.1", 3773, "/api/test")
    assert uri == "http://127.0.0.1:3773/api/test?path=a%2Fb&empty="


def test_session_cookies_are_scoped_and_cache_validators_removed():
    handler = object.__new__(T3ProxyHandler)
    handler.prefix = "/user/alice/neurodesk-t3/"
    response = SimpleNamespace(code=200, body=b'{}', headers=HTTPHeaders({
        "Content-Type": "application/json", "ETag": '"old"',
        "Cache-Control": "public, max-age=31536000, immutable",
        "Set-Cookie": "t3_session_3773=session; Path=/; HttpOnly; SameSite=Lax",
    }))
    handler._rewrite(response, "/api/auth/session")
    assert response.headers["Set-Cookie"] == (
        "t3_session_3773=session; Path=/user/alice/neurodesk-t3/; HttpOnly; SameSite=Lax"
    )
    assert "ETag" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("service", [None, SimpleNamespace(state="parked")])
def test_unavailable_or_unready_sidecar_is_not_proxied(service):
    import asyncio
    from tornado.web import HTTPError

    handler = object.__new__(T3ProxyHandler)
    handler.t3_app = SimpleNamespace(_supervisor=service)
    with pytest.raises(HTTPError) as error:
        asyncio.run(handler._forward("api/status"))
    assert error.value.status_code == 503


@pytest.mark.parametrize("paired", [False, True])
@pytest.mark.parametrize("prefix", ["/", "/user/alice/"])
def test_launcher_session_exchanges_credentials_privately(monkeypatch, paired, prefix):
    import asyncio
    from neurodesk_t3_code import web as t3web
    from neurodesk_t3_code.supervisor import ServiceState

    requests, credentials, headers = [], [], []

    class Client:
        async def fetch(self, url, **kwargs):
            requests.append((url, kwargs))
            if url.endswith("/session"):
                return SimpleNamespace(code=200, body=json.dumps({"authenticated": paired}).encode())
            assert json.loads(kwargs["body"]) == {"credential": "private-pairing-secret"}
            return SimpleNamespace(
                body=b'{"authenticated":true}',
                headers=HTTPHeaders({"Set-Cookie": "t3_session_3773=session; Path=/; HttpOnly; SameSite=Lax"}),
            )

    async def issue(service):
        credentials.append(service)
        return "private-pairing-secret"

    monkeypatch.setattr(t3web.httpclient, "AsyncHTTPClient", Client)
    monkeypatch.setattr(t3web, "pairing_credential", issue)
    handler = SimpleNamespace(
        t3_app=SimpleNamespace(_supervisor=SimpleNamespace(
            state=ServiceState.READY, policy=SimpleNamespace(readiness_host="127.0.0.1", port=3773))),
        base_url=prefix,
        request=SimpleNamespace(protocol="https", headers=HTTPHeaders({
            "Cookie": "jupyter=secret; t3_session_3773=existing", "Authorization": "token private-jupyter",
        })),
        set_header=lambda k, v: headers.append((k, v)),
        add_header=lambda k, v: headers.append((k, v)),
        set_status=lambda status: headers.append(("status", status)),
        finish=lambda: None,
    )
    asyncio.run(t3web.T3SessionHandler.post.__wrapped__(handler))
    assert requests[0][1]["headers"] == {"Cookie": "t3_session_3773=existing"}
    assert len(requests) == (1 if paired else 2)
    assert len(credentials) == (0 if paired else 1)
    assert ("status", 204) in headers
    assert ("Cache-Control", "no-store") in headers
    assert "private-pairing-secret" not in str(headers)
    if not paired:
        assert ("Set-Cookie", f"t3_session_3773=session; Path={prefix}neurodesk-t3/; HttpOnly; SameSite=Lax; Secure") in headers
        assert requests[1][1]["headers"] == {"Content-Type": "application/json"}
        assert requests[1][1]["follow_redirects"] is False


def test_session_failure_never_includes_upstream_credential(monkeypatch):
    import asyncio
    from neurodesk_t3_code import web as t3web
    from neurodesk_t3_code.supervisor import ServiceState
    from tornado.web import HTTPError

    class Client:
        async def fetch(self, *args, **kwargs):
            raise t3web.httpclient.HTTPClientError(500, "private-pairing-secret")

    monkeypatch.setattr(t3web.httpclient, "AsyncHTTPClient", Client)
    handler = SimpleNamespace(
        t3_app=SimpleNamespace(_supervisor=SimpleNamespace(
            state=ServiceState.READY, policy=SimpleNamespace(readiness_host="127.0.0.1", port=3773))),
        request=SimpleNamespace(headers=HTTPHeaders()), set_header=lambda *args: None,
    )
    with pytest.raises(HTTPError) as error:
        asyncio.run(t3web.T3SessionHandler.post.__wrapped__(handler))
    assert error.value.status_code == 503
    assert "private-pairing-secret" not in str(error.value)
    assert error.value.__suppress_context__


def test_pairing_cli_uses_supervised_state_and_keeps_output_private(tmp_path, capfd):
    import asyncio
    import os
    from neurodesk_t3_code.web import pairing_credential

    executable = tmp_path / "t3"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "assert sys.argv[1:4] == ['auth', 'pairing', 'create']\n"
        "assert sys.argv[4:6] == ['--base-dir', os.environ['T3CODE_HOME']]\n"
        "assert sys.argv[6:] == ['--ttl', '1m', '--label', 'JupyterLab', '--json']\n"
        "print(json.dumps({'credential': 'private-cli-secret'}))\n"
        "print('private-stderr-secret', file=sys.stderr)\n"
    )
    executable.chmod(0o755)
    service = SimpleNamespace(
        policy=SimpleNamespace(executable=executable, base_dir=tmp_path / ".t3",
                               workdir=tmp_path, home=tmp_path, provider_bin=tmp_path,
                               host="127.0.0.1", port=3773),
        environ=os.environ.copy(),
    )
    assert asyncio.run(pairing_credential(service)) == "private-cli-secret"
    captured = capfd.readouterr()
    assert "private-cli-secret" not in captured.out + captured.err
    assert "private-stderr-secret" not in captured.out + captured.err


def test_timed_out_pairing_cli_is_reaped(monkeypatch):
    import asyncio
    from neurodesk_t3_code import web as t3web

    class Process:
        returncode = None
        reaped = False

        async def communicate(self):
            if self.returncode is None:
                raise asyncio.TimeoutError()
            self.reaped = True
            return b"", None

        def kill(self):
            self.returncode = -9

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(t3web.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(t3web, "server_environment", lambda *args: {})
    service = SimpleNamespace(policy=SimpleNamespace(executable="/t3", base_dir="/state", workdir="/work"), environ={})
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(t3web.pairing_credential(service))
    assert process.returncode == -9
    assert process.reaped
