import os
import re
import subprocess

import pytest

from testlib import repo_path


HEALTH_SCRIPTS = ("test_cvmfs.sh", "test_cvmfs_1_2_3.sh")


def run_health(
    tmp_path,
    script,
    *,
    mounted=False,
    mount_type="fuse",
    mount_source="cvmfs2",
    readable=True,
    diagnostic="ok",
    hang_probe="",
    first_read="",
    first_mount=None,
    clock_advances=(),
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    repository = tmp_path / "repository"
    container = repository / "containers" / "sample_1"
    if mounted:
        container.mkdir(parents=True)
        (container / "commands.txt").write_text("sample\n")
    mount_table = tmp_path / "mounts"
    mount_table.write_text(
        f"{mount_source} {repository} {mount_type} defaults 0 0\n" if mounted else ""
    )
    first_mount_table = tmp_path / "first-mounts"
    if first_mount:
        first_type, first_source = first_mount
        first_mount_table.write_text(
            f"{first_source} {repository} {first_type} defaults 0 0\n"
        )
    calls = tmp_path / "calls"
    commands = {
        "sudo": """
printf 'sudo %s\n' "$*" >> "$CALLS"
[[ "$1" != -n ]] || shift
case "$1" in
  apt-get|dpkg|mkdir) exit 0 ;;
  tee) cat >/dev/null; exit 0 ;;
  timeout|cvmfs_config|systemctl|journalctl|tail) exec "$@" ;;
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
echo read >> "$CALLS"
if [[ ! -e "$CALLS.read" ]]; then
  : > "$CALLS.read"
  case "$FIRST_READ" in
    fail) exit 2 ;;
    timeout) exit 124 ;;
  esac
fi
if [[ "$HANG_PROBE" == ls ]]; then trap '' TERM; exec /bin/sleep 30; fi
exit "$READ_STATUS"
""",
        "findmnt": """
if [[ "$HANG_PROBE" == findmnt ]]; then trap '' TERM; exec /bin/sleep 30; fi
table="$MOUNT_TABLE"
if [[ ! -e "$CALLS.mount" && -e "$FIRST_MOUNT_TABLE" ]]; then
  table="$FIRST_MOUNT_TABLE"
fi
: > "$CALLS.mount"
/usr/bin/findmnt --fstab --tab-file "$table" "$@" || exit $?
echo verified >> "$CALLS"
""",
        "dig": "exit 0",
        "curl": "exit 0",
        "cp": 'echo copy >> "$CALLS"',
        "sleep": 'echo "sleep $*" >> "$CALLS"',
        "cvmfs_talk": 'echo refresh >> "$CALLS"',
        "timeout": """
[[ "$1" == --kill-after=5s ]] || exit 99
shift
[[ "$1" =~ ^[1-9][0-9]*s$ ]] || exit 99
duration="${1%s}"
shift
case "$1" in
  ls) (( duration <= 45 )) || exit 99 ;;
  *) (( duration <= 10 )) || exit 99 ;;
esac
printf 'limit %s %s\n' "$duration" "$1" >> "$CALLS"
printf 'bounded %s\n' "$*" >> "$CALLS"
real_limit=5s
case "$1" in
  ls|findmnt) [[ "$HANG_PROBE" != "$1" ]] || real_limit=0.1s ;;
  cvmfs_config|systemctl|journalctl|tail) [[ "$DIAGNOSTIC" != hang ]] || real_limit=0.1s ;;
esac
exec /usr/bin/timeout --kill-after=0.1s "$real_limit" "$@"
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
    clock = tmp_path / "clock.sh"
    clock.write_text(
        """
clock_step=0
timeout() {
  command timeout "$@"
  local status=$?
  local advances=($CLOCK_ADVANCES)
  SECONDS=$((SECONDS + ${advances[clock_step]:-0}))
  clock_step=$((clock_step + 1))
  return "$status"
}
sleep() {
  command sleep "$@"
  SECONDS=$((SECONDS + $1))
}
"""
    )
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        CVMFS_REPOSITORY_ROOT=str(repository),
        CVMFS_INVENTORY_REPLICA_RETRY="true",
        CALLS=str(calls),
        MOUNT_TABLE=str(mount_table),
        FIRST_MOUNT_TABLE=str(first_mount_table),
        FIRST_READ=first_read,
        BASH_ENV=str(clock),
        CLOCK_ADVANCES=" ".join(str(value) for value in clock_advances),
        READ_STATUS="0" if readable else "2",
        DIAGNOSTIC=diagnostic,
        HANG_PROBE=hang_probe,
    )
    result = subprocess.run(
        ["bash", str(repo_path(f".github/workflows/{script}")), "example.invalid"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
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
    assert calls.count("read\n") == 2
    assert "refresh" not in calls
    assert "cvmfs_config showconfig neurodesk.ardc.edu.au" in calls
    assert "cvmfs_config probe neurodesk.ardc.edu.au" in calls
    assert "systemctl" in calls
    assert "journalctl" in calls
    assert "tail" in calls


@pytest.mark.parametrize("script", HEALTH_SCRIPTS)
@pytest.mark.parametrize("mount_type", ("fuse", "fuse.cvmfs"))
def test_mounted_repository_keeps_inventory_and_file_read(tmp_path, script, mount_type):
    result, calls = run_health(tmp_path, script, mounted=True, mount_type=mount_type)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validated 1 CVMFS inventory entries." in result.stdout
    assert "inventory\n" in calls
    assert "copy\n" in calls
    assert "sleep" not in calls
    assert calls.count("read\n") == 1
    assert calls.index("verified\n") < calls.index("inventory\n")


@pytest.mark.parametrize("diagnostic", ("fail", "hang"))
def test_diagnostics_cannot_hide_or_hang_mount_failure(tmp_path, diagnostic):
    result, calls = run_health(tmp_path, "check_cvmfs_mount.sh", diagnostic=diagnostic)
    assert result.returncode == 1
    assert result.stdout.count("::error title=CVMFS mount unavailable::") == 1
    commands = (
        "cvmfs_config showconfig neurodesk.ardc.edu.au",
        "cvmfs_config probe neurodesk.ardc.edu.au",
        "cvmfs_config stat -v neurodesk.ardc.edu.au",
        "systemctl --no-pager --full status autofs",
        "journalctl --no-pager -n 100 -u autofs",
        "journalctl --no-pager -n 100 -t cvmfs2",
        "tail -n 100 /var/log/syslog",
    )
    sudo_calls = [call for call in calls.splitlines() if call.startswith("sudo ")]
    assert len(sudo_calls) == len(commands)
    assert set(sudo_calls) == {
        f"sudo -n timeout --kill-after=5s 10s {command}" for command in commands
    }
    for command in commands:
        assert f"bounded {command}\n" in calls


@pytest.mark.parametrize(
    ("mount_type", "mount_source"),
    (
        ("autofs", "cvmfs2"),
        ("ext4", "cvmfs2"),
        ("fuse", "other-filesystem"),
        ("fuse.cvmfs", "other-filesystem"),
    ),
)
@pytest.mark.parametrize("script", HEALTH_SCRIPTS)
def test_non_cvmfs_mount_is_not_healthy(tmp_path, mount_type, mount_source, script):
    result, calls = run_health(
        tmp_path,
        script,
        mounted=True,
        mount_type=mount_type,
        mount_source=mount_source,
    )
    assert result.returncode == 1
    assert result.stdout.count("::error title=CVMFS mount unavailable::") == 1
    assert calls.count("read\n") == 2
    assert "probe" in calls
    assert "inventory" not in calls
    assert "copy" not in calls


@pytest.mark.parametrize("script", HEALTH_SCRIPTS)
def test_unreadable_mount_is_not_healthy(tmp_path, script):
    result, calls = run_health(
        tmp_path, script, mounted=True, readable=False
    )
    assert result.returncode == 1
    assert result.stdout.count("::error title=CVMFS mount unavailable::") == 1
    assert calls.count("read\n") == 2
    assert "probe" in calls
    assert "inventory" not in calls
    assert "copy" not in calls


@pytest.mark.parametrize("probe", ("ls", "findmnt"))
def test_mount_probe_is_bounded(tmp_path, probe):
    result, calls = run_health(
        tmp_path, "check_cvmfs_mount.sh", mounted=True, hang_probe=probe
    )
    assert result.returncode == 1
    assert "::error title=CVMFS mount unavailable::" in result.stdout
    status_field = "read_status" if probe == "ls" else "findmnt_status"
    assert re.search(rf"{status_field}=(124|137)\b", result.stdout)
    assert "probe" in calls


@pytest.mark.parametrize("script", HEALTH_SCRIPTS)
@pytest.mark.parametrize("first_read", ("fail", "timeout"))
def test_initial_read_failure_recovers_before_inventory(tmp_path, script, first_read):
    result, calls = run_health(
        tmp_path, script, mounted=True, first_read=first_read
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::error" not in result.stdout
    assert "::warning" in result.stdout
    assert "recovered" in result.stdout.lower()
    status = 2 if first_read == "fail" else 124
    assert re.search(rf"attempt=1 read_status={status} .*elapsed=\d+s", result.stdout)
    assert re.search(r"attempt=2 read_status=0 findmnt_status=0 .*elapsed=\d+s", result.stdout)
    assert calls.count("read\n") == 2
    assert calls.index("sleep 61\n") < calls.rindex("read\n")
    assert calls.index("verified\n") < calls.index("inventory\n")
    assert "copy\n" in calls
    assert "refresh" not in calls
    assert "cvmfs_config probe" not in calls


@pytest.mark.parametrize("first_mount", (("autofs", "cvmfs2"), ("fuse", "other")))
def test_mount_verification_can_recover(tmp_path, first_mount):
    result, calls = run_health(
        tmp_path, "test_cvmfs.sh", mounted=True, first_mount=first_mount
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::error" not in result.stdout
    assert "::warning" in result.stdout
    assert re.search(r"attempt=1 read_status=0 findmnt_status=1 .*elapsed=\d+s", result.stdout)
    assert calls.count("read\n") == 2
    assert "sleep" not in calls
    assert calls.index("verified\n") < calls.index("inventory\n")


@pytest.mark.parametrize("clock_advances", ((50, 15, 105), (50, 15, 110), (50, 30, 90)))
def test_verification_deadline_reserves_kill_grace_and_clock_rounding(tmp_path, clock_advances):
    result, calls = run_health(
        tmp_path,
        "check_cvmfs_mount.sh",
        mounted=True,
        first_mount=("autofs", "cvmfs2"),
        clock_advances=clock_advances,
    )
    limits = [
        (int(duration), command)
        for duration, command in re.findall(r"^limit (\d+) (ls|findmnt)$", calls, re.M)
    ]
    assert [command for _, command in limits[:3]] == ["ls", "findmnt", "ls"]
    assert limits[2][0] <= min(45, 174 - sum(clock_advances[:2]))
    if sum(clock_advances) == 170:
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(limits) == 4
        assert limits[-1][1] == "findmnt"
        assert 0 < limits[-1][0] <= 4
    else:
        assert result.returncode == 1
        assert len(limits) == 3
        assert result.stdout.count("::error title=CVMFS mount unavailable::") == 1
        assert "cvmfs_config probe" in calls


def test_exhausted_deadline_does_not_start_another_read(tmp_path):
    result, calls = run_health(
        tmp_path,
        "check_cvmfs_mount.sh",
        mounted=True,
        first_read="fail",
        clock_advances=(174,),
    )
    assert result.returncode == 1
    assert calls.count("read\n") == 1
    assert "sleep" not in calls
    assert "bounded findmnt" not in calls
    assert result.stdout.count("::error title=CVMFS mount unavailable::") == 1
    assert "cvmfs_config probe" in calls


def test_mount_recovery_has_no_privileged_effects(tmp_path):
    result, calls = run_health(
        tmp_path, "check_cvmfs_mount.sh", mounted=True, first_read="fail"
    )
    assert result.returncode == 0
    assert "sudo " not in calls
    assert "systemctl restart" not in calls


@pytest.mark.parametrize(
    ("first_read_seconds", "retry_limit"), ((45, 45), (103, 10), (104, None))
)
def test_negative_cache_wait_fits_verification_budget(
    tmp_path, first_read_seconds, retry_limit
):
    result, calls = run_health(
        tmp_path,
        "check_cvmfs_mount.sh",
        mounted=True,
        first_read="timeout",
        clock_advances=(first_read_seconds,),
    )
    if retry_limit:
        assert calls.count("sleep 61\n") == 1
        limits = re.findall(r"^limit (\d+) ls$", calls, re.M)
        # Bash's whole-second clock may tick once between steps.
        assert limits[0] == "45"
        assert int(limits[1]) in (retry_limit - 1, retry_limit)
    else:
        assert result.returncode == 1
        assert "sleep" not in calls
        assert calls.count("read\n") == 1
        assert result.stdout.count("::error title=CVMFS mount unavailable::") == 1
