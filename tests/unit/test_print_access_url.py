"""Tests for print_access_url.sh, the end-of-startup-log access link.

The Jupyter ServerApp prints its token URL early in the container log, where
it scrolls out of view behind extension and deferred-startup output.
print_access_url.sh runs in the background, waits until the server answers
HTTP, and reprints the access link (read from the server's own
jpserver-<pid>.json runtime info file) as the last thing in the startup log.
"""

import http.server
import json
import os
import threading

from testlib import resolve_source, run_cmd

PRINT_ACCESS_URL = resolve_source(
    "/opt/neurodesktop/print_access_url.sh", "config/jupyter/print_access_url.sh"
)
BEFORE_NOTEBOOK = resolve_source(
    "/usr/local/bin/before-notebook.d/before_notebook.sh",
    "config/jupyter/before_notebook.sh",
)


class _SilentHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def _serve_on_free_port():
    server = http.server.HTTPServer(("127.0.0.1", 0), _SilentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def _write_server_info(runtime_dir, port, token="", base_url="/", pid=101):
    runtime_dir.mkdir(parents=True, exist_ok=True)
    info = {
        "port": port,
        "base_url": base_url,
        "token": token,
        "url": f"http://127.0.0.1:{port}{base_url}",
    }
    (runtime_dir / f"jpserver-{pid}.json").write_text(json.dumps(info))


def _run_watcher(runtime_dir, env=None, timeout=60):
    merged = {
        "JUPYTER_RUNTIME_DIR": str(runtime_dir),
        "NEURODESKTOP_ACCESS_URL_SETTLE": "0",
        "NEURODESKTOP_ACCESS_URL_MAX_WAIT": "20",
    }
    if env:
        merged.update(env)
    return run_cmd(f"bash {PRINT_ACCESS_URL}", env=merged, timeout=timeout)


def test_prints_token_url_once_server_answers(tmp_path):
    server, port = _serve_on_free_port()
    try:
        runtime_dir = tmp_path / "runtime"
        _write_server_info(runtime_dir, port, token="secret123")

        code, output = _run_watcher(runtime_dir)
        assert code == 0, output
        assert f"http://localhost:{port}/lab?token=secret123" in output
        assert "Neurodesktop is ready" in output
    finally:
        server.shutdown()


def test_prints_plain_url_without_token_and_honours_base_url(tmp_path):
    server, port = _serve_on_free_port()
    try:
        runtime_dir = tmp_path / "runtime"
        _write_server_info(runtime_dir, port, token="", base_url="/user/jovyan/")

        code, output = _run_watcher(runtime_dir)
        assert code == 0, output
        assert f"http://localhost:{port}/user/jovyan/lab" in output
        assert "token=" not in output
    finally:
        server.shutdown()


def test_uses_newest_server_info_file(tmp_path):
    server, port = _serve_on_free_port()
    try:
        runtime_dir = tmp_path / "runtime"
        _write_server_info(runtime_dir, port, token="staletoken", pid=100)
        stale = runtime_dir / "jpserver-100.json"
        # Backdate the stale file so the fresh one wins the mtime ordering.
        old = os.stat(stale).st_mtime - 3600
        os.utime(stale, (old, old))
        _write_server_info(runtime_dir, port, token="freshtoken", pid=200)

        code, output = _run_watcher(runtime_dir)
        assert code == 0, output
        assert "token=freshtoken" in output
        assert "staletoken" not in output
    finally:
        server.shutdown()


def test_disabled_via_env_prints_nothing(tmp_path):
    code, output = _run_watcher(
        tmp_path / "runtime", env={"NEURODESKTOP_PRINT_ACCESS_URL": "0"}
    )
    assert code == 0
    assert output == ""


def test_times_out_quietly_when_server_never_answers(tmp_path):
    runtime_dir = tmp_path / "runtime"
    # Port 9 (discard) on localhost is closed; curl fails to connect.
    _write_server_info(runtime_dir, 9)

    code, output = _run_watcher(
        runtime_dir, env={"NEURODESKTOP_ACCESS_URL_MAX_WAIT": "2"}
    )
    assert code == 0, output
    assert "no access link printed" in output
    assert "Neurodesktop is ready" not in output


def test_before_notebook_launches_watcher_in_background():
    content = BEFORE_NOTEBOOK.read_text()
    assert "/opt/neurodesktop/print_access_url.sh &" in content
