"""Regression tests for CVMFS endpoints shared by runtime and health checks."""

from testlib import repo_path, resolve_source


IHEP_HOST = "cvmfs-stratum-one.ihep.ac.cn"


def test_incomplete_ihep_replica_is_not_offered_or_marked_healthy():
    """Do not direct users to a replica that lacks published repository data."""
    selector = resolve_source(
        "/opt/neurodesktop/cvmfs_server_select.sh",
        "config/jupyter/cvmfs_server_select.sh",
    ).read_text(encoding="utf-8")
    workflow = repo_path(".github/workflows/test-cvmfs.yml").read_text(
        encoding="utf-8"
    )

    assert IHEP_HOST not in selector
    assert IHEP_HOST not in workflow
