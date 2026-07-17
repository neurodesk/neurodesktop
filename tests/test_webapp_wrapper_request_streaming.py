import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

import pytest


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
        repo_module_path if repo_module_path.exists() else installed_module_path
    )
    spec = importlib.util.spec_from_file_location(
        "webapp_wrapper_streaming", module_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_handler(wrapper, body):
    handler = object.__new__(wrapper.WebappHandler)
    handler.rfile = io.BytesIO(body)
    return handler


def test_request_body_is_read_in_bounded_chunks_without_overreading():
    wrapper = _load_webapp_wrapper_module()
    payload = b"x" * (wrapper.STREAM_CHUNK_SIZE * 2 + 17)
    trailing_request_data = b"next-request"
    handler = _make_handler(wrapper, payload + trailing_request_data)

    chunks = list(handler._iter_request_body(len(payload)))

    assert b"".join(chunks) == payload
    assert [len(chunk) for chunk in chunks] == [
        wrapper.STREAM_CHUNK_SIZE,
        wrapper.STREAM_CHUNK_SIZE,
        17,
    ]
    assert handler.rfile.read() == trailing_request_data


def test_request_body_stream_rejects_a_truncated_client_body():
    wrapper = _load_webapp_wrapper_module()
    handler = _make_handler(wrapper, b"short")

    with pytest.raises(ConnectionError, match="5 bytes remaining"):
        list(handler._iter_request_body(10))


def test_proxy_passes_a_lazy_fixed_length_body_to_httpx(monkeypatch):
    wrapper = _load_webapp_wrapper_module()
    payload = b"upload" * wrapper.STREAM_CHUNK_SIZE
    handler = _make_handler(wrapper, payload)
    handler.path = "/user/alice/ezbids/api/upload"
    handler.headers = {
        "Host": "hub.example.test",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(payload)),
    }

    wrapper.config = SimpleNamespace(
        app_name="ezbids",
        target_port=8082,
        path_rewrites=[],
    )
    monkeypatch.setattr(
        wrapper.WebappHandler,
        "_resolve_proxy_target",
        lambda _self: ("/upload", "", 8082),
    )
    monkeypatch.setattr(
        wrapper.WebappHandler,
        "_is_upgrade_request",
        lambda _self: False,
    )
    monkeypatch.setattr(
        wrapper.WebappHandler,
        "_is_main_app_html",
        lambda _self: False,
    )
    monkeypatch.setattr(
        wrapper.WebappHandler,
        "_send_streamed_response",
        lambda _self, _response, _target_port: None,
    )

    class FakeCookies:
        def clear(self):
            pass

    class FakeResponse:
        status_code = 204
        headers = {}

    class ResponseContext:
        def __init__(self, content, client):
            self.content = content
            self.client = client

        def __enter__(self):
            self.client.chunks = list(self.content)
            return FakeResponse()

        def __exit__(self, _exc_type, _exc_value, _traceback):
            return False

    class FakeClient:
        def __init__(self):
            self.cookies = FakeCookies()
            self.chunks = []
            self.headers = []
            self.body_position_when_called = None

        def stream(self, _method, _url, headers, content):
            self.headers = headers
            self.body_position_when_called = handler.rfile.tell()
            assert not isinstance(content, (bytes, bytearray))
            return ResponseContext(content, self)

    fake_client = FakeClient()
    monkeypatch.setattr(wrapper, "_http_client", fake_client)

    handler._proxy_request("POST")

    assert fake_client.body_position_when_called == 0
    assert b"".join(fake_client.chunks) == payload
    assert all(
        len(chunk) <= wrapper.STREAM_CHUNK_SIZE for chunk in fake_client.chunks
    )
    assert ("Content-Length", str(len(payload))) in fake_client.headers
