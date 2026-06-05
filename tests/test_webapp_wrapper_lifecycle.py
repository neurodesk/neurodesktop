import importlib.util
import os
import socket
import threading
from types import SimpleNamespace
from pathlib import Path


def _load_webapp_wrapper_module():
    repo_module_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "jupyter"
        / "webapp_wrapper"
        / "webapp_wrapper.py"
    )
    installed_module_path = (
        Path("/opt/neurodesktop") / "webapp_wrapper" / "webapp_wrapper.py"
    )
    module_path = (
        repo_module_path
        if repo_module_path.exists()
        else installed_module_path
    )
    spec = importlib.util.spec_from_file_location("webapp_wrapper", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    httpd = wrapper.UnixSocketHTTPServer(str(socket_path), wrapper.WebappHandler)
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
