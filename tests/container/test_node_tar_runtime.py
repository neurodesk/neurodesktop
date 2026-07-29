"""The image must ship the patched node-tar in every bundled copy.

The Dockerfile side of this contract — that each copy is actually patched
during the build — is asserted in ``tests/unit/test_node_tar_security.py``.
"""

import json
from pathlib import Path

import pytest


PATCHED_TAR_VERSION = "7.5.22"
RUNTIME_TAR_PACKAGE_FILES = (
    Path("/opt/code-server/lib/vscode/node_modules/tar/package.json"),
    Path("/usr/lib/node_modules/npm/node_modules/tar/package.json"),
)


@pytest.mark.parametrize("package_file", RUNTIME_TAR_PACKAGE_FILES)
def test_bundled_node_tar_runtime_version(package_file):
    assert package_file.exists(), f"bundled node-tar copy missing: {package_file}"

    package = json.loads(package_file.read_text(encoding="utf-8"))
    assert package["version"] == PATCHED_TAR_VERSION
