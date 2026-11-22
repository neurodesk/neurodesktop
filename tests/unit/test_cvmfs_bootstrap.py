"""Packaging contracts for the pinned CVMFS repository bootstrap."""

import os
import re
import subprocess

from testlib import repo_path


def _cvmfs_install_layer() -> str:
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    layers = re.findall(
        r"^[A-Z]+ .*?(?=^[A-Z]+ |\Z)", dockerfile, re.MULTILINE | re.DOTALL
    )
    matches = [
        layer
        for layer in layers
        if "cvmfs-release_${CVMFS_RELEASE_VERSION}" in layer
    ]
    assert len(matches) == 1, "expected one versioned CVMFS bootstrap download"
    return matches[0]


def test_cvmfs_bootstrap_download_uses_the_pinned_release(tmp_path):
    """A mutable latest alias must not select a package before validation."""
    layer = _cvmfs_install_layer()
    command = re.search(r"retry wget .*?(?= \\\n)", layer).group()
    output = tmp_path / "wget-arguments"
    driver = r'''
retry() { "$@"; }
wget() { printf '%s\n' "$@" > "$OUTPUT"; }
'''

    completed = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", driver + command],
        env={**os.environ, "CVMFS_RELEASE_VERSION": "9.8", "OUTPUT": str(output)},
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0, completed.stderr
    assert output.read_text(encoding="utf-8").splitlines() == [
        "-q",
        "https://cvmrepo.s3.cern.ch/cvmrepo/apt/pool/common/main/"
        "cvmfs-release_9.8_all.deb",
        "-O",
        "/tmp/cvmfs-release.deb",
    ]
    assert "cvmfs-release-latest" not in layer
    assert '${CVMFS_RELEASE_SHA256}  /tmp/cvmfs-release.deb' in layer
    assert 'dpkg-query -W -f=\'${Version}\' cvmfs-release' in layer
