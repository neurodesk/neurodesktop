import os
import subprocess

import pytest

from testlib import resolve_source


SCRIPT = resolve_source("/usr/local/bin/retry", "scripts/retry.sh")


def run_retry(*args, **env_overrides):
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def test_retry_returns_the_last_command_status_after_all_attempts_fail():
    result = run_retry(
        "bash",
        "-c",
        "exit 42",
        RETRY_ATTEMPTS="2",
        RETRY_DELAY="0",
    )

    assert result.returncode == 42
    assert "attempt 1/2" in result.stderr
    assert "failed after 2 attempts (exit 42); giving up." in result.stderr


def test_retry_stops_after_a_command_succeeds(tmp_path):
    counter = tmp_path / "attempts"
    command = """
count=$(cat "$RETRY_TEST_COUNTER" 2>/dev/null || echo 0)
count=$((count + 1))
printf '%s\n' "$count" > "$RETRY_TEST_COUNTER"
[ "$count" -ge 3 ]
"""

    result = run_retry(
        "bash",
        "-c",
        command,
        RETRY_ATTEMPTS="4",
        RETRY_DELAY="0",
        RETRY_TEST_COUNTER=str(counter),
    )

    assert result.returncode == 0
    assert counter.read_text() == "3\n"
    assert result.stderr.count("retrying") == 2
    assert "giving up" not in result.stderr


def test_retry_requires_a_command():
    result = run_retry()

    assert result.returncode == 2
    assert "usage: retry <command> [args ...]" in result.stderr


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("RETRY_ATTEMPTS", "0"),
        ("RETRY_ATTEMPTS", "00"),
        ("RETRY_ATTEMPTS", "many"),
        ("RETRY_DELAY", "-1"),
        ("RETRY_DELAY", "soon"),
    ],
)
def test_retry_rejects_invalid_tunables(variable, value):
    env = {
        "RETRY_ATTEMPTS": "1",
        "RETRY_DELAY": "0",
    }
    env[variable] = value
    result = run_retry(
        "bash",
        "-c",
        "exit 99",
        **env,
    )

    assert result.returncode == 2
    assert f"{variable} must be" in result.stderr
