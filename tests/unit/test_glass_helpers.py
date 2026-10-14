"""Exercise compiled Glass helpers at their X11, vsock and exec boundaries."""

import os
import select
import shutil
import signal
import socket
import struct
import subprocess
import time

import pytest

from testlib import repo_path


@pytest.fixture(scope="module")
def binaries(tmp_path_factory):
    build = tmp_path_factory.mktemp("glass-native")
    for tool in ("gcc", "Xvfb", "xrandr"):
        assert shutil.which(tool), (
            f"Install gcc libx11-dev xvfb x11-xserver-utils ({tool} missing)"
        )
    for name, source, flags in (
        ("clipboard", "config/glass/glass-clipboard.c", ["-lX11"]),
        ("resize", "config/glass/glass-resize.c", []),
        ("peer", "tests/fixtures/glass/peer.c", ["-lX11"]),
        (
            "transport.so",
            "tests/fixtures/glass/transport.c",
            ["-shared", "-fPIC", "-ldl"],
        ),
    ):
        subprocess.run(
            [
                "gcc",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(repo_path(source)),
                "-o",
                str(build / name),
                *flags,
            ],
            check=True,
        )
    return build


@pytest.fixture
def display():
    read, write = os.pipe()
    process = subprocess.Popen(
        [
            "Xvfb",
            "-displayfd",
            str(write),
            "-screen",
            "0",
            "800x600x24",
            "-nolisten",
            "tcp",
        ],
        pass_fds=(write,),
        stderr=subprocess.PIPE,
    )
    os.close(write)
    try:
        assert select.select([read], [], [], 5)[0], "Xvfb did not start"
        yield ":" + os.read(read, 32).decode().strip()
    finally:
        os.close(read)
        process.terminate()
        process.communicate(timeout=5)


class Bridge:
    def __init__(self, binaries, tmp_path, display, name):
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(tmp_path / "host"))
        self.listener.listen()
        self.listener.settimeout(4)
        self.env = {
            **os.environ,
            "DISPLAY": display,
            "LD_PRELOAD": str(binaries / "transport.so"),
            "GLASS_TEST_SOCKET": str(tmp_path / "host"),
            "GLASS_TEST_PORT": "10778" if name == "clipboard" else "10779",
        }
        recorder = tmp_path / "recorder"
        self.record = tmp_path / "calls"
        recorder.write_text(
            "#!/bin/sh\n"
            'if [ -n "$GLASS_TEST_REAL_XRANDR" ]; then\n'
            '  /usr/bin/xrandr "$@" && printf "accepted\\n" >> "$GLASS_TEST_CALLS"\n'
            "  exit $?\nfi\n"
            'printf "%s\\n" "$*" >> "$GLASS_TEST_CALLS"\n'
            '[ "$(wc -l < "$GLASS_TEST_CALLS")" -gt 1 ]\n'
        )
        recorder.chmod(0o755)
        self.env.update(
            GLASS_TEST_RECORDER=str(recorder), GLASS_TEST_CALLS=str(self.record)
        )
        self.process = subprocess.Popen(
            [binaries / name], env=self.env, stderr=subprocess.PIPE
        )
        self.connection, _ = self.listener.accept()
        self.connection.settimeout(3)
        self.peer = subprocess.Popen(
            [binaries / "peer"],
            env={**os.environ, "DISPLAY": display},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=0,
        )
        assert self.line() == "ready"

    def line(self):
        assert select.select([self.peer.stdout], [], [], 4)[0], (
            "X11 peer did not answer"
        )
        return self.peer.stdout.readline().decode().rstrip("\n")

    def command(self, command):
        self.peer.stdin.write((command + "\n").encode())
        self.peer.stdin.flush()
        assert self.line() == "ok"

    def receive(self):
        def exact(n):
            data = b""
            while len(data) < n:
                chunk = self.connection.recv(n - len(data))
                assert chunk, "bridge disconnected"
                data += chunk
            return data

        return exact(struct.unpack("!I", exact(4))[0])

    def send(self, text):
        frame = struct.pack("!I", len(text)) + text
        for byte in frame:
            self.connection.sendall(bytes([byte]))

    def quiet(self):
        assert not select.select([self.connection], [], [], 0.5)[0]

    def reconnect(self):
        self.connection.close()
        self.connection, _ = self.listener.accept()
        self.connection.settimeout(3)

    def disconnect(self):
        self.listener.close()
        self.connection.close()
        os.unlink(self.env["GLASS_TEST_SOCKET"])

    def resume(self):
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.env["GLASS_TEST_SOCKET"])
        self.listener.listen()
        self.listener.settimeout(4)
        self.connection, _ = self.listener.accept()
        self.connection.settimeout(3)

    def close(self):
        self.process.send_signal(signal.SIGCONT)
        self.process.terminate()
        self.process.communicate(timeout=5)
        self.peer.terminate()
        self.peer.communicate(timeout=5)
        self.connection.close()
        self.listener.close()


@pytest.fixture
def bridge(binaries, tmp_path, display):
    active = []

    def start(name="clipboard"):
        instance = Bridge(binaries, tmp_path, display, name)
        active.append(instance)
        return instance

    yield start
    for instance in active:
        instance.close()


