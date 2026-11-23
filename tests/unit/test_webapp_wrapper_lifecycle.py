import io
import json
import os
from pathlib import Path
import socket
import stat
import threading
from types import SimpleNamespace

import httpx
import pytest

from testlib import load_source_module


def _load_webapp_wrapper_module():
    return load_source_module(
        "webapp_wrapper",
        "/opt/neurodesktop/webapp_wrapper/webapp_wrapper.py",
        "config/jupyter/webapp_wrapper/webapp_wrapper.py",
    )


class DummyStatusHandler:
    def __init__(self):
        self.responses = []
        self.headers = []
        self.ended = False

    def send_response(self, code):
        self.responses.append(code)

    def send_header(self, name, value):
        self.headers.append((name, value))

    def end_headers(self):
        self.ended = True


def test_wrapper_socket_is_private_to_notebook_user(tmp_path, monkeypatch):
    wrapper = _load_webapp_wrapper_module()
    socket_path = tmp_path / "webapp.sock"
    mode_when_bound = []

    class BoundServer:
        def __init__(self, path, _handler):
            Path(path).touch(mode=0o666)
            mode_when_bound.append(stat.S_IMODE(Path(path).stat().st_mode))

        def server_close(self):
            pass

    monkeypatch.setattr(wrapper, "UnixSocketHTTPServer", BoundServer)

    httpd = wrapper.create_http_server(str(socket_path))
    try:
        assert mode_when_bound == [0o600]
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
    finally:
        httpd.server_close()
        socket_path.unlink(missing_ok=True)


def test_wrapper_removes_socket_when_private_mode_cannot_be_set(tmp_path, monkeypatch):
    wrapper = _load_webapp_wrapper_module()
    socket_path = tmp_path / "webapp.sock"
    closed = []

    class BoundServer:
        def __init__(self, path, _handler):
            Path(path).touch(mode=0o666)

        def server_close(self):
            closed.append(True)

    monkeypatch.setattr(wrapper, "UnixSocketHTTPServer", BoundServer)
    monkeypatch.setattr(
        wrapper.os,
        "chmod",
        lambda _path, _mode: (_ for _ in ()).throw(PermissionError("denied")),
    )

    with pytest.raises(PermissionError, match="denied"):
        wrapper.create_http_server(str(socket_path))

    assert closed == [True]
    assert not socket_path.exists()


def test_close_beacon_does_not_schedule_immediate_backend_stop(monkeypatch):
    wrapper = _load_webapp_wrapper_module()
    scheduled_close_checks = []
    direct_stops = []

    monkeypatch.setattr(wrapper, "log", lambda _message: None)
    monkeypatch.setattr(
        wrapper,
        "stop_backend_for_idle",
        lambda idle_for: direct_stops.append(idle_for),
    )
    monkeypatch.setattr(
        wrapper,
        "_schedule_close_check",
        lambda: scheduled_close_checks.append("scheduled"),
        raising=False,
    )

    handler = DummyStatusHandler()
    wrapper.WebappHandler._send_status(handler, "POST", is_close=True)

    assert handler.responses == [204]
    assert ("Cache-Control", "no-cache") in handler.headers
    assert handler.ended
    assert scheduled_close_checks == []
    assert direct_stops == []


def test_location_rewrites_keep_relative_redirects_under_app_path():
    wrapper = _load_webapp_wrapper_module()
    wrapper.config = SimpleNamespace(app_name="jamovi")
    handler = object.__new__(wrapper.WebappHandler)
    handler.path = "/user/alice/jamovi"

    assert (
        wrapper.WebappHandler._rewrite_location(handler, "session-id/", 42037)
        == "/user/alice/jamovi/session-id/"
    )
    assert (
        wrapper.WebappHandler._rewrite_location(handler, "/assets/main.js", 42037)
        == "/user/alice/jamovi/assets/main.js"
    )
    assert (
        wrapper.WebappHandler._rewrite_location(
            handler,
            "http://localhost:42037/session-id/",
            42037,
        )
        == "/user/alice/jamovi/session-id/"
    )
    assert (
        wrapper.WebappHandler._rewrite_location(
            handler,
            "https://example.org/external/",
            42037,
        )
        == "https://example.org/external/"
    )


