"""Regression tests for CVMFS endpoints shared by runtime and health checks."""

from testlib import repo_path, resolve_source


IHEP_HOST = "cvmfs-stratum-one.ihep.ac.cn"
IHEP_ENDPOINT = f"{IHEP_HOST}:8000"


def test_ihep_stratum_one_uses_its_published_service_port_everywhere():
    """Clients and monitoring must not silently probe IHEP on HTTP port 80."""
    selector = resolve_source(
        "/opt/neurodesktop/cvmfs_server_select.sh",
        "config/jupyter/cvmfs_server_select.sh",
    ).read_text(encoding="utf-8")
    workflow = repo_path(".github/workflows/test-cvmfs.yml").read_text(
        encoding="utf-8"
    )

    assert f"http://{IHEP_ENDPOINT}" in selector
    assert f'"{IHEP_ENDPOINT}"' in workflow

    assert f"http://{IHEP_HOST}\n" not in selector
    assert f'"{IHEP_HOST}"' not in workflow


def test_selector_helper_is_installed_beside_shell_entry_point():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    assert "install -m 0644 /tmp/jupyter/cvmfs_server_select.py /opt/neurodesktop/cvmfs_server_select.py" in dockerfile


def test_fnal_is_in_the_default_candidate_pool():
    selector = resolve_source(
        "/opt/neurodesktop/cvmfs_server_select.sh",
        "config/jupyter/cvmfs_server_select.sh",
    ).read_text(encoding="utf-8")
    assert "http://s1fnal-cvmfs.openhtc.io:8080" in selector
