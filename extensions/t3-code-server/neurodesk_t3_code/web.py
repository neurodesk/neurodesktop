"""Authenticated, fixed-target proxy for the pinned T3 web client.

T3 0.0.42 has no base-path option. Adapt only responses under this route;
never modify the installed client or claim root-level Jupyter routes.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jupyter_server.base.handlers import APIHandler, JupyterHandler
from jupyter_server.utils import url_path_join
from jupyter_server_proxy.handlers import ProxyHandler
from tornado import httpclient, web

from .supervisor import ServiceState, server_environment


ROUTE = "neurodesk-t3"
ROUTER_ANCHOR = "context:{},defaultPreload:`intent`"
PRELOAD_ANCHOR = "return`/`+e"
ASSET_URL_ANCHOR = "try{return new URL(t,e).toString()}catch{return null}"


def session_cookie_header(headers):
    return "; ".join(
        part.strip() for part in headers.get("Cookie", "").split(";")
        if part.strip().startswith("t3_session_")
    )


def scoped_cookie(cookie, prefix):
    return re.sub(r"(?i)(;\s*path=)/(?=;|$)", lambda m: m[1] + prefix, cookie)


async def pairing_credential(service):
    """Mint a short-lived credential without putting it in URLs or logs."""
    policy = service.policy
    process = await asyncio.create_subprocess_exec(
        str(policy.executable), "auth", "pairing", "create",
        "--base-dir", str(policy.base_dir), "--ttl", "1m",
        "--label", "JupyterLab", "--json",
        env=server_environment(policy, service.environ), cwd=policy.workdir,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()
    if process.returncode:
        raise ValueError("T3 session credential could not be created")
    credential = json.loads(output)["credential"]
    if not isinstance(credential, str) or not credential:
        raise ValueError("T3 returned an invalid credential")
    return credential


class T3SessionHandler(APIHandler):
    """Exchange Jupyter authentication for a scoped T3 browser session."""

    def initialize(self, t3_app):
        self.t3_app = t3_app

    @web.authenticated
    async def post(self):
        self.set_header("Cache-Control", "no-store")
        service = self.t3_app._supervisor
        if service is None or service.state is not ServiceState.READY:
            raise web.HTTPError(503, reason="T3 Code is starting. Wait a moment and reopen it.")
        host = service.policy.readiness_host
        authority = f"[{host}]" if ":" in host else host
        origin = f"http://{authority}:{service.policy.port}"
        client = httpclient.AsyncHTTPClient()
        try:
            session = await client.fetch(
                origin + "/api/auth/session",
                headers={"Cookie": session_cookie_header(self.request.headers)},
                request_timeout=5, follow_redirects=False, raise_error=False,
            )
            if session.code not in {200, 401, 403}:
                raise ValueError("T3 session lookup failed")
            if session.code != 200 or json.loads(session.body).get("authenticated") is not True:
                credential = await pairing_credential(service)
                response = await client.fetch(
                    origin + "/api/auth/browser-session", method="POST",
                    headers={"Content-Type": "application/json"},
                    body=json.dumps({"credential": credential}),
                    request_timeout=5, follow_redirects=False,
                )
                cookies = [cookie for cookie in response.headers.get_list("Set-Cookie")
                           if cookie.startswith("t3_session_")]
                if not json.loads(response.body).get("authenticated") or not cookies:
                    raise ValueError("T3 did not establish a session")
                prefix = url_path_join(self.base_url, ROUTE) + "/"
                for cookie in cookies:
                    cookie = scoped_cookie(cookie, prefix)
                    if self.request.protocol == "https":
                        cookie += "; Secure"
                    self.add_header("Set-Cookie", cookie)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, asyncio.TimeoutError,
                httpclient.HTTPClientError):
            # Neither CLI output nor upstream error bodies belong in diagnostics.
            raise web.HTTPError(
                503, reason="Could not connect to T3 Code. Wait a moment and reopen it."
            ) from None
        self.set_status(204)
        self.finish()


def rewrite_client(body: bytes, content_type: str, path: str, prefix: str) -> bytes:
    """Adapt HTML, Vite asset URLs and the app router, never API payloads."""
    if path.startswith("/assets/") and path.endswith(".js"):
        text = body.decode("utf-8")
        if re.fullmatch(r"/assets/main-[\w-]+\.js", path):
            if text.count(ROUTER_ANCHOR) != 1:
                raise ValueError("T3 web router changed; update the base-path adapter")
            text = text.replace(
                ROUTER_ANCHOR,
                "basepath:" + json.dumps(prefix[:-1]) + "," + ROUTER_ANCHOR,
            )
        if re.fullmatch(r"/assets/index-[\w-]+\.js", path):
            if text.count(PRELOAD_ANCHOR) != 1:
                raise ValueError("T3 asset loader changed; update the base-path adapter")
            text = text.replace(PRELOAD_ANCHOR, "return" + json.dumps(prefix) + "+e")
        if re.fullmatch(r"/assets/assets-[\w-]+\.js", path):
            if text.count(ASSET_URL_ANCHOR) != 1:
                raise ValueError("T3 file URL resolver changed; update the base-path adapter")
            text = text.replace(
                ASSET_URL_ANCHOR,
                "try{return window.__neurodeskT3Target(new URL(t,e).toString()).url}"
                "catch{return null}",
            )
        # Worker and WASM assets use absolute URLs even though JS imports are relative.
        text = re.sub(r'(["\'`])/assets/', lambda m: m[1] + prefix + "assets/", text)
        return text.encode()
    if content_type.split(";", 1)[0] == "text/html" and not path.startswith(
        ("/api/", "/assets/")
    ):
        text = body.decode("utf-8")
        # Only the application shell gets executable adapter code. File previews do not.
        if '<div id="root">' not in text or 'id="boot-shell"' not in text:
            return body
        text = re.sub(r'((?:src|href)=")/(?!/)', lambda m: m[1] + prefix, text)
        # Manifests omit cookies by default, even for same-origin URLs.
        text = re.sub(
            r'<link\b[^>]*\brel="manifest"[^>]*>',
            lambda m: re.sub(r'\s+crossorigin(?:="[^"]*")?', '', m[0])
            .replace('<link', '<link crossorigin="use-credentials"', 1),
            text,
        )
        bootstrap = '<script src="' + prefix + '_adapter.js"></script>'
        return text.replace("<head>", "<head>" + bootstrap, 1).encode()
    if path.startswith("/assets/") and path.endswith(".css"):
        return re.sub(rb'url\(([/])(?!/)', lambda _: b'url(' + prefix.encode(), body)
    return body


class T3StatusHandler(APIHandler):
    def initialize(self, t3_app):
        self.t3_app = t3_app

    @web.authenticated
    def get(self):
        service = self.t3_app._supervisor
        self.set_header("Cache-Control", "no-store")
        self.finish({
            "state": service.state.value if service else "unavailable",
        })


class T3ProxyHandler(ProxyHandler):
    def initialize(self, t3_app):
        self.t3_app = t3_app
        self.prefix = url_path_join(self.base_url, ROUTE) + "/"
        self.proxy_base = "/" + ROUTE
        self.rewrite_response = lambda response, path: self._rewrite(response, path)
        self.host_allowlist = lambda handler, host: (
            self.t3_app._supervisor is not None
            and host == self.t3_app._supervisor.policy.readiness_host
        )

    def check_xsrf_cookie(self):
        # Hub 6 checks cookie-authenticated CORS GETs too. Native module imports
        # cannot add the XSRF header supplied by our fetch adapter. Fetch Metadata
        # establishes the origin for these read-only, fixed static assets; it is
        # browser-controlled. Authentication still runs in the proxy handler.
        path = self.request.path.removeprefix(self.prefix)
        if (
            self.request.method in {"GET", "HEAD"}
            and self.request.headers.get("Sec-Fetch-Site") == "same-origin"
            and self.request.path.startswith(self.prefix)
            and (path == "manifest.webmanifest"
                 or re.fullmatch(r"assets/[\w-]+(?:\.[\w-]+)*\.(?:js|css|wasm)", path))
        ):
            return
        # Unlike the generic proxy, this app's fetch adapter supplies Jupyter XSRF.
        JupyterHandler.check_xsrf_cookie(self)

    def get_client_uri(self, protocol, host, port, proxied_path):
        uri = super().get_client_uri(protocol, host, port, proxied_path)
        parts = urlsplit(uri)
        query = urlencode([
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key != "token"
        ])
        return urlunsplit(parts._replace(query=query))

    def proxy_request_headers(self):
        headers = super().proxy_request_headers()
        headers["Accept-Encoding"] = "identity"
        # Jupyter credentials terminate here. T3 keeps its own paired session.
        headers.pop("Authorization", None)
        headers.pop("X-XSRFToken", None)
        headers.pop("Referer", None)
        headers.pop("If-None-Match", None)
        headers.pop("If-Modified-Since", None)
        headers["Cookie"] = session_cookie_header(headers)
        return headers

    def _rewrite(self, response, path):
        if response.code == 200:
            response.body = rewrite_client(
                response.body, response.headers.get("Content-Type", ""), path, self.prefix
            )
        # Rewritten bundles must not use upstream's immutable cache validators.
        response.headers["Cache-Control"] = "no-store"
        for header in ("ETag", "Last-Modified", "Content-Length"):
            response.headers.pop(header, None)
        cookies = response.headers.get_list("Set-Cookie")
        if cookies:
            del response.headers["Set-Cookie"]
            for cookie in cookies:
                response.headers.add("Set-Cookie", scoped_cookie(cookie, self.prefix))

    async def _forward(self, path):
        service = self.t3_app._supervisor
        if service is None:
            raise web.HTTPError(503, reason="T3 Code is unavailable; check the Jupyter server log")
        if service.state is not ServiceState.READY:
            raise web.HTTPError(503, reason="T3 Code is not ready; reopen the launcher shortly")
        if path == "_adapter.js":
            _ = self.xsrf_token
            self.set_header("Content-Type", "application/javascript")
            self.set_header("Cache-Control", "no-store")
            adapter = Path(__file__).with_name("web_adapter.js").read_text()
            self.finish(adapter.replace("__T3_PREFIX__", json.dumps(self.prefix)))
            return
        await super().proxy(service.policy.readiness_host, service.policy.port, "/" + path)

    http_get = _forward
    post = _forward
    put = _forward
    patch = _forward
    delete = _forward
    head = _forward
    options = _forward

    async def open(self, path):
        service = self.t3_app._supervisor
        if service is None or service.state is not ServiceState.READY:
            self.close(code=1013, reason="T3 Code is not ready")
            return
        try:
            await self.proxy_open(service.policy.readiness_host, service.policy.port, "/" + path)
        except httpclient.HTTPClientError as error:
            if error.code not in {401, 403}:
                raise
            self.close(code=1008, reason="Pair with T3 Code before connecting")
