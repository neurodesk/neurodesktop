#!/usr/bin/env python3
"""
Tiny redirect server for hosted webapp launcher entries.

The launcher extension opens direct URLs itself. This server is a fallback for
manual visits to the proxied path or older frontends that still follow
path_info.
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class RedirectHandler(BaseHTTPRequestHandler):
    target_url = ""

    def do_GET(self):
        self._redirect()

    def do_HEAD(self):
        self._redirect()

    def _redirect(self):
        self.send_response(302)
        self.send_header("Location", self.target_url)
        self.end_headers()

    def log_message(self, format, *args):
        return


def parse_args():
    parser = argparse.ArgumentParser(description="Redirect to a hosted webapp")
    parser.add_argument("--url", required=True)
    parser.add_argument("--port", type=int, required=True)
    return parser.parse_args()


def validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit(f"Invalid redirect URL: {url}")


def main():
    args = parse_args()
    validate_url(args.url)

    RedirectHandler.target_url = args.url
    server = ThreadingHTTPServer(("127.0.0.1", args.port), RedirectHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
