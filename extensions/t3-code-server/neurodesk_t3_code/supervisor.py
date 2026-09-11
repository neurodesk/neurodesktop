"""Policy and process ownership for the optional T3 Code server.

This module has no Jupyter dependency. The thin server extension owns one
``T3Supervisor`` for the lifetime of Jupyter Server, while checkout tests drive
the same process boundary with a small fake server.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
import logging
import os
from pathlib import Path
import signal
import socket
from typing import Mapping


DEFAULT_EXECUTABLE = Path("/opt/t3-code/node_modules/.bin/t3")
DEFAULT_PROVIDER_BIN = Path("/opt/neurodesktop/t3-provider-bin")
DEFAULT_PORT = 3773
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


class ConfigError(ValueError):
    """An enabled deployment supplied an unsafe or unusable configuration."""


@dataclass(frozen=True)
class Disabled:
    reason: str


@dataclass(frozen=True)
class Enabled:
    home: Path
    base_dir: Path
    workdir: Path
    host: str
    port: int
    executable: Path
    provider_bin: Path

    @property
    def readiness_host(self) -> str:
        return "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host


Policy = Disabled | Enabled


class ServiceState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    BACKING_OFF = "backing-off"
    PARKED = "parked"


def _parse_enabled(raw: str) -> bool:
    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ConfigError(
        "NEURODESKTOP_T3_CODE_ENABLE must be one of 1, true, yes, on, "
        "0, false, no, or off"
    )


def _absolute_directory(raw: str, name: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ConfigError(f"{name} must name an existing absolute directory")
    return path.resolve()


def policy_from_environment(
    environ: Mapping[str, str], *, euid: int | None = None
) -> Policy:
    """Parse all external settings once, before a T3 process can start."""

    if not _parse_enabled(environ.get("NEURODESKTOP_T3_CODE_ENABLE", "0")):
        return Disabled("NEURODESKTOP_T3_CODE_ENABLE is disabled")

    current_uid = os.geteuid() if euid is None else euid
    if current_uid == 0:
        raise ConfigError("T3 Code must run as the Neurodesktop user, not root")

    home = _absolute_directory(environ.get("HOME", ""), "HOME")
    home_stat = home.stat()
    if home_stat.st_uid != current_uid:
        raise ConfigError(f"HOME must be owned by uid {current_uid}")
    if not os.access(home, os.W_OK | os.X_OK):
        raise ConfigError("HOME must be writable by the Neurodesktop user")

    workdir = _absolute_directory(
        environ.get("NEURODESKTOP_T3_CODE_WORKDIR", str(home)),
        "NEURODESKTOP_T3_CODE_WORKDIR",
    )
    base_dir = Path(
        environ.get("NEURODESKTOP_T3_CODE_HOME", str(home / ".t3"))
    ).expanduser()
    if not base_dir.is_absolute():
        raise ConfigError("NEURODESKTOP_T3_CODE_HOME must be an absolute path")

    host = environ.get("NEURODESKTOP_T3_CODE_HOST", "127.0.0.1").strip()
    if not host or any(character.isspace() for character in host):
        raise ConfigError("NEURODESKTOP_T3_CODE_HOST must be a host or interface")

    try:
        port = int(environ.get("NEURODESKTOP_T3_CODE_PORT", str(DEFAULT_PORT)))
    except ValueError as error:
        raise ConfigError("NEURODESKTOP_T3_CODE_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise ConfigError("NEURODESKTOP_T3_CODE_PORT must be between 1 and 65535")

    executable = Path(
        environ.get("NEURODESKTOP_T3_CODE_EXECUTABLE", str(DEFAULT_EXECUTABLE))
    )
    if not executable.is_absolute() or not executable.is_file():
        raise ConfigError("NEURODESKTOP_T3_CODE_EXECUTABLE is not an installed file")
    if not os.access(executable, os.X_OK):
        raise ConfigError("NEURODESKTOP_T3_CODE_EXECUTABLE is not executable")

    provider_bin = _absolute_directory(
        environ.get(
            "NEURODESKTOP_T3_CODE_PROVIDER_BIN", str(DEFAULT_PROVIDER_BIN)
        ),
        "NEURODESKTOP_T3_CODE_PROVIDER_BIN",
    )
    return Enabled(
        home=home,
        base_dir=base_dir.resolve(),
        workdir=workdir,
        host=host,
        port=port,
        executable=executable.resolve(),
        provider_bin=provider_bin,
    )


def server_command(policy: Enabled) -> list[str]:
    """Return the fixed, shell-free argv used for every managed server."""

    return [
        str(policy.executable),
        "serve",
        "--mode",
        "web",
        "--no-browser",
        "--host",
        policy.host,
        "--port",
        str(policy.port),
        "--base-dir",
        str(policy.base_dir),
        str(policy.workdir),
    ]


def server_environment(
    policy: Enabled, environ: Mapping[str, str]
) -> dict[str, str]:
    """Give T3 quiet, image-owned providers and private persistent state."""

    # A parent T3 desktop or harness exports service-launcher IPC and bearer
    # variables. They belong to that process tree and can make this independent
    # server attach to the wrong launcher or inherit a credential. Rebuild the
    # complete T3 namespace from the policy below.
    child = {
        name: value
        for name, value in environ.items()
        if not name.startswith(("T3_", "T3CODE_"))
    }
    current_path = child.get("PATH", "")
    child.update(
        {
            "HOME": str(policy.home),
            "PATH": str(policy.provider_bin)
            + (os.pathsep + current_path if current_path else ""),
            "CODEX_PATH": "/usr/bin/codex",
            "CLAUDE_CODE_EXECUTABLE": "/opt/jovyan_defaults/.local/bin/claude",
            "T3CODE_HOME": str(policy.base_dir),
            "T3CODE_HOST": policy.host,
            "T3CODE_PORT": str(policy.port),
            "T3CODE_LOG_LEVEL": "Warn",
            "T3CODE_TRACE_MIN_LEVEL": "Warn",
            "T3CODE_TRACE_TIMING_ENABLED": "false",
            "T3CODE_TRACE_FILE": "/dev/null",
            "T3CODE_NO_BROWSER": "true",
            "T3CODE_AUTO_BOOTSTRAP_PROJECT_FROM_CWD": "false",
        }
    )
    return child


def find_free_port() -> int:
    """Reserve and release a loopback port for isolated process tests."""

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class T3Supervisor:
    """Own exactly one T3 process until Jupyter Server shuts down."""

    def __init__(
        self,
        policy: Enabled,
        *,
        environ: Mapping[str, str] | None = None,
        logger: logging.Logger | None = None,
        readiness_timeout: float = 20.0,
        restart_limit: int = 5,
    ) -> None:
        self.policy = policy
        self.environ = dict(os.environ if environ is None else environ)
        self.log = logger or logging.getLogger(__name__)
        self.readiness_timeout = readiness_timeout
        self.restart_limit = restart_limit
        self.state = ServiceState.STOPPED
        self.pid: int | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    def start(self) -> None:
        """Start ownership once; repeated calls keep the existing task."""

        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self.run(), name="neurodesk-t3-code")

    async def wait_ready(self, *, timeout: float | None = None) -> None:
        """Wait until T3 accepts TCP connections or startup parks."""

        await asyncio.wait_for(
            self._ready.wait(),
            timeout=self.readiness_timeout if timeout is None else timeout,
        )
        if self.state is not ServiceState.READY:
            raise RuntimeError(f"T3 Code did not start: {self.state.value}")

    async def run(self) -> None:
        """Start, monitor, and restart the child within a bounded budget."""

        failures = 0
        try:
            if await self._port_accepting():
                self.state = ServiceState.PARKED
                self._ready.set()
                self.log.warning(
                    "T3 Code is disabled for this session because %s:%s is in use.",
                    self.policy.host,
                    self.policy.port,
                )
                return

            while failures < self.restart_limit:
                self.state = ServiceState.STARTING
                self.policy.base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                try:
                    self._process = await asyncio.create_subprocess_exec(
                        *server_command(self.policy),
                        env=server_environment(self.policy, self.environ),
                        cwd=self.policy.workdir,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except OSError as error:
                    failures += 1
                    self.log.warning(
                        "T3 Code process could not start (%s); Jupyter remains available.",
                        error.strerror or type(error).__name__,
                    )
                    if failures < self.restart_limit:
                        self.state = ServiceState.BACKING_OFF
                        await asyncio.sleep(min(2 ** (failures - 1), 8))
                    continue
                self.pid = self._process.pid
                if await self._wait_for_listener():
                    self.state = ServiceState.READY
                    self._ready.set()
                    self.log.info(
                        "T3 Code is listening on %s:%s as pid %s.",
                        self.policy.host,
                        self.policy.port,
                        self.pid,
                    )
                    await self._process.wait()
                failures += 1
                self.pid = None
                self._process = None
                if failures < self.restart_limit:
                    self.state = ServiceState.BACKING_OFF
                    await asyncio.sleep(min(2 ** (failures - 1), 8))

            self.state = ServiceState.PARKED
            self._ready.set()
            self.log.warning(
                "T3 Code stopped after %s failed starts; Jupyter remains available.",
                failures,
            )
        finally:
            await self._terminate_process()
            if self.state is not ServiceState.PARKED:
                self.state = ServiceState.STOPPED

    async def close(self) -> None:
        """Cancel supervision and stop the complete T3 process group."""

        task = self._task
        if task is None:
            self.state = ServiceState.STOPPED
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None
        self.pid = None
        self.state = ServiceState.STOPPED

    async def _wait_for_listener(self) -> bool:
        deadline = asyncio.get_running_loop().time() + self.readiness_timeout
        while asyncio.get_running_loop().time() < deadline:
            if self._process is None or self._process.returncode is not None:
                return False
            if await self._port_accepting():
                return True
            await asyncio.sleep(0.1)
        await self._terminate_process()
        return False

    async def _port_accepting(self) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.policy.readiness_host, self.policy.port),
                timeout=0.25,
            )
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()
        return True

    async def _terminate_process(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            self._process = None
            self.pid = None
            return
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        self._process = None
        self.pid = None
