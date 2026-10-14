"""Tornado handlers for serving FreeBrowse static files."""

import os
from tornado.web import StaticFileHandler, authenticated
from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.utils import url_path_join
import re


class FreeBrowseStaticHandler(JupyterHandler, StaticFileHandler):
    """Authenticated, packaged application assets, never user workspace files."""

    def check_xsrf_cookie(self):
        # Module scripts and crossorigin styles use CORS mode, even on the
        # same origin. HubOAuth otherwise rejects their session cookies since
        # declarative asset requests cannot supply an X-XSRFToken header.
        # Fetch Metadata is browser-controlled. Only same-origin, read-only
        # requests to this static directory qualify; authentication still runs.
        if (self.request.method in {"GET", "HEAD"}
                and self.request.headers.get("Sec-Fetch-Site") == "same-origin"):
            return
        return super().check_xsrf_cookie()

    @authenticated
    async def get(self, path, include_body=True):
        await super().get(path, include_body)

    @authenticated
    async def head(self, path):
        await self.get(path, include_body=False)

HERE = os.path.dirname(__file__)
STATIC_DIR = os.path.join(HERE, "static", "freebrowse")


def setup_handlers(web_app):
    """Register the /freebrowse/ static file handler."""
    base_url = web_app.settings.get("base_url", "/")
    route_pattern = re.escape(url_path_join(base_url, "freebrowse")) + r"/(.*)"

    web_app.add_handlers(
        r".*",
        [
            (
                route_pattern,
                FreeBrowseStaticHandler,
                {"path": STATIC_DIR, "default_filename": "index.html"},
            )
        ],
    )
