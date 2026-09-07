"""Exercise bounded subprocess handling in the subscription worker."""

import subprocess
import sys
import time

import pytest

from testlib import load_source_module


@pytest.fixture
def worker():
    return load_source_module(
        "agentic_process_test", "/opt/agentic_worker.py", ".github/scripts/agentic_worker.py"
    )


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_command_stops_a_process_that_exceeds_the_output_limit(worker, monkeypatch, stream):
    monkeypatch.setattr(worker, "MAX_COMMAND_OUTPUT", 1024)
    script = (
        "import sys, time; "
        f"sys.{stream}.write('x' * 65536); sys.{stream}.flush(); "
        "time.sleep(5)"
    )

    with pytest.raises(worker.CommandFailure, match="exited") as failure:
        worker.command([sys.executable, "-c", script], timeout=2)

    assert len(failure.value.output.encode()) <= 2 * worker.MAX_COMMAND_OUTPUT
    assert "output exceeded 1024 bytes" in failure.value.output


def test_command_rejects_a_fast_success_that_exceeds_the_output_limit(worker, monkeypatch):
    monkeypatch.setattr(worker, "MAX_COMMAND_OUTPUT", 1024)

    with pytest.raises(worker.CommandFailure):
        worker.command([sys.executable, "-c", "import os; os.write(1, b'x' * 4096)"])


def test_command_timeout_kills_descendants(worker, tmp_path):
    marker = tmp_path / "descendant-finished"
    child = f"import time; from pathlib import Path; time.sleep(0.25); Path({str(marker)!r}).write_text('alive')"
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep(5)"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        worker.command([sys.executable, "-c", parent], timeout=0.05)

    time.sleep(0.4)
    assert not marker.exists()


def test_command_failure_preserves_output_without_putting_it_in_the_exception(worker):
    secret = "sensitive-child-output"
    script = (
        "import sys; "
        "print('stdout detail'); "
        f"print({secret!r}, file=sys.stderr); "
        "raise SystemExit(7)"
    )

    with pytest.raises(worker.CommandFailure) as failure:
        worker.command([sys.executable, "-c", script])

    assert failure.value.output == f"stdout detail\n{secret}\n"
    assert secret not in str(failure.value)


def test_command_returns_stdout_and_accepts_stdin(worker):
    output = worker.command(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        data="hello",
    )

    assert output == "HELLO\n"


def test_command_binary_mode_preserves_non_utf8_output(worker):
    expected = b"\x00\xffbinary\x80output\n"

    output = worker.command(
        [sys.executable, "-c", f"import os; os.write(1, {expected!r})"],
        binary=True,
    )

    assert output == expected
