"""Static binaries and an unauthenticated userspace daemon as the image user."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from testlib import first_existing_path, load_source_module


def binary(name):
    return str(first_existing_path(f"/usr/local/bin/{name}"))


def test_tailscale_cli_and_daemon_versions_match():
    client = subprocess.check_output([binary("tailscale"), "version"], text=True, timeout=10)
    daemon = subprocess.check_output([binary("tailscaled"), "--version"], text=True, timeout=10)
    assert client.splitlines()[0] == daemon.splitlines()[0] == "1.102.4"


def test_tailscaled_starts_as_unprivileged_user_without_tun():
    assert os.geteuid() != 0, "Run the image tests as the unprivileged notebook user"
    # Keep the LocalAPI socket path below the Unix socket length limit.
    with tempfile.TemporaryDirectory(prefix="ts-test-") as directory:
        root = Path(directory)
        socket = root / "ts.sock"
        with (root / "daemon.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    binary("tailscaled"),
                    "--tun=userspace-networking",
                    "--port=0",
                    f"--socket={socket}",
                    f"--statedir={root / 'state'}",
                    f"--state={root / 'state/tailscaled.state'}",
                ],
                env={**os.environ, "TS_NO_LOGS_NO_SUPPORT": "true"},
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and process.poll() is None:
                    if socket.exists():
                        status = subprocess.run(
                            [binary("tailscale"), f"--socket={socket}", "status", "--json"],
                            capture_output=True, text=True, timeout=3,
                        )
                        if status.returncode == 0:
                            state = json.loads(status.stdout)["BackendState"]
                            if state == "NeedsLogin":
                                return
                    time.sleep(0.1)
                log.seek(0)
                raise AssertionError(f"Userspace daemon did not become ready:\n{log.read()[-8000:]}")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def test_setup_command_starts_and_reuses_rootless_daemon(monkeypatch):
    assert os.geteuid() != 0, "Run as the unprivileged notebook user"
    script = load_source_module(
        "t3_setup", "/opt/neurodesktop/t3_neurodesk_setup.py",
        "scripts/t3_neurodesk_setup.py",
    )
    command = binary("t3_neurodesk_setup")
    assert shutil.which("neurodesktop-t3-setup") is None
    help_result = subprocess.run([command, "--help"], capture_output=True, text=True, timeout=10)
    assert help_result.returncode == 0
    assert "--check" in help_result.stdout
    processes = []
    real_popen = subprocess.Popen

    def record_process(argv, **kwargs):
        process = real_popen(argv, **kwargs)
        if argv[0] == binary("tailscaled"):
            processes.append(process)
        return process

    monkeypatch.setattr(script.subprocess, "Popen", record_process)
    monkeypatch.setenv("TS_NO_LOGS_NO_SUPPORT", "true")
    with tempfile.TemporaryDirectory(prefix="ts-setup-") as directory:
        root = Path(directory)
        socket = root / "ts.sock"
        ts = [binary("tailscale"), f"--socket={socket}"]
        try:
            first = script.ensure_daemon(binary("tailscaled"), ts, socket, root / "state")
            second = script.ensure_daemon(binary("tailscaled"), ts, socket, root / "state")
            assert first["BackendState"] == second["BackendState"] == "NeedsLogin"
            assert script.command_json([*ts, "serve", "status", "--json"]) == {}
            assert len(processes) == 1
            assert (root / "state").stat().st_mode & 0o777 == 0o700
            assert os.getsid(processes[0].pid) == processes[0].pid
        finally:
            for process in processes:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
