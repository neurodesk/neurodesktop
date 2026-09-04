"""Regression tests for CVMFS setup in the image-build workflow."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/build-neurodesktop.yml").read_text()
INSTALLER = (ROOT / ".github/scripts/install_cvmfs.sh").read_text()


def test_cvmfs_setup_does_not_restore_stale_apt_packages():
    """Runner package updates must not conflict with cached apt metadata."""
    assert "cvmfs-contrib/github-action-cvmfs" not in WORKFLOW
    assert "run: .github/scripts/install_cvmfs.sh" in WORKFLOW
    assert "apt-get update" in INSTALLER
    assert "apt_cache" not in INSTALLER


def test_cvmfs_installer_checks_that_the_client_was_installed():
    assert "cvmfs-config-default" in INSTALLER
    assert "command -v cvmfs_config" in INSTALLER
