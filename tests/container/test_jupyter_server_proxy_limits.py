"""Runtime contract for Jupyter Server Proxy's Unix-socket HTTP client."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from jupyter_server_proxy.handlers import ProxyHandler
from jupyter_server_proxy.unixsock import UnixResolver
from tornado.httpclient import AsyncHTTPClient


def test_installed_unix_socket_path_uses_configured_factory():
    import jupyter_server_proxy

    handlers_path = Path(jupyter_server_proxy.__file__).parent / "handlers.py"
    handlers = handlers_path.read_text(encoding="utf-8")
    assert "neurodesktop-configured-unix-http-client" in handlers
    assert (
        "from tornado.simple_httpclient import SimpleAsyncHTTPClient"
        not in handlers
    )

    saved_configuration = AsyncHTTPClient._save_configuration()
    one_gibibyte = 1024 * 1024 * 1024
    AsyncHTTPClient.configure(
        None,
        max_buffer_size=one_gibibyte,
        max_body_size=one_gibibyte,
    )

    async def exercise_proxy_branch():
        captured = {}

        async def capture_buffered(_host, _port, _path, _body, client):
            captured["client"] = client

        handler = SimpleNamespace(
            unix_socket="/tmp/ezbids.sock",
            request=SimpleNamespace(headers={}, body=None, method="GET"),
            log=SimpleNamespace(debug=lambda *_args: None),
            _check_host_allowlist=lambda _host: True,
            _record_activity=lambda: None,
            _proxy_buffered=capture_buffered,
        )
        await ProxyHandler.proxy(handler, "localhost", 0, "/download.zip")

        client = captured["client"]
        try:
            assert client.max_buffer_size == one_gibibyte
            assert client.max_body_size == one_gibibyte
            assert isinstance(client.resolver, UnixResolver)
            assert client.resolver.socket_path == "/tmp/ezbids.sock"
        finally:
            client.close()

    try:
        asyncio.run(exercise_proxy_branch())
    finally:
        AsyncHTTPClient._restore_configuration(saved_configuration)
