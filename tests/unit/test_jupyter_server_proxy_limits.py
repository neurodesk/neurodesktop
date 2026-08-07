"""Jupyter Server Proxy response-size configuration contracts."""

import runpy
import sys
from types import ModuleType, SimpleNamespace

from tornado.httpclient import AsyncHTTPClient

from testlib import repo_path


def test_server_proxy_response_limit_is_one_gibibyte(monkeypatch):
    configure_calls = []

    monkeypatch.setattr(
        AsyncHTTPClient,
        "configure",
        staticmethod(
            lambda impl, **kwargs: configure_calls.append((impl, kwargs))
        ),
    )

    workspace = ModuleType("jupyter_ai_workspace")
    workspace.seed_agents_on_chat_save = lambda **_kwargs: None
    monkeypatch.setitem(sys.modules, "jupyter_ai_workspace", workspace)

    config = SimpleNamespace(FileContentsManager=SimpleNamespace())
    runpy.run_path(
        repo_path("config/jupyter/jupyter_server_config_extra.py"),
        init_globals={"c": config},
    )

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
