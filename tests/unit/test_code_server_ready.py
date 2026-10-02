"""The VS Code launcher hides code-server's socket until its routes answer."""

import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest

from testlib import load_source_module, repo_path, resolve_source


LAUNCHER = resolve_source("/opt/neurodesktop/code_server_ready.py", "config/jupyter/code_server_ready.py")

FAKE_CODE_SERVER = """\
import os, signal, socket, sys, time
from pathlib import Path

state = Path(sys.argv[1])
endpoint = sys.argv[sys.argv.index("--socket") + 1]
(state / "pid").write_text(str(os.getpid()))
(state / "socket-arg").write_text(endpoint)
if (state / "exit-early").exists():
    sys.exit(3)
def shut_down(signum, _frame):
    # Like libuv, unlink the bound path when the server closes.
    time.sleep(float((state / "shutdown-delay").read_text()) if (state / "shutdown-delay").exists() else 0)
    try:
        os.unlink(endpoint)
    except FileNotFoundError:
        pass
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)

signal.signal(signal.SIGTERM, shut_down)
server = socket.socket(socket.AF_UNIX)
server.bind(endpoint)
os.chmod(endpoint, 0o600)
server.listen()
while True:
    connection, _ = server.accept()
    with connection:
        request = connection.recv(4096)
        status = "200 OK" if (state / "routes").exists() else "404 Not Found"
        with (state / "requests").open("a") as log:
            log.write(status + "\\n")
        connection.sendall(f"HTTP/1.1 {status}\\r\\nContent-Length: 0\\r\\nConnection: close\\r\\n\\r\\n".encode())
"""


def wait_until(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.02)


def pid_running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open(f"/proc/{pid}/stat") as stat:
        return stat.read().rsplit(")", 1)[1].split()[0] != "Z"


@pytest.fixture
def launch(tmp_path):
    fake = tmp_path / "code-server"
    fake.write_text(f"#!{sys.executable}\n{FAKE_CODE_SERVER}")
    fake.chmod(0o755)
    # Mirror the proxy's short mkdtemp() path; AF_UNIX paths are limited to 107 bytes.
    socket_dir = Path(tempfile.mkdtemp(prefix="jupyter-server-proxy-", dir="/tmp"))
    endpoint = socket_dir / "socket"
    processes = []

    def start(state=tmp_path):
        process = subprocess.Popen(
            [str(LAUNCHER), str(fake), str(state), "--auth", "none",
             "--socket", str(endpoint), "--socket-mode", "0600", str(tmp_path)],
        )
        processes.append(process)
        return process

    yield start, endpoint, tmp_path
    for process in processes:
        if process.poll() is None:
            process.kill()
            process.wait()
    for pid_file in tmp_path.glob("**/pid"):
        if pid_running(int(pid_file.read_text())):
            os.kill(int(pid_file.read_text()), signal.SIGKILL)
    shutil.rmtree(socket_dir, ignore_errors=True)


def get_status(endpoint):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(str(endpoint))
        client.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        return client.recv(4096).split(b"\r\n", 1)[0]


def test_socket_appears_only_after_code_server_routes_answer(launch):
    start, endpoint, state = launch
    process = start()

    wait_until(lambda: (state / "requests").exists() and "404" in (state / "requests").read_text())
    staging = state / "socket-arg"
    assert staging.read_text() == str(endpoint.parent / f".socket.{process.pid}.starting")
    assert os.path.dirname(staging.read_text()) == str(endpoint.parent)
    assert not endpoint.exists()

    (state / "routes").touch()
    wait_until(endpoint.exists)

    assert endpoint.stat().st_mode & 0o777 == 0o600
    assert not os.path.exists(staging.read_text())
    assert get_status(endpoint) == b"HTTP/1.1 200 OK"

    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=10) == 128 + signal.SIGTERM
    assert not pid_running(int((state / "pid").read_text()))
    assert not endpoint.exists()


def test_launcher_leaves_a_socket_it_did_not_publish(launch):
    start, endpoint, state = launch
    process = start()
    (state / "routes").touch()
    wait_until(endpoint.exists)

    # A later launch for the same proxy path has replaced this one's socket.
    with socket.socket(socket.AF_UNIX) as successor:
        replacement = endpoint.with_name("successor")
        successor.bind(str(replacement))
        os.replace(replacement, endpoint)

        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 128 + signal.SIGTERM
        assert endpoint.is_socket()


def test_orphan_from_a_timed_out_launch_keeps_off_the_next_socket(launch):
    start, endpoint, state = launch
    first_state, second_state = state / "first", state / "second"
    first_state.mkdir()
    second_state.mkdir()
    (first_state / "shutdown-delay").write_text("0.5")
    first = start(first_state)
    wait_until(lambda: (first_state / "requests").exists())

    # Jupyter Server Proxy SIGKILLs a launch that misses its readiness timeout
    # and starts another while the orphaned code-server is still shutting down.
    first.kill()
    first.wait()
    second = start(second_state)
    wait_until(lambda: (second_state / "requests").exists())
    wait_until(lambda: not pid_running(int((first_state / "pid").read_text())))
    (second_state / "routes").touch()
    wait_until(endpoint.exists)

    assert get_status(endpoint) == b"HTTP/1.1 200 OK"
    assert second.poll() is None


def test_code_server_exit_before_ready_is_reported(launch):
    start, endpoint, state = launch
    (state / "exit-early").touch()

    assert start().wait(timeout=10) == 3
    assert not endpoint.exists()


def test_killed_launcher_takes_code_server_with_it(launch):
    start, _, state = launch
    process = start()
    wait_until((state / "pid").exists)
    wait_until(lambda: (state / "requests").exists())

    process.kill()
    process.wait()

    wait_until(lambda: not pid_running(int((state / "pid").read_text())))


def test_launcher_requires_exactly_one_socket_argument():
    module = load_source_module("code_server_ready", "/opt/neurodesktop/code_server_ready.py",
                                "config/jupyter/code_server_ready.py")
    for command in (["code-server"], ["code-server", "--socket"],
                    ["code-server", "--socket", "a", "--socket", "b"]):
        with pytest.raises(ValueError):
            module.staging_command(command)


def test_launcher_is_installed_executable():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    assert "install -m 0755 /tmp/jupyter/code_server_ready.py /opt/neurodesktop/code_server_ready.py" in dockerfile
