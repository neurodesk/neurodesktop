"""Start the installed voxl launcher and check its authenticated loopback stream."""

import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest

LAUNCHER = "/opt/neurodesktop/voxl.sh"
TOKEN = "installed-image-voxl-token"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _status(port, path, token=None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def _listeners(pid):
    """TCP listening sockets owned by *pid*, from ss's process annotation."""
    output = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True, check=True).stdout
    return [line for line in output.splitlines() if f"pid={pid}," in line]


def test_installed_voxl_binary_links_its_libraries():
    ldd = subprocess.run(["ldd", "/usr/local/bin/voxl"], capture_output=True, text=True, check=True)
    assert "not found" not in ldd.stdout, ldd.stdout
    version = subprocess.run(["/usr/local/bin/voxl", "--version"], capture_output=True, text=True, check=True)
    assert version.stdout.startswith("voxl ")


def _segments_created_by(pid):
    with open("/proc/sysvipc/shm", encoding="ascii") as table:
        rows = [line.split() for line in table.readlines()[1:]]
    return [
        {"mode": int(row[2], 8) & 0o777, "nattch": int(row[6])}
        for row in rows
        if int(row[4]) == pid
    ]


def test_installed_launcher_streams_only_with_the_proxy_token(tmp_path):
    port = _free_port()
    home = tmp_path / "home"
    home.mkdir()
    log_path = tmp_path / "launcher.log"
    log = log_path.open("wb")
    proc = subprocess.Popen(
        [LAUNCHER, str(port)],
        env={**os.environ, "HOME": str(home), "VOXL_PASSWORD": TOKEN},
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    xvfb_pid = None
    try:
        deadline = time.monotonic() + 60
        status = None
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                status, body = _status(port, "/healthz", TOKEN)
                if status == 200:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        if status != 200:
            pytest.fail(f"voxl never answered /healthz:\n{log_path.read_text()}")
        assert body == b"ok"

        assert _status(port, "/", None)[0] == 401
        assert _status(port, "/", "wrong-token")[0] == 401
        assert _status(port, "/", TOKEN)[0] == 200

        display = (home / ".neurodesk" / "runtime-voxl" / "xvfb.displayfd").read_text().strip()
        xvfb_pid = int(
            subprocess.run(
                ["pgrep", "-u", str(os.getuid()), "-x", "-f", f"/usr/local/libexec/voxl/Xvfb :{display} .*"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split()[0]
        )
        # No TCP X11 listener; clients authenticate over the Unix socket.
        assert _listeners(xvfb_pid) == []
        # voxl reads the framebuffer in place, and only Xvfb and voxl hold it.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and b"directly from the Xvfb framebuffer" not in log_path.read_bytes():
            time.sleep(0.2)
        assert b"directly from the Xvfb framebuffer" in log_path.read_bytes(), log_path.read_text()
        # The patched Xvfb gives the XTEST pointer scroll axes for smooth scrolling.
        assert b"smooth scrolling via XTEST device" in log_path.read_bytes(), log_path.read_text()
        assert _segments_created_by(xvfb_pid) == [{"mode": 0o600, "nattch": 2}]

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and os.path.exists(f"/proc/{xvfb_pid}"):
            time.sleep(0.2)
        assert not os.path.exists(f"/proc/{xvfb_pid}"), "the voxl X server outlived its launcher"
        assert _segments_created_by(xvfb_pid) == []
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        if xvfb_pid and os.path.exists(f"/proc/{xvfb_pid}"):
            os.kill(xvfb_pid, signal.SIGKILL)
        log.close()
