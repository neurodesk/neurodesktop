"""Jupyter Server Proxy response-size configuration contracts."""

import asyncio
import runpy
import sys
from types import ModuleType, SimpleNamespace

import pytest
from tornado.httpclient import AsyncHTTPClient

from testlib import load_source_module, repo_path


HANDLERS_SOURCE = '''from tornado import httpclient
from tornado.simple_httpclient import SimpleAsyncHTTPClient


class UnixResolver:
    def __init__(self, path):
        self.path = path


class ProxyHandler:
    def __init__(self, unix_socket):
        self.unix_socket = unix_socket

    def make_client(self):
        if self.unix_socket is not None:
            client = SimpleAsyncHTTPClient(
                force_instance=True, resolver=UnixResolver(self.unix_socket)
            )
        else:
            client = httpclient.AsyncHTTPClient(force_instance=True)
        return client
'''


def load_patcher_module():
    return load_source_module(
        "jupyter_server_proxy_patch",
        "/opt/neurodesktop/patch_jupyter_server_proxy.py",
        "config/jupyter/patch_jupyter_server_proxy.py",
    )


def write_upstream_fixture(package_dir):
    package_dir.mkdir()
    (package_dir / "handlers.py").write_text(HANDLERS_SOURCE, encoding="utf-8")


def install_server_config(monkeypatch):
    workspace = ModuleType("jupyter_ai_workspace")
    workspace.seed_agents_on_chat_save = lambda **_kwargs: None
    monkeypatch.setitem(sys.modules, "jupyter_ai_workspace", workspace)

    config = SimpleNamespace(FileContentsManager=SimpleNamespace())
    runpy.run_path(
        repo_path("config/jupyter/jupyter_server_config_extra.py"),
        init_globals={"c": config},
    )


def test_server_proxy_response_limit_is_one_gibibyte(monkeypatch):
    configure_calls = []

    monkeypatch.setattr(
        AsyncHTTPClient,
        "configure",
        staticmethod(
            lambda impl, **kwargs: configure_calls.append((impl, kwargs))
        ),
    )

    install_server_config(monkeypatch)

    one_gibibyte = 1024 * 1024 * 1024
    assert configure_calls == [
        (
            None,
            {
                "max_buffer_size": one_gibibyte,
                "max_body_size": one_gibibyte,
            },
        )
    ]


def test_proxy_clients_keep_bounded_limits_after_jupyterhub_reset(
    tmp_path, monkeypatch
):
    """Exercise both constructors after JupyterHub replaces factory defaults."""
    patcher = load_patcher_module()
    package_dir = tmp_path / "jupyter_server_proxy"
    write_upstream_fixture(package_dir)
    assert patcher.patch_package(package_dir)

    namespace = {}
    exec(
        (package_dir / "handlers.py").read_text(encoding="utf-8"),
        namespace,
    )

    saved_configuration = AsyncHTTPClient._save_configuration()
    try:
        install_server_config(monkeypatch)
        AsyncHTTPClient.configure(
            AsyncHTTPClient.configured_class(),
            defaults={"validate_cert": True},
        )

        async def inspect_client(unix_socket):
            handler = namespace["ProxyHandler"](unix_socket)
            client = handler.make_client()
            try:
                return (
                    client.max_buffer_size,
                    client.max_body_size,
                    getattr(client.resolver, "path", None),
                )
            finally:
                client.close()

        one_gibibyte = 1024 * 1024 * 1024
        assert asyncio.run(inspect_client(None))[:2] == (
            one_gibibyte,
            one_gibibyte,
        )
        assert asyncio.run(inspect_client("/tmp/ezbids.sock")) == (
            one_gibibyte,
            one_gibibyte,
            "/tmp/ezbids.sock",
        )
    finally:
        AsyncHTTPClient._restore_configuration(saved_configuration)


def test_proxy_patch_is_idempotent_and_bounds_both_clients(tmp_path):
    patcher = load_patcher_module()
    package_dir = tmp_path / "jupyter_server_proxy"
    write_upstream_fixture(package_dir)

    assert patcher.patch_package(package_dir)
    handlers = (package_dir / "handlers.py").read_text(encoding="utf-8")
    assert patcher.UNIX_MARKER in handlers
    assert patcher.TCP_MARKER in handlers
    assert (
        "from tornado.simple_httpclient import SimpleAsyncHTTPClient"
        not in handlers
    )
    assert handlers.count("client = httpclient.AsyncHTTPClient(") == 2
    assert handlers.count("max_buffer_size=1024 * 1024 * 1024") == 2
    assert handlers.count("max_body_size=1024 * 1024 * 1024") == 2
    compile(handlers, "handlers.py", "exec")

    assert not patcher.patch_package(package_dir)


def test_unix_socket_patch_refuses_anchor_drift(tmp_path):
    patcher = load_patcher_module()
    package_dir = tmp_path / "jupyter_server_proxy"
    write_upstream_fixture(package_dir)
    handlers_path = package_dir / "handlers.py"
    handlers_path.write_text(
        HANDLERS_SOURCE.replace(
            "client = SimpleAsyncHTTPClient(", "client = OtherClient("
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unix-socket client anchor"):
        patcher.patch_package(package_dir)


def test_proxy_patch_refuses_tcp_anchor_drift(tmp_path):
    patcher = load_patcher_module()
    package_dir = tmp_path / "jupyter_server_proxy"
    write_upstream_fixture(package_dir)
    handlers_path = package_dir / "handlers.py"
    handlers_path.write_text(
        HANDLERS_SOURCE.replace(
            "client = httpclient.AsyncHTTPClient(force_instance=True)",
            "client = httpclient.AsyncHTTPClient()",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="TCP client anchor"):
        patcher.patch_package(package_dir)


def test_dockerfile_pins_and_patches_jupyter_server_proxy():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    package_pin = dockerfile.index("jupyter-server-proxy==4.5.0")
    patch_install = dockerfile.index(
        "/opt/neurodesktop/patch_jupyter_server_proxy.py"
    )
    patch_run = dockerfile.index(
        "/opt/conda/bin/python /opt/neurodesktop/patch_jupyter_server_proxy.py"
    )
    assert package_pin < patch_install < patch_run
