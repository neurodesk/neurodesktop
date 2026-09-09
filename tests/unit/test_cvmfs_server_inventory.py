"""Regression tests for CVMFS endpoints shared by runtime and health checks."""

from pathlib import Path

from testlib import repo_path


IHEP_HOST = "cvmfs-stratum-one.ihep.ac.cn"
IHEP_ENDPOINT = f"{IHEP_HOST}:8000"


def _read(relative: str) -> str:
    return Path(repo_path(relative)).read_text(encoding="utf-8")


def test_ihep_stratum_one_uses_its_published_service_port_everywhere():
    """Clients and monitoring must not silently probe IHEP on HTTP port 80."""
    selector = _read("config/jupyter/cvmfs_server_select.sh")
    workflow = _read(".github/workflows/test-cvmfs.yml")

    assert f"http://{IHEP_ENDPOINT}" in selector
    assert f'"{IHEP_ENDPOINT}"' in workflow

    assert f"http://{IHEP_HOST}\n" not in selector
    assert f'"{IHEP_HOST}"' not in workflow
