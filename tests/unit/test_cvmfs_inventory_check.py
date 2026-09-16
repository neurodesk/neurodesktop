"""Behavioral tests for the scheduled CVMFS inventory health check."""

import os
import subprocess

import pytest

from testlib import repo_path


CHECKER = repo_path(".github/workflows/check_cvmfs_inventory.sh")
HEALTH_SCRIPTS = (
    repo_path(".github/workflows/test_cvmfs.sh"),
    repo_path(".github/workflows/test_cvmfs_1_2_3.sh"),
)


def _write_fake_wget(bin_dir):
    wget = bin_dir / "wget"
    wget.write_text(
        """#!/usr/bin/env bash
set -eu
output=""
for argument in "$@"; do
    case "$argument" in
        --output-document=*) output="${argument#*=}" ;;
    esac
done
if [[ "${FAKE_WGET_FAIL:-false}" == "true" ]]; then
    exit 8
fi
cp "$FAKE_INVENTORY" "$output"
"""
    )
    wget.chmod(0o755)


def _write_fake_cvmfs_talk(bin_dir):
    cvmfs_talk = bin_dir / "cvmfs_talk"
    cvmfs_talk.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_REFRESH_MARKER"
if [[ "$FAKE_REFRESH_STATUS" != 0 ]]; then exit "$FAKE_REFRESH_STATUS"; fi
if [[ -n "$FAKE_DISAPPEARS" ]]; then rm "$FAKE_REPOSITORY/containers/$FAKE_DISAPPEARS/commands.txt"; fi
# A refresh must not make the checker download a newer desired inventory.
printf 'replacement-inventory\\n' > "$FAKE_INVENTORY"
for name in $FAKE_REFRESH_CONTAINERS; do
    container="$FAKE_REPOSITORY/containers/$name"
    mkdir -p "$container"
    printf 'tool\\n' > "$container/commands.txt"
