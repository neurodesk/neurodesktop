"""Regression tests for CVMFS endpoints and health-check orchestration."""

import yaml

from testlib import repo_path, resolve_source


IHEP_HOST = "cvmfs-stratum-one.ihep.ac.cn"
IHEP_ENDPOINT = f"{IHEP_HOST}:8000"
ORIGIN = "stratum0.neurodesk.cloud.edu.au"
WORKFLOW_PATH = repo_path(".github/workflows/test-cvmfs.yml")


def test_ihep_stratum_one_uses_its_published_service_port_everywhere():
    """Clients and monitoring must not silently probe IHEP on HTTP port 80."""
    selector = resolve_source(
        "/opt/neurodesktop/cvmfs_server_select.sh",
        "config/jupyter/cvmfs_server_select.sh",
    ).read_text(encoding="utf-8")
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert f"http://{IHEP_ENDPOINT}" in selector
    assert f'"{IHEP_ENDPOINT}"' in workflow

    assert f"http://{IHEP_HOST}\n" not in selector
    assert f'"{IHEP_HOST}"' not in workflow


def test_replica_checks_wait_for_a_complete_authoritative_repository():
    """An unpublished desired artifact must produce one actionable failure."""
    jobs = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]
    origin_job = jobs["test_cvmfs_origin"]
    origin_step = next(
        step
        for step in origin_job["steps"]
        if step.get("name")
        == "check if the authoritative CVMFS repository is online and complete"
    )

    assert origin_step["run"].split()[-1] == ORIGIN
    assert "strategy" not in origin_job
    assert not origin_job.get("continue-on-error", False)
    assert not origin_step.get("continue-on-error", False)
    for job_name in ("test_cvmfs", "test_cvmfs_1_2_3"):
        job = jobs[job_name]
        assert job["needs"] == "test_cvmfs_origin"
        assert job.get("if", "success()") == "success()"

    replicas = jobs["test_cvmfs"]["strategy"]["matrix"]["cvmfs-servers"]
    assert ORIGIN not in replicas
    assert len(replicas) == len(set(replicas)) == 14
