import os
import subprocess

import pytest

from testlib import repo_path


HEALTH_SCRIPTS = ("test_cvmfs.sh", "test_cvmfs_1_2_3.sh")


def run_health(
    tmp_path, script, *, mounted=False, readable=True, diagnostic="ok", hang_probe=""
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    repository = tmp_path / "repository"
    container = repository / "containers" / "sample_1"
    if mounted:
        container.mkdir(parents=True)
        (container / "commands.txt").write_text("sample\n")
    calls = tmp_path / "calls"
    commands = {
        "sudo": """
[[ "$1" != -n ]] || shift
case "$1" in
  apt-get|dpkg|mkdir) exit 0 ;;
  tee) cat >/dev/null; exit 0 ;;
  cvmfs_config|systemctl|journalctl|tail) exec "$@" ;;
  *) exit 99 ;;
esac
""",
        "wget": """
for arg in "$@"; do
  case "$arg" in
    --output-document=*) echo inventory >> "$CALLS"; echo sample_1 > "${arg#*=}" ;;
  esac
done
""",
        "ls": """
[[ "$*" == "$CVMFS_REPOSITORY_ROOT" ]] || exit 99
if [[ "$HANG_PROBE" == ls ]]; then trap '' TERM; exec /bin/sleep 30; fi
exit "$READ_STATUS"
""",
        "findmnt": """
[[ "$*" == "--noheadings --mountpoint $CVMFS_REPOSITORY_ROOT --types fuse.cvmfs" ]] || exit 99
if [[ "$HANG_PROBE" == findmnt ]]; then trap '' TERM; exec /bin/sleep 30; fi
exit "$MOUNT_STATUS"
""",
        "dig": "exit 0",
        "curl": "exit 0",
        "cp": 'echo copy >> "$CALLS"',
        "sleep": 'echo "sleep $*" >> "$CALLS"',
        "cvmfs_talk": 'echo refresh >> "$CALLS"',
        "timeout": """
[[ "$1" == --kill-after=5s ]] || exit 99
shift
case "$1" in 10s|45s) ;; *) exit 99 ;; esac
shift
printf 'bounded %s\n' "$*" >> "$CALLS"
exec /usr/bin/timeout --kill-after=0.1s 0.1s "$@"
""",
    }
    for name in ("cvmfs_config", "systemctl", "journalctl", "tail"):
        commands[name] = """
printf '%s %s\n' "${0##*/}" "$*" >> "$CALLS"
case "$*" in setup|chksetup) exit 0 ;; esac
case "$DIAGNOSTIC" in
  fail) exit 37 ;;
  hang) trap '' TERM; exec /bin/sleep 30 ;;
esac
"""
    for name, body in commands.items():
        tool = bin_dir / name
        tool.write_text("#!/bin/bash\n" + body)
        tool.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        CVMFS_REPOSITORY_ROOT=str(repository),
        CVMFS_INVENTORY_REPLICA_RETRY="true",
        CALLS=str(calls),
        MOUNT_STATUS="0" if mounted else "1",
        READ_STATUS="0" if readable else "2",
        DIAGNOSTIC=diagnostic,
        HANG_PROBE=hang_probe,
    )
    result = subprocess.run(
        ["bash", str(repo_path(f".github/workflows/{script}")), "example.invalid"],
        env=env,
        capture_output=True,
        text=True,
        timeout=8,
    )
    return result, calls.read_text() if calls.exists() else ""


@pytest.mark.parametrize("script", HEALTH_SCRIPTS)
def test_unmounted_repository_stops_before_inventory(tmp_path, script):
    result, calls = run_health(tmp_path, script)
    assert result.returncode == 1
    assert result.stdout.count("::error title=CVMFS mount unavailable::") == 1
    assert "inventory" not in calls
    assert "copy" not in calls
    assert "sleep" not in calls
    assert "refresh" not in calls
    assert "cvmfs_config showconfig neurodesk.ardc.edu.au" in calls
    assert "cvmfs_config probe neurodesk.ardc.edu.au" in calls
    assert "systemctl" in calls
    assert "journalctl" in calls
    assert "tail" in calls


@pytest.mark.parametrize("script", HEALTH_SCRIPTS)
def test_mounted_repository_keeps_inventory_and_file_read(tmp_path, script):
    result, calls = run_health(tmp_path, script, mounted=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validated 1 CVMFS inventory entries." in result.stdout
    assert "inventory\n" in calls
    assert "copy\n" in calls
    assert "sleep" not in calls


@pytest.mark.parametrize("diagnostic", ("fail", "hang"))
def test_diagnostics_cannot_hide_or_hang_mount_failure(tmp_path, diagnostic):
    result, calls = run_health(tmp_path, "check_cvmfs_mount.sh", diagnostic=diagnostic)
    assert result.returncode == 1
    assert result.stdout.count("::error title=CVMFS mount unavailable::") == 1
    for command in ("cvmfs_config", "systemctl", "journalctl", "tail"):
        assert f"bounded sudo -n {command}" in calls


def test_unreadable_mount_is_not_healthy(tmp_path):
    result, calls = run_health(
        tmp_path, "check_cvmfs_mount.sh", mounted=True, readable=False
    )
    assert result.returncode == 1
    assert "::error title=CVMFS mount unavailable::" in result.stdout
    assert "probe" in calls


@pytest.mark.parametrize("probe", ("ls", "findmnt"))
def test_mount_probe_is_bounded(tmp_path, probe):
    result, calls = run_health(
        tmp_path, "check_cvmfs_mount.sh", mounted=True, hang_probe=probe
    )
    assert result.returncode == 1
    assert "::error title=CVMFS mount unavailable::" in result.stdout
    assert "probe" in calls
