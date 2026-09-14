"""Installed-image checks for the optional T3 Code server."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time


def test_t3_code_runtime_and_native_terminal_support_are_installed():
    assert subprocess.check_output(["t3", "--version"], text=True).strip() == "t3 v0.0.40"
    major, minor, *_ = map(int, subprocess.check_output(["node", "--version"], text=True).strip()[1:].split("."))
    assert (major, minor) >= (24, 10)
    assert shutil.which("codex")
    assert shutil.which("claude")
    assert shutil.which("opencode")

    subprocess.run(
        [
            "node",
            "-e",
            "const p=require('/opt/t3-code/node_modules/node-pty');"
            "const x=p.spawn('/bin/sh',['-c','exit 0']);"
            "x.onExit(({exitCode})=>process.exit(exitCode));",
        ],
        check=True,
        timeout=10,
    )


def test_t3_code_image_does_not_ship_duplicate_provider_or_foreign_pty_payloads():
    node_modules = Path("/opt/t3-code/node_modules")
    assert not list((node_modules / "@anthropic-ai").glob("claude-agent-sdk-*"))
    assert not (node_modules / "node-pty/prebuilds").exists()
    assert not Path.home().joinpath(".cache/node-gyp").exists()
    assert os.access("/opt/neurodesktop/t3-provider-bin/codex", os.X_OK)
    assert os.access("/opt/neurodesktop/t3-provider-bin/claude", os.X_OK)
    assert os.access("/opt/neurodesktop/t3-provider-bin/opencode", os.X_OK)


def test_real_t3_server_starts_on_loopback_with_private_state(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path),
            "PATH": f"/opt/neurodesktop/t3-provider-bin:{environment['PATH']}",
            "T3CODE_LOG_LEVEL": "Warn",
            "T3CODE_TRACE_MIN_LEVEL": "Warn",
            "T3CODE_TRACE_FILE": "/dev/null",
        }
    )
    process = subprocess.Popen(
        [
            "t3",
            "serve",
            "--mode",
            "web",
            "--no-browser",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--base-dir",
            str(tmp_path / ".t3"),
            str(tmp_path),
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f"T3 exited during startup with {process.returncode}")
            with socket.socket() as client:
                client.settimeout(0.2)
                if client.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("T3 did not listen within 20 seconds")
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    assert (tmp_path / ".t3/userdata").is_dir()
