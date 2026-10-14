"""Tests for .github/scripts/cvmfs_ci_cache.sh and its workflow wiring."""

import datetime
import getpass
import os
import subprocess

import pytest
import yaml

from testlib import repo_path

SCRIPT = repo_path(".github/scripts/cvmfs_ci_cache.sh")
CACHED_JOBS = [
    ("pr-image-validation.yml", "image"),
    ("build-neurodesktop.yml", "test-image"),
    ("build-neurodesktop-test.yml", "test-image"),
    ("build-neurodesktop-dev.yml", "test-image"),
]


@pytest.fixture
def cache(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    sudo.write_text('#!/bin/sh\nexec "$@"\n')
    sudo.chmod(0o755)
    (tmp_path / "config.d").mkdir()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "NEURODESKTOP_CVMFS_CI_CACHE_DIR": str(tmp_path / "alien"),
        "NEURODESKTOP_CVMFS_CONFIG_DIR": str(tmp_path / "config.d"),
        "NEURODESKTOP_CVMFS_USER": getpass.getuser(),
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
    }

    def run(*args):
        return subprocess.run(["bash", str(SCRIPT), *args], env=env, check=True,
                              capture_output=True, text=True, timeout=30).stdout

    return tmp_path, run


def test_init_keys_rotate_weekly_and_restore_any_run_from_that_week(cache):
    tmp_path, run = cache
    outputs = dict(line.split("=", 1) for line in run("init", "amd64").splitlines())
    year, week, _ = datetime.datetime.now(datetime.UTC).isocalendar()
    assert outputs["path"] == str(tmp_path / "alien")
    assert (tmp_path / "alien").is_dir()
    assert outputs["restore-prefix"] == f"neurodesk-cvmfs-alien-v2-amd64-{year}-W{week:02d}-"
    assert outputs["key"] == outputs["restore-prefix"] + "123-2"


def test_prepare_mounts_the_restored_objects_as_an_alien_cache(cache):
    tmp_path, run = cache
    run("init", "amd64")
    restored = tmp_path / "alien" / "ab" / "cdef"
    restored.parent.mkdir()
    restored.write_bytes(b"object")
    run("prepare")
    config = (tmp_path / "config.d" / "neurodesk.ardc.edu.au.local").read_text()
    assert config.splitlines() == [
        f"CVMFS_ALIEN_CACHE={tmp_path / 'alien'}",
        "CVMFS_QUOTA_LIMIT=-1",
        "CVMFS_SHARED_CACHE=no",
    ]
    assert restored.read_bytes() == b"object"


def test_export_saves_objects_without_unfinished_transfers(cache):
    tmp_path, run = cache
    run("init", "amd64")
    (tmp_path / "alien" / "txn").mkdir()
    (tmp_path / "alien" / "txn" / "partial").write_bytes(b"half")
    (tmp_path / "alien" / "ab").mkdir()
    (tmp_path / "alien" / "ab" / "cdef").write_bytes(b"object")
    run("export")
    assert list((tmp_path / "alien" / "txn").iterdir()) == []
    assert (tmp_path / "alien" / "ab" / "cdef").read_bytes() == b"object"


@pytest.mark.parametrize(("workflow_name", "job"), CACHED_JOBS)
def test_workflow_restores_before_mounting_and_saves_after_testing(workflow_name, job):
    workflow = yaml.safe_load(repo_path(f".github/workflows/{workflow_name}").read_text())
    steps = workflow["jobs"][job]["steps"]
    index = {step.get("id"): i for i, step in enumerate(steps) if step.get("id")}

    def position(fragment):
        return next(i for i, step in enumerate(steps) if fragment in step.get("run", ""))

    restore, save = steps[index["cvmfs-cache"]], next(
        step for step in steps if step.get("uses", "").startswith("actions/cache/save@"))
    assert index["cvmfs-cache-key"] < index["cvmfs-cache"] < index["cvmfs-cache-prepare"]
    assert index["cvmfs-cache-prepare"] < position("cvmfs_config setup")
    assert position("validate_image_runtime.sh") < position("cvmfs_ci_cache.sh export")
    assert position("cvmfs_ci_cache.sh export") < steps.index(save)
    assert restore["with"]["key"] == save["with"]["key"]
    assert restore["with"]["path"] == save["with"]["path"]
    assert "steps.cvmfs-cache-prepare.outcome == 'success'" in save["if"]
