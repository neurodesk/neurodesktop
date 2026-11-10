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


def test_installed_launcher_streams_only_with_the_proxy_token(tmp_path):
    if not os.path.isdir("/tmp/.X11-unix"):
        pytest.fail("the image must provide /tmp/.X11-unix for TigerVNC")
    port = _free_port()
    home = tmp_path / "home"
    home.mkdir()
    proc = subprocess.Popen(
        [LAUNCHER, str(port)],
        env={**os.environ, "HOME": str(home), "VOXL_PASSWORD": TOKEN},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    xvnc_pid = None
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
            proc.kill()
            pytest.fail(f"voxl never answered /healthz:\n{proc.communicate()[0].decode()}")
        assert body == b"ok"

        assert _status(port, "/", None)[0] == 401
        assert _status(port, "/", "wrong-token")[0] == 401
        assert _status(port, "/", TOKEN)[0] == 200

        state_dir = home / ".neurodesk" / "runtime-voxl"
        sockets = list(state_dir.glob("rfb-*.sock"))
        assert len(sockets) == 1
        assert oct(sockets[0].stat().st_mode & 0o777) == "0o600"
        display = sockets[0].stem.removeprefix("rfb-")
        xvnc_pid = int(
            subprocess.run(
                ["pgrep", "-u", str(os.getuid()), "-f", f"^/usr/bin/Xtigervnc :{display} "],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split()[0]
        )
        # The X server opens no TCP RFB or X11 listener; voxl reads the display
        # through its Unix socket.
        assert _listeners(xvnc_pid) == []

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and os.path.exists(f"/proc/{xvnc_pid}"):
            time.sleep(0.2)
        assert not os.path.exists(f"/proc/{xvnc_pid}"), "the voxl X server outlived its launcher"
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        if xvnc_pid and os.path.exists(f"/proc/{xvnc_pid}"):
            os.kill(xvnc_pid, signal.SIGKILL)
