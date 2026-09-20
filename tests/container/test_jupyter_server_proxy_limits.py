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


def test_large_response_through_initialized_jupyter_proxy(tmp_path):
    """Transfer 101 MiB over both transports after Hub-style default reset."""
    import hashlib
    import os
    import signal
    import subprocess
    import sys
    import urllib.request

    from test_rise_slides_image import _unused_port, _wait_for_server

    backend = tmp_path / "backend.py"
    backend.write_text('''
import sys
from tornado.httpserver import HTTPServer
from tornado.ioloop import IOLoop
from tornado.netutil import bind_unix_socket
from tornado.web import Application, RequestHandler

class Payload(RequestHandler):
    async def get(self):
        if self.request.path != '/payload':
            self.finish('ready')
            return
        chunk = bytes(range(256)) * 4096
        self.set_header('Content-Type', 'application/octet-stream')
        self.set_header('Content-Length', len(chunk) * 101)
        for _ in range(101):
            self.write(chunk)
            await self.flush()

server = HTTPServer(Application([(r'/.*', Payload)]))
if sys.argv[1] == 'unix':
    server.add_socket(bind_unix_socket(sys.argv[2]))
else:
    server.listen(int(sys.argv[2]), address='127.0.0.1')
IOLoop.current().start()
''')
    # Reset after configuration loading, at the extension lifecycle boundary.
    (tmp_path / "audit_reset.py").write_text('''
from tornado.httpclient import AsyncHTTPClient
from tornado.web import RequestHandler

def _load_jupyter_server_extension(serverapp):
    AsyncHTTPClient.configure(AsyncHTTPClient.configured_class(), defaults={'validate_cert': True})
    class Limits(RequestHandler):
        def get(self):
            client = AsyncHTTPClient(force_instance=True)
            try:
                self.finish({'max_buffer_size': client.max_buffer_size})
            finally:
                client.close()
    serverapp.web_app.add_handlers('.*$', [(serverapp.base_url + 'audit-limits', Limits)])
''')
    config = tmp_path / "jupyter_server_config.py"
    config.write_text(
        f"import sys\nsys.path.insert(0, {str(tmp_path)!r})\n"
        "c.ServerApp.jpserver_extensions = {'jupyter_server_proxy': True, 'audit_reset': True, 'neurodesk_t3_code': False}\n"
        "c.ServerProxy.servers = " + repr({
            "large-tcp": {"command": [sys.executable, str(backend), "tcp", "{port}"], "timeout": 30},
            "large-unix": {"command": [sys.executable, str(backend), "unix", "{unix_socket}"],
                           "unix_socket": True, "timeout": 30},
        }) + "\n"
    )
    port = _unused_port()
    token = "large-response-test"
    prefix = f"http://127.0.0.1:{port}/user/proxy-test/"
    log_path = tmp_path / "jupyter.log"
    with log_path.open("w") as log:
        server = subprocess.Popen([
            sys.executable, "-m", "jupyter", "server", "--no-browser", "--ServerApp.allow_root=True",
            f"--config={config}", f"--ServerApp.port={port}", "--ServerApp.port_retries=0",
            "--ServerApp.base_url=/user/proxy-test/", f"--ServerApp.root_dir={tmp_path}",
            f"--IdentityProvider.token={token}",
        ], env={**os.environ, "HOME": str(tmp_path)}, cwd=tmp_path,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            _wait_for_server(prefix + "api/status?token=" + token, server, log_path)
            import json
            with urllib.request.urlopen(prefix + "audit-limits", timeout=10) as response:
                assert json.load(response)["max_buffer_size"] == 100 * 1024 * 1024
            chunk = bytes(range(256)) * 4096
            expected = hashlib.sha256()
            for _ in range(101):
                expected.update(chunk)
            for transport in ("tcp", "unix"):
                request = urllib.request.Request(
                    prefix + f"large-{transport}/payload",
                    headers={"Authorization": f"token {token}"},
                )
                digest = hashlib.sha256()
                received = 0
                with urllib.request.urlopen(request, timeout=90) as response:
                    assert response.status == 200
                    while block := response.read(1024 * 1024):
                        received += len(block)
                        digest.update(block)
                assert received == 101 * 1024 * 1024, transport
                assert digest.digest() == expected.digest(), transport
        finally:
            try:
                os.killpg(server.pid, signal.SIGTERM)
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait(timeout=10)
            except ProcessLookupError:
                server.wait(timeout=10)
