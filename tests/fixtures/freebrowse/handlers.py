"""Tornado handlers for serving FreeBrowse static files."""

import os
from tornado.web import StaticFileHandler

HERE = os.path.dirname(__file__)
STATIC_DIR = os.path.join(HERE, "static", "freebrowse")


def setup_handlers(web_app):
    """Register the /freebrowse/ static file handler."""
    base_url = web_app.settings.get("base_url", "/")
    route_pattern = rf"{base_url}freebrowse/(.*)"

    web_app.add_handlers(
        r".*",
        [
            (
                route_pattern,
                StaticFileHandler,
                {"path": STATIC_DIR, "default_filename": "index.html"},
            )
        ],
    )
