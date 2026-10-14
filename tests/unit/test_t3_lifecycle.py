"""Drive the T3 Jupyter lifecycle adapter with fake process owners."""

from __future__ import annotations

import asyncio
import logging
import sys

import pytest

from testlib import repo_path


sys.path.insert(0, str(repo_path("extensions/t3-code-server")))


class FakeSupervisor:
    def __init__(self, policy, *, logger):
        self.policy = policy
        self.logger = logger
        self.events = []
        self.ready = asyncio.Event()
        self.failure = None

    def start(self):
        self.events.append("start")

    async def wait_ready(self):
        await self.ready.wait()
        if self.failure is not None:
            raise self.failure

    async def close(self):
        self.events.append("close")


class FakeConnect:
    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.events = []

    async def restore(self):
        self.events.append("restore")

    async def close(self):
        self.events.append("close")


@pytest.fixture
def lifecycle(monkeypatch):
    from neurodesk_t3_code import serverext

    created = {}

    def supervisor_factory(policy, *, logger):
        created["supervisor"] = FakeSupervisor(policy, logger=logger)
        return created["supervisor"]

    def connect_factory(supervisor):
        created["connect"] = FakeConnect(supervisor)
        return created["connect"]

    monkeypatch.setattr(serverext, "policy_from_environment", lambda environ: "policy")
    monkeypatch.setattr(serverext, "T3Supervisor", supervisor_factory)
    monkeypatch.setattr(serverext, "ConnectManager", connect_factory)
    return serverext, created


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


def test_connect_restores_after_readiness_and_shutdown_closes_both(lifecycle):
    serverext, created = lifecycle

    async def scenario():
        app = serverext.NeurodeskT3CodeApp()
        await app._start_jupyter_server_extension(object())
        supervisor, connect = created["supervisor"], created["connect"]
        assert supervisor.policy == "policy"
        assert connect.supervisor is supervisor
        assert supervisor.events == ["start"]
        await _settle()
        assert connect.events == []

        supervisor.ready.set()
        await app._readiness_task
        assert connect.events == ["restore"]

        await app.stop_extension()
        assert connect.events == ["restore", "close"]
        assert supervisor.events == ["start", "close"]
        assert app._readiness_task is None
        assert app._connect is None
        assert app._supervisor is None

    asyncio.run(scenario())


def test_shutdown_before_readiness_cancels_restore(lifecycle):
    serverext, created = lifecycle

    async def scenario():
        app = serverext.NeurodeskT3CodeApp()
        await app._start_jupyter_server_extension(object())
        await _settle()
        task = app._readiness_task
        await app.stop_extension()
        assert task.cancelled()
        assert created["connect"].events == ["close"]
        assert created["supervisor"].events == ["start", "close"]

    asyncio.run(scenario())


def test_failed_startup_is_reported_without_restoring_connect(lifecycle, caplog):
    serverext, created = lifecycle

    async def scenario():
        app = serverext.NeurodeskT3CodeApp()
        app.log = logging.getLogger("t3-lifecycle-test")
        await app._start_jupyter_server_extension(object())
        created["supervisor"].failure = RuntimeError("T3 Code did not start: parked")
        created["supervisor"].ready.set()
        await app._readiness_task
        assert created["connect"].events == []

    with caplog.at_level(logging.WARNING, logger="t3-lifecycle-test"):
        asyncio.run(scenario())
    assert "startup did not complete: T3 Code did not start: parked" in caplog.text


def test_unsafe_configuration_starts_nothing(lifecycle, monkeypatch, caplog):
    serverext, created = lifecycle

    def reject(environ):
        raise serverext.ConfigError("HOME must be owned by uid 1000")

    monkeypatch.setattr(serverext, "policy_from_environment", reject)

    async def scenario():
        app = serverext.NeurodeskT3CodeApp()
        app.log = logging.getLogger("t3-lifecycle-test")
        await app._start_jupyter_server_extension(object())
        assert app._readiness_task is None
        await app.stop_extension()

    with caplog.at_level(logging.WARNING, logger="t3-lifecycle-test"):
        asyncio.run(scenario())
    assert created == {}
    assert "was not started: HOME must be owned by uid 1000" in caplog.text