def test_same_window_copies_and_host_takeover(bridge):
    b = bridge()
    b.command("own first")
    assert b.receive() == b"first"
    b.command("set second")
    assert b.receive() == b"second"
    b.quiet()
    b.send(b"host")
    time.sleep(0.3)
    b.command("paste")
    assert b.line() == "text:host"
    b.command("own second")
    assert b.receive() == b"second"
    b.reconnect()
    assert b.receive() == b"second"


@pytest.mark.parametrize("paste_offline", [True, False])
def test_host_clipboard_survives_relay_outage(bridge, paste_offline):
    b = bridge()
    b.send(b"cached host")
    time.sleep(0.3)
    b.command("paste")
    assert b.line() == "text:cached host"
    b.disconnect()
    time.sleep(0.4)
    if paste_offline:
        b.command("paste")
        assert b.line() == "text:cached host"
    b.resume()
    b.command("paste")
    assert b.line() == "text:cached host"
    b.send(b"fresh host")
    time.sleep(0.3)
    b.command("paste")
    assert b.line() == "text:fresh host"


@pytest.mark.parametrize("initial_owner", ["host", "guest"])
def test_guest_clipboard_changed_offline_is_sent_after_reconnect(bridge, initial_owner):
    b = bridge()
    if initial_owner == "host":
        b.send(b"cached host")
        time.sleep(0.3)
    else:
        b.command("own initial guest")
        assert b.receive() == b"initial guest"
    b.disconnect()
    time.sleep(0.4)
    b.command("own offline guest")
    b.resume()
    assert b.receive() == b"offline guest"
    b.command("paste")
    assert b.line() == "text:offline guest"


def test_first_empty_and_refusal_recovery(bridge):
    b = bridge()
    b.command("own ")
    assert b.receive() == b""
    b.command("refuse")
    time.sleep(0.4)
    b.command("set recovered")
    assert b.receive() == b"recovered"


def test_pending_timeout_and_host_takeover(bridge):
    b = bridge()
    b.command("own initial")
    assert b.receive() == b"initial"
    b.command("hold")
    time.sleep(1.5)
    b.send(b"host")
    time.sleep(0.3)
    b.command("release")
    b.quiet()
    b.command("paste")
    assert b.line() == "text:host"
    b.command("own recovered")
    assert b.receive() == b"recovered"


def test_timeout_rejects_old_reply_then_samples_again(bridge):
    b = bridge()
    b.command("own initial")
    assert b.receive() == b"initial"
    b.command("hold")
    b.command("text delayed")
    time.sleep(1.5)
    b.command("release")
    b.quiet()
    assert b.receive() == b"delayed"


def test_oversized_selection_refuses_and_recovers(bridge):
    b = bridge()
    text = b"x" * (20 << 20)
    b.connection.sendall(struct.pack("!I", len(text)) + text)
    time.sleep(0.3)
    b.command("paste")
    time.sleep(0.1)
    assert b.process.poll() is None, b.process.stderr.read().decode()
    assert b.line() == "refused"
    b.command("targets")
    assert b.line() == "targets:TARGETS,UTF8_STRING,STRING"
    b.send(b"recovered")
    time.sleep(0.3)
    b.command("paste")
    assert b.line() == "text:recovered"
    assert b.process.poll() is None


def test_unrelated_x_error_keeps_default_handler(bridge, monkeypatch):
    monkeypatch.setenv("GLASS_TEST_UNRELATED_ERROR", "1")
    b = bridge()
    b.send(b"host")
    time.sleep(0.3)
    b.command("paste")
    assert b.process.wait(timeout=3) == 1
    assert b"BadAtom" in b.process.stderr.read()


def test_destroyed_requester_does_not_kill_bridge(bridge):
    b = bridge()
    b.send(b"survives")
    time.sleep(0.3)
    b.process.send_signal(signal.SIGSTOP)
    _, status = os.waitpid(b.process.pid, os.WUNTRACED)
    assert os.WIFSTOPPED(status)
    b.command("destroy")
    b.process.send_signal(signal.SIGCONT)
    time.sleep(0.3)
    assert b.process.poll() is None
    b.command("paste")
    assert b.line() == "text:survives"


def test_resize_fragmentation_retries_invalid_and_reconnect(bridge):
    b = bridge("resize")
    frame = struct.pack("!II", 800, 600)
    b.connection.sendall(frame[:3])
    time.sleep(0.1)
    assert not b.record.exists()
    for byte in frame[3:]:
        b.connection.sendall(bytes([byte]))
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if b.record.exists() and len(b.record.read_text().splitlines()) == 2:
            break
        time.sleep(0.02)
    assert b.record.read_text().splitlines() == ["--screen 0 --size 800x600"] * 2
    for size in ((0, 600), (800, 0), (8193, 600), (800, 8193)):
        b.connection.sendall(struct.pack("!II", *size))
        assert b.connection.recv(1) == b""
        b.reconnect()
    assert len(b.record.read_text().splitlines()) == 2
    b.connection.sendall(struct.pack("!II", 640, 480))
    time.sleep(0.2)
    assert b.record.read_text().splitlines()[-1] == "--screen 0 --size 640x480"


def test_real_xrandr_accepts_advertised_mode(bridge, monkeypatch):
    monkeypatch.setenv("GLASS_TEST_REAL_XRANDR", "1")
    b = bridge("resize")
    b.connection.sendall(struct.pack("!II", 800, 600))
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline and not b.record.exists():
        time.sleep(0.02)
    assert b.record.read_text() == "accepted\n"
