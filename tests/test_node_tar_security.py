import json
from pathlib import Path

import pytest


PATCHED_TAR_VERSION = "7.5.19"
RUNTIME_TAR_PACKAGE_FILES = (
    Path("/opt/code-server/lib/vscode/node_modules/tar/package.json"),
    Path("/usr/lib/node_modules/npm/node_modules/tar/package.json"),
)


def _dockerfile_path():
    checkout_dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    if checkout_dockerfile.exists():
        return checkout_dockerfile
    packaged_dockerfile = Path("/opt/tests/Dockerfile")
    if packaged_dockerfile.exists():
        return packaged_dockerfile
    raise AssertionError("Dockerfile not found in checkout or packaged image tests")


def test_dockerfile_patches_every_bundled_node_tar_copy():
    dockerfile = _dockerfile_path().read_text(encoding="utf-8")

    assert dockerfile.count(
        f'ARG NODE_TAR_VERSION="{PATCHED_TAR_VERSION}"'
    ) == 1
    assert dockerfile.count('npm pack --silent "tar@${NODE_TAR_VERSION}"') == 1
    assert '/opt/code-server/lib/vscode/node_modules/tar' in dockerfile
    assert '$(npm root -g)/npm/node_modules/tar' in dockerfile
    for package_file in RUNTIME_TAR_PACKAGE_FILES:
        assert f'require("{package_file}").version' in dockerfile


@pytest.mark.parametrize("package_file", RUNTIME_TAR_PACKAGE_FILES)
def test_bundled_node_tar_runtime_version(package_file):
    if not package_file.exists():
        pytest.skip("bundled node-tar package is only available inside the image")

    package = json.loads(package_file.read_text(encoding="utf-8"))
    assert package["version"] == PATCHED_TAR_VERSION
