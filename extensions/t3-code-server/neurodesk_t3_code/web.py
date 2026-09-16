"""Authenticated, fixed-target proxy for the pinned T3 web client.

T3 0.0.40 has no base-path option. Adapt only responses under this route;
never modify the installed client or claim root-level Jupyter routes.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jupyter_server.base.handlers import APIHandler, JupyterHandler
from jupyter_server.utils import url_path_join
from jupyter_server_proxy.handlers import ProxyHandler
from tornado import httpclient, web

from .supervisor import ServiceState


ROUTE = "neurodesk-t3"
ROUTER_ANCHOR = "context:{},defaultPreload:`intent`"
PRELOAD_ANCHOR = "return`/`+e"
ASSET_URL_ANCHOR = "try{return new URL(t,e).toString()}catch{return null}"


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
        headers["Cookie"] = "; ".join(
            part.strip() for part in headers.get("Cookie", "").split(";")
            if part.strip().startswith("t3_session_")
        )
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
                response.headers.add("Set-Cookie", re.sub(
                    r"(?i)(;\s*path=)/(?=;|$)", lambda m: m[1] + self.prefix, cookie
                ))

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
