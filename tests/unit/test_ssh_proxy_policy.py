"""Exercise SSH entrypoint discovery through the shipped Jupyter config."""

import importlib.metadata
import runpy
import sys
from types import ModuleType, SimpleNamespace

import pytest
from tornado.httpclient import AsyncHTTPClient

from testlib import repo_path


@pytest.mark.parametrize("disable_ssh", [None, "false", "true"])
def test_jupyter_filters_only_the_ssh_entrypoint_before_loading(
    monkeypatch, disable_ssh
):
    points = importlib.metadata.EntryPoints(
        [
            importlib.metadata.EntryPoint(
                name=name, value="unused:setup", group="jupyter_serverproxy_servers"
            )
            for name in ("sshd", "custom-desktop", "custom-webapp")
        ]
    )
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda **kwargs: points.select(**kwargs)
    )
    package = ModuleType("jupyter_server_proxy")
    proxy_config = ModuleType("jupyter_server_proxy.config")
    proxy_config.entry_points = importlib.metadata.entry_points
    package.config = proxy_config
    monkeypatch.setitem(sys.modules, "jupyter_server_proxy", package)
    monkeypatch.setitem(sys.modules, "jupyter_server_proxy.config", proxy_config)
    workspace = ModuleType("jupyter_ai_workspace")
    workspace.seed_agents_on_chat_save = lambda **kwargs: None
    monkeypatch.setitem(sys.modules, "jupyter_ai_workspace", workspace)
    monkeypatch.delenv("NEURODESKTOP_DISABLE_SSH", raising=False)
    if disable_ssh is not None:
        monkeypatch.setenv("NEURODESKTOP_DISABLE_SSH", disable_ssh)
    config = SimpleNamespace(FileContentsManager=SimpleNamespace())

    saved_configuration = AsyncHTTPClient._save_configuration()
    try:
        for _ in range(2):
            runpy.run_path(
                repo_path("config/jupyter/jupyter_server_config_extra.py"),
                init_globals={"c": config},
            )
            discovered = proxy_config.entry_points(group="jupyter_serverproxy_servers")
            expected = ["custom-desktop", "custom-webapp"]
            if disable_ssh != "true":
                expected.insert(0, "sshd")
            assert [point.name for point in discovered] == expected
    finally:
        AsyncHTTPClient._restore_configuration(saved_configuration)
