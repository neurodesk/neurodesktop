"""Behavioral tests for the scheduled CVMFS inventory health check."""

import os
import subprocess

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


def _run_checker(tmp_path, inventory, present=(), download_fails=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    repository = tmp_path / "repository"
    repository.mkdir()
    inventory_file = tmp_path / "desired.log"
    inventory_file.write_text(inventory)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_wget(bin_dir)
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
        }
    )
    result = subprocess.run(
        ["bash", str(CHECKER)],
        capture_output=True,
        env=env,
        text=True,
    )
    return result, repository


def _add_container(repository, name):
    container = repository / "containers" / name
    container.mkdir(parents=True)
    (container / "commands.txt").write_text("tool\n")


def test_inventory_check_accepts_a_complete_snapshot(tmp_path):
    result, _ = _run_checker(
        tmp_path,
        "present-one_1.0_20260101 categories:testing,\n"
        "present-two_2.0_20260202 categories:testing,\n",
        present=("present-one_1.0_20260101", "present-two_2.0_20260202"),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validated 2 CVMFS inventory entries" in result.stdout


def test_inventory_check_reports_every_missing_container(tmp_path):
    result, _ = _run_checker(
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
    result, _ = _run_checker(tmp_path, "")
    assert result.returncode != 0
    assert "inventory download was empty" in result.stdout

    result, _ = _run_checker(
        tmp_path / "failed-download", "ignored\n", download_fails=True
    )

    assert result.returncode != 0
    assert "could not download CVMFS inventory" in result.stdout


def test_health_jobs_share_the_inventory_checker():
    for script_path in HEALTH_SCRIPTS:
        script = script_path.read_text()
        assert "check_cvmfs_inventory.sh" in script
        assert 'check_cvmfs_inventory.sh" || exit $?' in script
        assert "done < log.txt" not in script
