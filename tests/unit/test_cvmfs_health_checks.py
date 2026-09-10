"""Behavioral tests for the scheduled CVMFS health-check scripts."""

import os
import subprocess
from pathlib import Path

import pytest

from testlib import repo_path


HEALTH_CHECKS = (
    repo_path(".github/workflows/test_cvmfs.sh"),
    repo_path(".github/workflows/test_cvmfs_1_2_3.sh"),
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _fake_health_check_commands(
    tmp_path: Path, wget_body: str
) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"

    _write_executable(fake_bin / "wget", wget_body)
    _write_executable(
        fake_bin / "sudo",
        'printf \'sudo %s\\n\' "$*" >> "$CVMFS_TEST_COMMAND_LOG"\n'
        "# tee must consume the public key/config input so pipefail sees success.\n"
        'if [ "${1:-}" = tee ]; then cat >/dev/null; fi\n'
        "exit 0",
    )
    for command in ("ls", "dig", "curl", "cvmfs_config", "cp"):
        _write_executable(
            fake_bin / command,
            f'printf \'{command} %s\\n\' "$*" >> "$CVMFS_TEST_COMMAND_LOG"',
        )

    return fake_bin, command_log


def _run_health_check(
    script: Path, tmp_path: Path, fake_bin: Path, command_log: Path
):
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CVMFS_TEST_COMMAND_LOG": str(command_log),
            "CVMFS_TEST_WGET_COUNT": str(tmp_path / "wget.count"),
            "RETRY_ATTEMPTS": "2",
            "RETRY_DELAY": "0",
        }
    )
    args = ["bash", str(script)]
    if script.name == "test_cvmfs.sh":
        args.append("stratum.example.test")
    return subprocess.run(
        args,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize("script", HEALTH_CHECKS, ids=lambda path: path.name)
def test_health_check_retries_a_transient_bootstrap_download(script, tmp_path):
    fake_bin, command_log = _fake_health_check_commands(
        tmp_path,
        """
count=$(cat "$CVMFS_TEST_WGET_COUNT" 2>/dev/null || echo 0)
count=$((count + 1))
printf '%s\\n' "$count" > "$CVMFS_TEST_WGET_COUNT"
if [ "$count" -eq 1 ]; then exit 23; fi
for ((i = 1; i <= $#; i++)); do
    if [ "${!i}" = -O ]; then
        next=$((i + 1))
        : > "${!next}"
    fi
done
""",
    )

    result = _run_health_check(script, tmp_path, fake_bin, command_log)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "retrying in 0s" in result.stderr
    assert (tmp_path / "wget.count").read_text(encoding="utf-8") == "3\n"
    commands = command_log.read_text(encoding="utf-8")
    assert "apt_install_retry.sh lsb-release" in commands
    assert "apt_install_retry.sh cvmfs" in commands
    assert "cvmfs-fuse3 cvmfs-libs" in commands
    assert "cvmfs_config setup" in commands


def test_single_server_check_keeps_unrelated_endpoint_probes_diagnostic(tmp_path):
    fake_bin, command_log = _fake_health_check_commands(
        tmp_path,
        """
for ((i = 1; i <= $#; i++)); do
    if [ "${!i}" = -O ]; then
        next=$((i + 1))
        : > "${!next}"
    fi
done
""",
    )
    _write_executable(fake_bin / "dig", "exit 6")
    _write_executable(fake_bin / "curl", "exit 7")

    result = _run_health_check(
        HEALTH_CHECKS[0], tmp_path, fake_bin, command_log
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "DNS diagnostic" in result.stdout
    assert result.stdout.count("download diagnostic failed") == 2


@pytest.mark.parametrize("script", HEALTH_CHECKS, ids=lambda path: path.name)
def test_health_check_stops_when_bootstrap_download_never_succeeds(
    script, tmp_path
):
    fake_bin, command_log = _fake_health_check_commands(tmp_path, "exit 28")

    result = _run_health_check(script, tmp_path, fake_bin, command_log)

    assert result.returncode == 28
    assert "failed after 2 attempts (exit 28); giving up" in result.stderr
    commands = command_log.read_text(encoding="utf-8")
    assert "apt_install_retry.sh lsb-release" in commands
    assert "apt_install_retry.sh cvmfs" not in commands
    assert "cvmfs_config setup" not in commands
