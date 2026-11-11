"""Run config/voxl/voxl.sh against fake X, session and voxl executables."""

import signal
import subprocess
import time

import pytest

from testlib import repo_path

TOKEN = "launch-token-for-tests"


def _write_executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _long_running(log, name):
    """Shell that logs its start and SIGTERM, then idles until stopped."""
    return (
        f'trap \'echo "{name} stopped" >> "{log}"; exit 0\' TERM\n'
        "while :; do sleep 0.05; done\n"
    )


@pytest.fixture
def launcher(tmp_path):
    home = tmp_path / "home"
    (home / ".vnc").mkdir(parents=True)
    log = tmp_path / "calls.log"
    _write_executable(
        home / ".vnc" / "xstartup",
        f'#!/usr/bin/env bash\necho "session DISPLAY=$DISPLAY" >> "{log}"\n' + _long_running(log, "session"),
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Refuses the first display it is offered, like a display another session
    # holds, then serves the next one until stopped.
    _write_executable(
        bin_dir / "Xvfb",
        "#!/usr/bin/env bash\n"
        f'if [ ! -e "{log}.first" ]; then touch "{log}.first"; echo "busy $1" >> "{log}"; exit 1; fi\n'
        f'echo "xvfb $$ $*" >> "{log}"\n'
        # Report readiness the way Xvfb does: the display number on -displayfd.
        'fd=""; prev=""\n'
        'for arg in "$@"; do [ "$prev" = -displayfd ] && fd="$arg"; prev="$arg"; done\n'
        'echo "${1#:}" >&"$fd"\n' + _long_running(log, "xvfb"),
    )
    _write_executable(bin_dir / "xset", "#!/bin/sh\nexit 0\n")
    _write_executable(bin_dir / "mcookie", "#!/bin/sh\necho 0123456789abcdef0123456789abcdef\n")
    _write_executable(bin_dir / "xauth", f'#!/bin/sh\necho "xauth $*" >> "{log}"\n')
    _write_executable(
        bin_dir / "neurodesktop-shm-private",
        f'#!/bin/sh\necho "shm-private $1" >> "{log}"\nexit "${{FAKE_SHM_STATUS:-0}}"\n',
    )
    voxl = _write_executable(
        tmp_path / "voxl",
        f'#!/usr/bin/env bash\necho "voxl DISPLAY=$DISPLAY PASSWORD=$VOXL_PASSWORD $*" >> "{log}"\n'
        + _long_running(log, "voxl"),
    )
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "NEURODESKTOP_VOXL_BIN": str(voxl),
        "NEURODESKTOP_XVFB_BIN": str(bin_dir / "Xvfb"),
        "VOXL_PASSWORD": TOKEN,
    }
    return env, log


def _wait_for(log, text, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log.exists() and text in log.read_text():
            return log.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{text!r} not logged:\n{log.read_text() if log.exists() else ''}")


def _line(calls, prefix):
    return next(line for line in calls.splitlines() if line.startswith(prefix))


def _start(env):
    return subprocess.Popen(
        [str(repo_path("config/voxl/voxl.sh")), "45123"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_streams_a_private_xvfb_through_an_authenticated_loopback_voxl(launcher):
    env, log = launcher
    proc = _start(env)
    try:
        calls = _wait_for(log, "voxl DISPLAY=")
        _wait_for(log, "session DISPLAY=")
        calls = log.read_text()
        _, xvfb_pid, display, *xvfb_args = _line(calls, "xvfb ").split()
        busy = _line(calls, "busy ").split()[1]
        assert int(display[1:]) > int(busy[1:]) >= 60

        # Zero-copy framebuffer, no TCP listener, and cookie authentication.
        assert "-shmem" in xvfb_args
        assert xvfb_args[xvfb_args.index("-nolisten") + 1] == "tcp"
        assert xvfb_args[xvfb_args.index("-auth") + 1] == env["HOME"] + "/.Xauthority"
        assert f"xauth add {display} . 0123456789abcdef0123456789abcdef" in calls
        # The framebuffer is made private before the session or voxl starts.
        assert _line(calls, "shm-private ") == f"shm-private {xvfb_pid}"
        assert calls.index("shm-private ") < calls.index("session DISPLAY=")
        assert calls.index("shm-private ") < calls.index("voxl DISPLAY=")
        assert f"session DISPLAY={display}" in calls

        args = _line(calls, "voxl ").split()
        assert f"DISPLAY={display}" in args
        assert f"PASSWORD={TOKEN}" in args
        assert args[args.index("--addr") + 1] == "127.0.0.1"
        assert args[args.index("--port") + 1] == "45123"
        assert args[args.index("--file-transfers") + 1] == "none"
        assert not any(arg.startswith("--enable-basic-auth") for arg in args)

        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) != 0
        calls = log.read_text()
        for name in ("voxl", "session", "xvfb"):
            assert f"{name} stopped" in calls
        # voxl stops before the X server it reads from.
        assert calls.index("voxl stopped") < calls.index("xvfb stopped")
        assert f"xauth remove {display}" in calls
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_refuses_a_framebuffer_it_cannot_make_private(launcher):
    env, log = launcher
    env["FAKE_SHM_STATUS"] = "2"
    proc = _start(env)
    _out, err = proc.communicate(timeout=20)
    assert proc.returncode == 1
    assert "Could not make the Xvfb framebuffer" in err
    calls = log.read_text()
    assert "xvfb stopped" in calls
    assert "voxl DISPLAY=" not in calls
    assert "session DISPLAY=" not in calls


def test_refuses_to_start_without_the_proxy_token(launcher):
    env, log = launcher
    del env["VOXL_PASSWORD"]
    proc = _start(env)
    _out, err = proc.communicate(timeout=10)
    assert proc.returncode == 1
    assert "VOXL_PASSWORD is empty" in err
    assert not log.exists()


def test_reports_when_no_display_can_start(launcher, tmp_path):
    env, log = launcher
    _write_executable(
        tmp_path / "bin" / "Xvfb",
        "#!/usr/bin/env bash\necho 'Fatal server error: no screens' >&2\nexit 1\n",
    )
    proc = _start(env)
    _out, err = proc.communicate(timeout=60)
    assert proc.returncode == 1
    assert "Could not start an X display for voxl" in err
    assert "no screens" in err
    assert "shm-private" not in (log.read_text() if log.exists() else "")
