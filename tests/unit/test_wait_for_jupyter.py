import io
import json
import os
from urllib.error import HTTPError, URLError

import pytest

from testlib import load_source_module, resolve_source


@pytest.fixture
def readiness(tmp_path, monkeypatch):
    module = load_source_module("wait_for_jupyter", "/opt/neurodesktop/wait_for_jupyter.py", "config/jupyter/wait_for_jupyter.py")
    monkeypatch.setattr(module, "runtime_directories", lambda: [tmp_path])
    return module


class Clock:
    """Advance the helper's deadline only when the helper sleeps.

    A budget of a few milliseconds measured against the real clock can expire
    while the process is descheduled, before the first probe is even sent. The
    helper then reports its answer without asking the server, so any assertion
    on what it probed depends on machine load.
    """

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(readiness, monkeypatch):
    replacement = Clock()
    monkeypatch.setattr(readiness, "time", replacement)
    return replacement


def runtime_file(tmp_path, **values):
    path = tmp_path / f"jpserver-{os.getpid()}.json"
    path.write_text(json.dumps({"port": 9999, "base_url": "/user/alice/", "token": "secret", **values}))
    return path


def test_runtime_endpoint_uses_custom_port_and_base_without_credentials(readiness, tmp_path):
    runtime_file(tmp_path, url="http://remote.example:9999/", secure=True)
    assert list(readiness.server_urls([tmp_path])) == ["https://127.0.0.1:9999/user/alice/"]


def test_ignores_mcp_partial_and_dead_process_records(readiness, tmp_path, monkeypatch):
    (tmp_path / "jpserver-mcp-123.json").write_text('{"port": 7777}')
    path = runtime_file(tmp_path)
    path.write_text('{')
    assert list(readiness.server_urls([tmp_path])) == []
    runtime_file(tmp_path)
    def dead(*args):
        raise ProcessLookupError
    monkeypatch.setattr(readiness.os, "kill", dead)
    assert list(readiness.server_urls([tmp_path])) == []


@pytest.mark.parametrize("status,expected", [(200, True), (302, True), (403, True), (503, False)])
def test_wait_checks_http_readiness(readiness, clock, tmp_path, monkeypatch, status, expected):
    runtime_file(tmp_path)
    calls = []
    class Opener:
        def open(self, url, timeout):
            calls.append(url)
            if status >= 300:
                raise HTTPError(url, status, "test", {}, io.BytesIO())
            response = io.BytesIO()
            response.status = status
            return response
    monkeypatch.setattr(readiness.request, "build_opener", lambda *args: Opener())
    assert readiness.wait_for_jupyter(timeout=0.01) is expected
    assert calls == ["http://127.0.0.1:9999/user/alice/"]


def test_timeout_is_bounded_when_server_never_answers(readiness, clock, tmp_path, monkeypatch):
    runtime_file(tmp_path)
    attempts = []
    class Opener:
        def open(self, url, timeout):
            attempts.append(timeout)
            raise URLError("not listening")
    monkeypatch.setattr(readiness.request, "build_opener", lambda *args: Opener())
    assert not readiness.wait_for_jupyter(timeout=0.01)
    # An empty list would mean the budget expired before the helper asked.
    assert attempts == [0.01]


def test_image_installs_readiness_helper():
    dockerfile = resolve_source("/Dockerfile", "Dockerfile").read_text()
    assert "install -m 0644 /tmp/jupyter/wait_for_jupyter.py /opt/neurodesktop/wait_for_jupyter.py" in dockerfile


def test_actual_http_server_on_custom_port_and_base(readiness, tmp_path):
    import http.server
    import threading

    seen = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "http://must-not-follow.invalid/login")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        runtime_file(tmp_path, port=server.server_port)
        assert readiness.wait_for_jupyter(timeout=2)
        assert seen == [("/user/alice/", None)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