done
"""
    )
    cvmfs_talk.chmod(0o755)


def _run_checker(
    tmp_path,
    inventory,
    present=(),
    download_fails=False,
    refresh_catalog=False,
    appears_after_refresh=(),
    refresh_status=0,
    disappears_after_refresh="",
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    repository = tmp_path / "repository"
    repository.mkdir()
    inventory_file = tmp_path / "desired.log"
    inventory_file.write_text(inventory)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_wget(bin_dir)
    # Never invoke the host's sudo or CVMFS tools, even on a configured client.
    for name, script in {
        "sudo": '[[ "$1" == "-n" ]] || exit 99; shift; exec "$@"',
        "timeout": '''
[[ "$1" == "--kill-after=5s" ]] || exit 99
shift
case "$1 $2" in
    "10s cvmfs_config"|"45s cvmfs_talk") ;;
    *) exit 99 ;;
esac
shift
if [[ "$1" == cvmfs_talk && "$FAKE_REFRESH_STATUS" == 124 ]]; then
    exit 124
fi
exec "$@"
''',
        "cvmfs_config": 'echo "File Catalog Revision: 69976"',
        "cvmfs_talk": 'exit 1',
    }.items():
        tool = bin_dir / name
        tool.write_text("#!/usr/bin/env bash\n" + script + "\n")
        tool.chmod(0o755)
    refresh_marker = tmp_path / "refresh.log"
    if refresh_catalog:
        _write_fake_cvmfs_talk(bin_dir)
    for name in present:
        _add_container(repository, name)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "FAKE_INVENTORY": str(inventory_file),
            "CVMFS_REPOSITORY_ROOT": str(repository),
            "CVMFS_INVENTORY_URL": "https://example.invalid/cvmfs/log.txt",
            "FAKE_WGET_FAIL": "true" if download_fails else "false",
            "FAKE_REFRESH_CONTAINERS": " ".join(appears_after_refresh),
            "FAKE_REFRESH_MARKER": str(refresh_marker),
            "FAKE_REPOSITORY": str(repository),
            "FAKE_REFRESH_STATUS": str(refresh_status),
            "FAKE_DISAPPEARS": disappears_after_refresh,
        }
    )
    result = subprocess.run(
        ["bash", str(CHECKER)],
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )
    return result, repository, refresh_marker


def _add_container(repository, name):
    container = repository / "containers" / name
    container.mkdir(parents=True)
    (container / "commands.txt").write_text("tool\n")


def test_inventory_check_accepts_a_complete_snapshot(tmp_path):
    result, _, _ = _run_checker(
        tmp_path,
        "present-one_1.0_20260101 categories:testing,\n"
        "present-two_2.0_20260202 categories:testing,\n",
        present=("present-one_1.0_20260101", "present-two_2.0_20260202"),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validated 2 CVMFS inventory entries" in result.stdout


def test_inventory_check_reports_every_missing_container(tmp_path):
    result, _, _ = _run_checker(
        tmp_path,
        "present_1.0_20260101 categories:testing,\n"
        "missing-one_1.0_20260101 categories:testing,\n"
        "missing-two_2.0_20260202 categories:testing,",
        present=("present_1.0_20260101",),
    )

    assert result.returncode == 2
    assert "missing-one_1.0_20260101" in result.stdout
    assert "missing-two_2.0_20260202" in result.stdout
    assert "2 of 3 CVMFS inventory entries are missing" in result.stdout


def test_inventory_check_rejects_failed_or_empty_download(tmp_path):
    result, _, _ = _run_checker(tmp_path, "")
    assert result.returncode != 0
    assert "inventory download was empty" in result.stdout

    result, _, _ = _run_checker(
        tmp_path / "failed-download", "ignored\n", download_fails=True
    )

    assert result.returncode != 0
    assert "could not download CVMFS inventory" in result.stdout


@pytest.mark.parametrize(
    "invalid", [" missing", "\tmissing", " invalid/name", "../outside", ".", ".."]
)
def test_inventory_check_rejects_malformed_records(tmp_path, invalid):
    result, _, _ = _run_checker(
        tmp_path, f"present\n{invalid}\n", present=("present",)
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "invalid container identifier" in result.stdout


def test_inventory_check_refreshes_catalog_before_reporting_missing_entries(
    tmp_path,
):
    result, _, refresh_marker = _run_checker(
        tmp_path,
        "present_1.0_20260101 categories:testing,\n"
        "late_2.0_20260202 categories:testing,\n",
        present=("present_1.0_20260101",),
        refresh_catalog=True,
        appears_after_refresh=("late_2.0_20260202",),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "refreshing the catalog before the final check" in result.stdout
    assert "Validated 2 CVMFS inventory entries" in result.stdout
    assert "::error" not in result.stdout
    assert refresh_marker.read_text().splitlines() == [
        "-i repository remount sync"
    ]


def test_inventory_check_reports_persistent_mismatch_after_catalog_refresh(
    tmp_path,
):
    result, _, refresh_marker = _run_checker(
        tmp_path,
        "missing-one_1.0_20260101 categories:testing,\n"
        "missing-two_2.0_20260202 categories:testing,\n",
        refresh_catalog=True,
    )

    assert result.returncode == 2
    assert "missing-one_1.0_20260101" in result.stdout
    assert "missing-two_2.0_20260202" in result.stdout
    assert "2 of 2 CVMFS inventory entries are missing" in result.stdout
    assert refresh_marker.read_text().splitlines() == [
        "-i repository remount sync"
    ]


def test_health_jobs_share_the_inventory_checker():
    for script_path in HEALTH_SCRIPTS:
        script = script_path.read_text()
        assert "check_cvmfs_inventory.sh" in script
        assert 'check_cvmfs_inventory.sh" || exit $?' in script
        assert "done < log.txt" not in script


@pytest.mark.parametrize("status", [1, 124])
def test_failed_or_timed_out_refresh_still_fails_missing_inventory(tmp_path, status):
    result, _, _ = _run_checker(
        tmp_path, "missing\n", refresh_catalog=True, refresh_status=status
    )
    assert result.returncode == 2
    assert "refresh failed or timed out" in result.stdout
    assert "1 of 1 CVMFS inventory entries are missing" in result.stdout
    assert result.stdout.count("File Catalog Revision:") == 2


def test_refresh_rechecks_previously_present_entries(tmp_path):
    result, _, _ = _run_checker(
        tmp_path, "present\nlate\n", present=("present",), refresh_catalog=True,
        appears_after_refresh=("late",), disappears_after_refresh="present",
    )
    assert result.returncode == 2
    assert "mismatch::present is missing" in result.stdout
    assert "replacement-inventory" not in result.stdout


def test_complete_inventory_does_not_refresh(tmp_path):
    result, _, marker = _run_checker(
        tmp_path, "present\n", present=("present",), refresh_catalog=True
    )
    assert result.returncode == 0
    assert not marker.exists()
