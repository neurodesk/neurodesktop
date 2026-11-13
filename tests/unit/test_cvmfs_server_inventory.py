"""Regression tests for CVMFS endpoints and health-check orchestration."""

import os
import subprocess

import yaml

from testlib import repo_path, resolve_source


IHEP_HOST = "cvmfs-stratum-one.ihep.ac.cn"
IHEP_ENDPOINT = f"{IHEP_HOST}:8000"
RETIRED_IHEP_ALIAS = "s1ihep-cvmfs.openhtc.io"
ORIGIN = "stratum0.neurodesk.cloud.edu.au"
WORKFLOW_PATH = repo_path(".github/workflows/test-cvmfs.yml")


def _default_host_pool(tmp_path):
    selector = resolve_source(
        "/opt/neurodesktop/cvmfs_server_select.sh",
        "config/jupyter/cvmfs_server_select.sh",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "python3"
    stub.write_text('#!/bin/sh\nprintf "%s\\0" "$@" > "$ARGS_FILE"\n')
    stub.chmod(0o755)
    arguments_file = tmp_path / "arguments"
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "NEURODESKTOP_CVMFS_HOST_POOL"
    }
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        ARGS_FILE=str(arguments_file),
        NEURODESKTOP_CVMFS_TARGET_CONFIG=str(tmp_path / "repository.conf"),
        NEURODESKTOP_CVMFS_CACHE_FILE=str(tmp_path / "selection.env"),
    )
    subprocess.run(["bash", selector], env=env, check=True, timeout=30)
    arguments = arguments_file.read_text().split("\0")
    return set(arguments[arguments.index("--host-pool") + 1].split())


def test_ihep_stratum_one_uses_only_its_published_direct_endpoint(tmp_path):
    """Clients must avoid the stale CDN alias and use IHEP's direct service."""
    host_pool = _default_host_pool(tmp_path)
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    replicas = set(
        workflow["jobs"]["test_cvmfs"]["strategy"]["matrix"]["cvmfs-servers"]
    )

    assert f"http://{IHEP_ENDPOINT}" in host_pool
    assert IHEP_ENDPOINT in replicas

    assert f"http://{IHEP_HOST}" not in host_pool
    assert IHEP_HOST not in replicas
    assert f"http://{RETIRED_IHEP_ALIAS}" not in host_pool
    assert RETIRED_IHEP_ALIAS not in replicas


def test_replica_checks_wait_for_a_complete_authoritative_repository():
    """An unpublished desired artifact must produce one actionable failure."""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    retry_setting = "CVMFS_INVENTORY_REPLICA_RETRY"
    assert retry_setting not in workflow.get("env", {})
    origin_job = jobs["test_cvmfs_origin"]
    origin_step = next(
        step
        for step in origin_job["steps"]
        if step.get("name")
        == "check if the authoritative CVMFS repository is online and complete"
    )

    assert origin_step["run"].split()[-1] == ORIGIN
    assert "strategy" not in origin_job
    assert retry_setting not in origin_job.get("env", {})
    assert retry_setting not in origin_step.get("env", {})
    assert not origin_job.get("continue-on-error", False)
    assert not origin_step.get("continue-on-error", False)
    for job_name in ("test_cvmfs", "test_cvmfs_1_2_3"):
        job = jobs[job_name]
        assert job["needs"] == "test_cvmfs_origin"
        assert job["env"][retry_setting] == "true"
        assert job.get("if", "success()") == "success()"

    replicas = jobs["test_cvmfs"]["strategy"]["matrix"]["cvmfs-servers"]
    assert ORIGIN not in replicas
    assert len(replicas) == len(set(replicas))


def test_selector_helper_is_installed_beside_shell_entry_point():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    assert "install -m 0644 /tmp/jupyter/cvmfs_server_select.py /opt/neurodesktop/cvmfs_server_select.py" in dockerfile


def test_fnal_is_in_the_default_candidate_pool():
    selector = resolve_source(
        "/opt/neurodesktop/cvmfs_server_select.sh",
        "config/jupyter/cvmfs_server_select.sh",
    ).read_text(encoding="utf-8")
    assert any(line == "http://s1fnal-cvmfs.openhtc.io:8080" for line in selector.splitlines())