def test_path_rewrite_map_supports_explicit_base_path_targets():
    wrapper = _load_webapp_wrapper_module()

    rewrite_map = wrapper.build_path_rewrite_map(
        [
            {"from": "/assets/", "to": "${base_path}assets/"},
            {"from": "\"/version\"", "to": "\"${base_path}version\""},
            {"from": "\"/settings\"", "to": "\"${base_path}settings\""},
            "/jamovi/",
        ],
        "/user/alice/jamovi/",
    )

    assert (b"/assets/", b"/user/alice/jamovi/assets/") in rewrite_map
    assert (b'"/version"', b'"/user/alice/jamovi/version"') in rewrite_map
    assert (b'"/settings"', b'"/user/alice/jamovi/settings"') in rewrite_map
    assert (b"/jamovi/", b"/user/alice/jamovi/") in rewrite_map

    html = wrapper.apply_path_rewrites(
        (
            b'<script src="/assets/main.js"></script>'
            b'<script>fetch("/version");fetch("/settings");</script>'
        ),
        rewrite_map,
    )
    assert html == (
        b'<script src="/user/alice/jamovi/assets/main.js"></script>'
        b'<script>fetch("/user/alice/jamovi/version");'
        b'fetch("/user/alice/jamovi/settings");</script>'
    )


def test_jamovi_config_roots_use_browser_facing_base_path():
    wrapper = _load_webapp_wrapper_module()
    wrapper.config = SimpleNamespace(app_name="jamovi")

    handler = object.__new__(wrapper.WebappHandler)
    handler.path = "/user/alice/jamovi/config.js"
    handler.headers = {"Host": "hub.example.test"}

    assert wrapper.WebappHandler._build_jamovi_config_js(handler) == (
        b'window.config = {"client":{"roots":['
        b'"hub.example.test/user/alice/jamovi",'
        b'"hub.example.test/user/alice/jamovi/analyses",'
        b'"hub.example.test/user/alice/jamovi/results"]}}'
    )


def test_websocket_upgrade_requests_are_tunneled_to_backend(tmp_path):
    wrapper = _load_webapp_wrapper_module()
    backend_ready = threading.Event()
    backend_done = threading.Event()
    received = {}

    backend_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    backend_socket.bind(("127.0.0.1", 0))
    backend_socket.listen(1)
    backend_port = backend_socket.getsockname()[1]

    def backend():
        backend_ready.set()
        conn, _addr = backend_socket.accept()
        with conn:
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            received["request"] = request
            conn.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"\r\n"
            )
            received["payload"] = conn.recv(len(b"client-data"))
            conn.sendall(b"server-data")
        backend_done.set()

    backend_thread = threading.Thread(target=backend, daemon=True)
    backend_thread.start()
    assert backend_ready.wait(2)

    wrapper.config = SimpleNamespace(
        app_name="jamovi",
        target_port=backend_port,
        default_port=backend_port,
        routes=[("/jamovi", backend_port)],
        path_rewrites=[],
        status_endpoint="jamovi-wrapper-status",
        logfile=str(tmp_path / "jamovi_wrapper.log"),
    )
    wrapper.container_ready = True

    socket_path = Path("/tmp") / f"ndwrap-{os.getpid()}-{backend_port}.sock"
    socket_path.unlink(missing_ok=True)
    httpd = wrapper.create_http_server(str(socket_path))
    assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    client.connect(str(socket_path))
    try:
        client.sendall(
            b"GET /user/alice/jamovi/abc123/coms HTTP/1.1\r\n"
            b"Host: hub.example.test\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGVzdA==\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"\r\n"
        )

        response = b""
        while b"\r\n\r\n" not in response:
            response += client.recv(4096)

        assert b"101 Switching Protocols" in response

        client.sendall(b"client-data")
        assert client.recv(len(b"server-data")) == b"server-data"
    finally:
        client.close()
        httpd.shutdown()
        httpd.server_close()
        socket_path.unlink(missing_ok=True)
        backend_socket.close()
        server_thread.join(2)

    assert backend_done.wait(2)
    assert received["payload"] == b"client-data"
    assert received["request"].startswith(b"GET /abc123/coms HTTP/1.1\r\n")
    assert f"Host: localhost:{backend_port}\r\n".encode() in received["request"]


