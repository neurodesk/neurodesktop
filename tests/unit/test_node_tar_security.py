"""The Dockerfile must patch every bundled copy of node-tar.

The runtime half — that the patched version actually shipped — lives in
``tests/container/test_node_tar_runtime.py``.
"""

from pathlib import Path

from testlib import repo_path


PATCHED_TAR_VERSION = "7.5.22"
RUNTIME_TAR_PACKAGE_FILES = (
    Path("/opt/code-server/lib/vscode/node_modules/tar/package.json"),
    Path("/usr/lib/node_modules/npm/node_modules/tar/package.json"),
)


def test_dockerfile_patches_every_bundled_node_tar_copy():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.count(
        f'ARG NODE_TAR_VERSION="{PATCHED_TAR_VERSION}"'
    ) == 1
    assert dockerfile.count('npm pack --silent "tar@${NODE_TAR_VERSION}"') == 1
    assert '/opt/code-server/lib/vscode/node_modules/tar' in dockerfile
    assert '$(npm root -g)/npm/node_modules/tar' in dockerfile
    for package_file in RUNTIME_TAR_PACKAGE_FILES:
        assert f'require("{package_file}").version' in dockerfile


def test_node_tar_patch_strips_its_own_sourcemaps():
    """The late security patch must not undo the earlier image cleanup."""
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    patch_layer = dockerfile.split(
        'ARG NODE_TAR_VERSION="', maxsplit=1
    )[1].split("\n\n", maxsplit=1)[0]

    assert (
        'find "${node_tar_dir}" -type f \\( -name "*.js.map" '
        '-o -name "*.css.map" \\) -delete'
    ) in patch_layer
