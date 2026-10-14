#!/usr/bin/python3 -I
"""Expose code-server's Unix socket only after its routes are registered.

code-server 4.138.0 listens and applies ``--socket-mode`` before it registers
``/healthz`` and the editor routes. jupyter-server-proxy 4.5.0 treats any HTTP
response as ready, so it could forward a user's first request to the empty
router and show a 404. Start code-server on a staging socket beside the
proxy's path and rename it into place once ``/healthz`` answers 200.

Usage: code_server_ready.py CODE_SERVER [ARGS...] --socket PATH [ARGS...]
"""

from __future__ import annotations

import ctypes
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


PR_SET_PDEATHSIG = 1
FORWARDED_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def staging_command(command: list[str]) -> tuple[list[str], Path, Path]:
    """Return *command* listening on a staging socket, and both socket paths."""
    if command.count("--socket") != 1 or command.index("--socket") + 1 >= len(command):
        raise ValueError("expected exactly one '--socket PATH' argument")
    index = command.index("--socket")
    target = Path(command[index + 1])
    # A per-launch name keeps an orphan from a timed-out launch, which libuv
    # unlinks by path on shutdown, from removing this launch's socket.
    staging = target.with_name(f".{target.name}.{os.getpid()}.starting")
    return [*command[: index + 1], str(staging), *command[index + 2 :]], target, staging


def healthz_status(endpoint: Path) -> int | None:
    """Return the HTTP status of ``GET /healthz``, or None if unreachable."""
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(str(endpoint))
            client.sendall(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            response = b""
            while b"\r\n" not in response and (chunk := client.recv(4096)):
                response += chunk
    except OSError:
        return None
    fields = response.split(b"\r\n", 1)[0].split(b" ")
    return int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else None


def _die_with_parent(parent: int) -> None:
    # Jupyter Server Proxy may SIGKILL this launcher; take code-server with it.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    if os.getppid() != parent:
        os._exit(1)


def run(command: list[str], poll_interval: float = 0.05) -> int:
    command, target, staging = staging_command(command)
    for stale in (target, staging):
        stale.unlink(missing_ok=True)

    parent = os.getpid()
    child = subprocess.Popen(command, preexec_fn=lambda: _die_with_parent(parent))
    for signum in FORWARDED_SIGNALS:
        signal.signal(signum, lambda received, _frame: child.send_signal(received))

    published = None
    try:
        while child.poll() is None:
            if healthz_status(staging) == 200:
                os.replace(staging, target)
                published = target.stat().st_ino
                break
            time.sleep(poll_interval)
        returncode = child.wait()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait()
        staging.unlink(missing_ok=True)
        try:
            if target.stat().st_ino == published:
                target.unlink()
        except FileNotFoundError:
            pass
    return 128 - returncode if returncode < 0 else returncode


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    return run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
