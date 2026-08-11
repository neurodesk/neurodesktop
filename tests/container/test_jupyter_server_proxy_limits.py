"""Runtime contract for Jupyter Server Proxy's bounded HTTP clients."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from jupyter_server_proxy.handlers import ProxyHandler
from jupyter_server_proxy.unixsock import UnixResolver
from tornado.httpclient import AsyncHTTPClient


def test_installed_proxy_clients_remain_bounded_after_jupyterhub_reset():
    import jupyter_server_proxy

    handlers_path = Path(jupyter_server_proxy.__file__).parent / "handlers.py"
    handlers = handlers_path.read_text(encoding="utf-8")
    assert "neurodesktop-bounded-unix-http-client" in handlers
    assert "neurodesktop-bounded-tcp-http-client" in handlers
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
    AsyncHTTPClient.configure(
        AsyncHTTPClient.configured_class(),
        defaults={"validate_cert": True},
    )

    async def exercise_proxy_branch(unix_socket):
        captured = {}

        async def capture_buffered(_host, _port, _path, _body, client):
            captured["client"] = client

        handler = SimpleNamespace(
            unix_socket=unix_socket,
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
            if unix_socket is not None:
                assert isinstance(client.resolver, UnixResolver)
                assert client.resolver.socket_path == unix_socket
        finally:
            client.close()

    try:
        asyncio.run(exercise_proxy_branch(None))
        asyncio.run(exercise_proxy_branch("/tmp/ezbids.sock"))
    finally:
        AsyncHTTPClient._restore_configuration(saved_configuration)
