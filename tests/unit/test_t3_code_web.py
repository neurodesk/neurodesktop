"""T3 base-path compatibility, credential isolation and browser transport."""
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from testlib import repo_path

sys.path.insert(0, str(repo_path("extensions/t3-code-server")))
pytest.importorskip("jupyter_server_proxy")
from neurodesk_t3_code.web import rewrite_client, T3ProxyHandler
from tornado.httputil import HTTPHeaders


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