def test_local_sif_start_container_splits_multiword_startup_command(tmp_path, monkeypatch):
    wrapper = _load_webapp_wrapper_module()
    expected_sif = "/opt/neurodesktop-test-webapps/neurovue/neurovue.sif"
    launched = {}

    class FakeProcess:
        pid = 43210

        def __init__(self):
            self.stdout = io.StringIO("")

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        launched["cmd"] = cmd
        launched["env"] = kwargs["env"]
        return FakeProcess()

    wrapper.config = SimpleNamespace(
        app_name="neurovue",
        title="NeuroVue",
        module="neurovue",
        version=None,
        startup_command='neurovue start --title "My Viewer"',
        target_port=3000,
        default_port=3000,
        routes=[("/neurovue", 3000)],
        startup_timeout=1,
        logfile=str(tmp_path / "neurovue_wrapper.log"),
    )
    wrapper.container_ready = False
    wrapper.container_error = None

    monkeypatch.setattr(wrapper, "find_free_port", lambda: 34567)
    monkeypatch.setattr(wrapper.os.path, "exists", lambda path: path == expected_sif)
    monkeypatch.setattr(wrapper.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(wrapper.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(wrapper, "check_app_ready", lambda: True)

    wrapper.start_container()

    assert wrapper.container_ready is True
    assert wrapper.container_error is None
    assert launched["cmd"] == [
        "apptainer", "exec", "--writable-tmpfs",
        "-B", "/neurodesktop-storage:/neurodesktop-storage",
        "-B", "/home/jovyan:/home/jovyan",
        expected_sif,
        "neurovue", "start", "--title", "My Viewer",
    ]
    assert launched["env"]["NEURODESK_WEBAPP_PORT"] == "34567"
    assert launched["env"]["APPTAINERENV_NEURODESK_WEBAPP_PORT"] == "34567"


def test_build_local_sif_command_rejects_empty_startup_command():
    wrapper = _load_webapp_wrapper_module()

    with pytest.raises(ValueError, match="must not be empty"):
        wrapper.build_local_sif_command("/opt/test/app.sif", "   ")


def test_webapp_config_loads_rewrite_location_and_rejects_non_boolean(tmp_path, monkeypatch):
    wrapper = _load_webapp_wrapper_module()
    config_file = tmp_path / "webapps.json"
    config_file.write_text(
        json.dumps(
            {
                "webapps": {
                    "defaultapp": {"startup_command": "defaultapp start"},
                    "neurovue": {
                        "startup_command": "neurovue start",
                        "rewrite_location": False,
                    },
                    "badapp": {
                        "startup_command": "badapp start",
                        "rewrite_location": "false",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(wrapper, "CONFIG_PATH", config_file)

    assert wrapper.WebappConfig("defaultapp").rewrite_location is True
    assert wrapper.WebappConfig("neurovue").rewrite_location is False

    with pytest.raises(ValueError, match="rewrite_location for badapp must be a boolean"):
        wrapper.WebappConfig("badapp")


def _decode_chunked_body(raw_body):
    chunks = []
    pos = 0
    while pos < len(raw_body):
        line_end = raw_body.index(b"\r\n", pos)
        size = int(raw_body[pos:line_end], 16)
        pos = line_end + 2
        if size == 0:
            break
        chunks.append(raw_body[pos:pos + size])
        pos += size + 2
    return b"".join(chunks)


@pytest.mark.parametrize("rewrite_location", [True, False])
def test_uncompressed_main_html_respects_rewrite_location_opt_out(rewrite_location):
    wrapper = _load_webapp_wrapper_module()
    wrapper.config = SimpleNamespace(
        app_name="neurovue",
        path_rewrites=["/neurovue/"],
        status_endpoint="neurovue-wrapper-status",
        heartbeat_interval=60,
        rewrite_location=rewrite_location,
    )

    class FakeTextResponse:
        status_code = 200
        headers = httpx.Headers({"Content-Type": "text/html; charset=utf-8"})

        def iter_raw(self, _chunk_size):
            yield b"<!doctype html><html><head><title>NeuroVue</title></head><body>ok</body></html>"

        def iter_bytes(self, _chunk_size):
            yield from self.iter_raw(_chunk_size)

    handler = object.__new__(wrapper.WebappHandler)
    handler.path = "/user/alice/neurovue/"
    handler.wfile = io.BytesIO()
    handler.responses = []
    handler.sent_headers = []
    handler.send_response = lambda code: handler.responses.append(code)
    handler.send_header = lambda name, value: handler.sent_headers.append((name, value))
    handler.end_headers = lambda: None

    wrapper.WebappHandler._send_streamed_text_response(
        handler, FakeTextResponse(), 3000, is_main_html=True
    )

    body = _decode_chunked_body(handler.wfile.getvalue()).decode("utf-8")
    assert handler.responses == [200]
    assert "neurovue-wrapper-status" in body
    assert "sendHeartbeat" in body
    if rewrite_location:
        assert '<base href="/user/alice/neurovue/">' in body
        assert "History.prototype.replaceState" in body
    else:
        assert "var statusUrl = '/user/alice/neurovue/neurovue-wrapper-status';" in body
        assert "<base href=" not in body
        assert "History.prototype.replaceState" not in body
