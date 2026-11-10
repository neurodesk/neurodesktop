"""Run config/voxl/voxl.sh against fake vncserver and voxl executables."""

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


@pytest.fixture
def launcher(tmp_path):
    home = tmp_path / "home"
    (home / ".vnc").mkdir(parents=True)
    _write_executable(home / ".vnc" / "xstartup", "#!/bin/sh\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"

    # Refuses the first display it is offered, like a display another session
    # holds, then records the start and later kill of the display it accepted.
    _write_executable(
        bin_dir / "vncserver",
        "#!/usr/bin/env bash\n"
        f"log={log}\n"
        'if [ "$1" = "-kill" ]; then echo "kill $2" >> "$log"; exit 0; fi\n'
        'first=$(cat "$log.first" 2>/dev/null || true)\n'
        'if [ -z "$first" ]; then echo "$1" > "$log.first"; echo "busy $1" >> "$log"; exit 1; fi\n'
        'echo "start $*" >> "$log"\n',
    )
    _write_executable(bin_dir / "xset", "#!/bin/sh\n")
    voxl = _write_executable(
        tmp_path / "voxl",
        "#!/usr/bin/env bash\n"
        f"log={log}\n"
        'echo "voxl DISPLAY=$DISPLAY PASSWORD=$VOXL_PASSWORD $*" >> "$log"\n'
        'trap \'echo "voxl stopped" >> "$log"; exit 0\' TERM\n'
        "while :; do sleep 0.1; done\n",
    )
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "NEURODESKTOP_VOXL_BIN": str(voxl),
        "VOXL_PASSWORD": TOKEN,
    }
    return env, log, home


def _wait_for(log, text, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log.exists() and text in log.read_text():
            return log.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{text!r} not logged:\n{log.read_text() if log.exists() else ''}")


def test_streams_a_private_display_through_an_authenticated_loopback_voxl(launcher):
    env, log, home = launcher
    proc = subprocess.Popen(
        [str(repo_path("config/voxl/voxl.sh")), "45123"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        calls = _wait_for(log, "voxl DISPLAY=")
        start = next(line for line in calls.splitlines() if line.startswith("start "))
        display = start.split()[1]
        busy = next(line for line in calls.splitlines() if line.startswith("busy "))
        assert int(display[1:]) > int(busy.split()[1][1:]) >= 60

        # The X server offers RFB only on a private Unix socket.
        state_dir = home / ".neurodesk" / "runtime-voxl"
        assert "-rfbport -1" in start
        assert f"-rfbunixpath {state_dir}/rfb-{display[1:]}.sock -rfbunixmode 0600" in start
        assert oct(state_dir.stat().st_mode & 0o777) == "0o700"

        voxl = next(line for line in calls.splitlines() if line.startswith("voxl "))
        args = voxl.split()
        assert f"DISPLAY={display}" in args
        assert f"PASSWORD={TOKEN}" in args
        assert args[args.index("--addr") + 1] == "127.0.0.1"
        assert args[args.index("--port") + 1] == "45123"
        assert args[args.index("--file-transfers") + 1] == "none"
        assert not any(arg.startswith("--enable-basic-auth") for arg in args)

        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) != 0
        calls = log.read_text()
        assert "voxl stopped" in calls
        assert f"kill {display}" in calls
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_refuses_to_start_without_the_proxy_token(launcher):
    env, log, _home = launcher
    del env["VOXL_PASSWORD"]
    result = subprocess.run(
        [str(repo_path("config/voxl/voxl.sh")), "45123"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "VOXL_PASSWORD is empty" in result.stderr
    assert not log.exists()


def test_reports_when_no_display_can_start(launcher, tmp_path):
    env, log, home = launcher
    _write_executable(
        tmp_path / "bin" / "vncserver",
        "#!/usr/bin/env bash\necho 'Fatal server error: no screens' >&2\nexit 1\n",
    )
    result = subprocess.run(
        [str(repo_path("config/voxl/voxl.sh")), "45123"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "Could not start an X display for voxl" in result.stderr
    assert "no screens" in result.stderr
    assert not log.exists()
