"""Behavioral coverage for the earliest docker-stacks startup hook."""

import os
from pathlib import Path
import subprocess

import pytest

from testlib import resolve_source


START_NOTEBOOK = resolve_source(
    "/usr/local/bin/start-notebook.d/start_notebook.sh",
    "config/jupyter/start_notebook.sh",
)


def run_hook(
    tmp_path: Path,
    *,
    current_uid: int | None = None,
    current_gid: int | None = None,
    chown_succeeds: bool = True,
    restartable: str | None = None,
    apptainer: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    home = tmp_path / "home"
    home.mkdir()
    calls = tmp_path / "calls"
    target_uid = os.getuid()
    target_gid = os.getgid()
    current_uid = target_uid if current_uid is None else current_uid
    current_gid = target_gid if current_gid is None else current_gid
    driver = tmp_path / "run-hook.sh"
    driver.write_text(
        """#!/bin/bash
set -eu
stat() {
    test "$1" = -c
    case "$2" in
        %u) printf '%s\\n' "$CURRENT_UID" ;;
        %g) printf '%s\\n' "$CURRENT_GID" ;;
        %u:%g) printf '%s:%s\\n' "$CURRENT_UID" "$CURRENT_GID" ;;
        *) return 2 ;;
    esac
}
touch() {
    printf 'touch:%s\\n' "$*" >> "$CALLS"
    command touch "$@"
}
chown() {
    printf 'chown:%s\\n' "$*" >> "$CALLS"
    test "$CHOWN_SUCCEEDS" = 1
}
rm() {
    printf 'rm:%s\\n' "$*" >> "$CALLS"
    command rm "$@"
}
source "$START_NOTEBOOK"
printf 'RESULT:%s:%s:%s\\n' \
    "$RESTARTABLE" "${CHOWN_HOME-<unset>}" "${CHOWN_HOME_OPTS-<unset>}"
"""
    )
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "NB_UID": str(target_uid),
        "NB_GID": str(target_gid),
        "CURRENT_UID": str(current_uid),
        "CURRENT_GID": str(current_gid),
        "CHOWN_SUCCEEDS": "1" if chown_succeeds else "0",
        "CALLS": str(calls),
        "START_NOTEBOOK": str(START_NOTEBOOK),
    }
    if restartable is not None:
        env["RESTARTABLE"] = restartable
    if apptainer:
        env["APPTAINER_CONTAINER"] = "/tmp/container.sif"
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", str(driver)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, calls.read_text().splitlines() if calls.exists() else []


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(None, "yes"), ("no", "no")],
)
def test_restartable_default_is_nounset_safe_and_preserves_override(
    tmp_path, configured, expected
):
    result, calls = run_hook(tmp_path, restartable=configured)

    assert result.returncode == 0, result.stderr
    assert f"RESULT:{expected}:<unset>:<unset>" in result.stdout
    assert calls == []


def test_mismatched_home_requests_recursive_ownership_repair(tmp_path):
    result, calls = run_hook(
        tmp_path,
        current_uid=os.getuid() + 1,
        current_gid=os.getgid() + 1,
    )

    assert result.returncode == 0, result.stderr
    assert "RESULT:yes:yes:-R" in result.stdout
    assert [call.split(":", 1)[0] for call in calls] == ["touch", "chown", "rm"]
    assert not list((tmp_path / "home").glob(".neurodesktop-chown-probe-*"))


def test_unsupported_chown_warns_without_requesting_recursive_repair(tmp_path):
    result, calls = run_hook(
        tmp_path,
        current_uid=os.getuid() + 1,
        chown_succeeds=False,
    )

    assert result.returncode == 0, result.stderr
    assert "RESULT:yes:<unset>:<unset>" in result.stdout
    assert "[WARN] Skipping CHOWN_HOME" in result.stdout
    assert [call.split(":", 1)[0] for call in calls] == ["touch", "chown", "rm"]
    assert not list((tmp_path / "home").glob(".neurodesktop-chown-probe-*"))


def test_apptainer_home_never_attempts_ownership_repair(tmp_path):
    result, calls = run_hook(
        tmp_path,
        current_uid=os.getuid() + 1,
        current_gid=os.getgid() + 1,
        apptainer=True,
    )

    assert result.returncode == 0, result.stderr
    assert "RESULT:yes:<unset>:<unset>" in result.stdout
    assert calls == []
