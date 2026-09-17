"""Jupyter lifecycle adapter for the T3 Code supervisor."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import os

from jupyter_server.extension.application import ExtensionApp

from .web import T3ProxyHandler, T3SessionHandler, T3StatusHandler

from .supervisor import ConfigError, T3Supervisor, policy_from_environment


class NeurodeskT3CodeApp(ExtensionApp):
    """Start the sidecar automatically after Jupyter's event loop is running."""

    name = "neurodesk_t3_code"
    load_other_extensions = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._supervisor: T3Supervisor | None = None
        self._readiness_task: asyncio.Task[None] | None = None

    def initialize_handlers(self) -> None:
        self.handlers.extend([
            (r"/neurodesk-t3-status", T3StatusHandler, {"t3_app": self}),
            (r"/neurodesk-t3/_session", T3SessionHandler, {"t3_app": self}),
            (r"/neurodesk-t3/(.*)", T3ProxyHandler, {"t3_app": self}),
        ])

    async def _start_jupyter_server_extension(self, _serverapp) -> None:
        try:
            policy = policy_from_environment(os.environ)
        except ConfigError as error:
            self.log.warning("T3 Code sidecar was not started: %s", error)
            return

        self._supervisor = T3Supervisor(policy, logger=self.log)
        self._supervisor.start()
        self._readiness_task = asyncio.create_task(self._report_readiness())

    async def _report_readiness(self) -> None:
        assert self._supervisor is not None
        try:
            await self._supervisor.wait_ready()
        except (RuntimeError, asyncio.TimeoutError) as error:
            self.log.warning("T3 Code sidecar startup did not complete: %s", error)

    async def stop_extension(self) -> None:
        if self._readiness_task is not None:
            self._readiness_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._readiness_task
            self._readiness_task = None
        if self._supervisor is not None:
            await self._supervisor.close()
            self._supervisor = None
