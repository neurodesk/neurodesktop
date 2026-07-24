"""Regression tests for the jupyterlab-myst rebuild workaround in Dockerfile.

The workaround step rebuilds jupyterlab-myst against RISE's core path so that
@jupyterlab/markdownviewer is bundled into MyST's federated extension. These
tests guard the shell commands in that Dockerfile RUN so they are not accidentally
broken by future refactors.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGED_DOCKERFILE = Path(__file__).with_name("Dockerfile")
DOCKERFILE = (
    PACKAGED_DOCKERFILE if PACKAGED_DOCKERFILE.exists() else REPO_ROOT / "Dockerfile"
)


@pytest.fixture
def dockerfile() -> str:
    text = DOCKERFILE.read_text()
    # Locate the complete MyST rebuild block.
    start = text.find("# Workaround for jupyterlab-rise + jupyterlab-myst")
    if start == -1:
        pytest.fail("MyST rebuild RUN not found in Dockerfile")
    # Dockerfile RUNs are backslash-continued lines; stop at the next blank line.
    end = text.find("\n\n", start)
    return text[start:end]


def test_myst_build_uses_pinned_release_and_frozen_lockfile(dockerfile: str) -> None:
    """MyST 2.6.0 ships package-lock.json, so reproduce it with npm ci."""
    assert "jupyterlab_myst==2.6.0" in DOCKERFILE.read_text()
    assert "npm_config_cache=/tmp/myst-npm-cache npm ci" in dockerfile
    assert "npm install" not in dockerfile
    assert "pnpm" not in dockerfile
    assert "npx --yes" not in dockerfile


def test_myst_build_install_runs_before_labextension_build(dockerfile: str) -> None:
    """The frozen dependency install must precede the webpack-based build."""
    install_marker = "npm_config_cache=/tmp/myst-npm-cache npm ci"
    build_marker = "jupyter labextension build --core-path=/tmp/rise/app"
    assert dockerfile.find(install_marker) < dockerfile.find(build_marker)


def test_myst_build_does_not_copy_transitive_tsconfigs(dockerfile: str) -> None:
    """The build must not return to package-specific tsconfig workarounds."""
    assert "safe-regex-test/node_modules/@ljharb/tsconfig" not in dockerfile
    assert "khroma/node_modules/tsex" not in dockerfile


def test_myst_build_removes_temporary_package_manager_state(dockerfile: str) -> None:
    """The temporary npm cache must not remain in the image."""
    assert "/tmp/myst-npm-cache" in dockerfile
    cleanup = dockerfile.rsplit("rm -rf", maxsplit=1)[-1]
    assert "/tmp/myst-npm-cache" in cleanup


def test_myst_build_copies_rebuilt_labextension(dockerfile: str) -> None:
    """After rebuilding, the labextension artifacts must replace the pip-installed
    copies in both the package directory and the JupyterLab app directory.
    """
    assert "cp -a /tmp/myst/jupyterlab_myst/labextension" in dockerfile
    assert "APP_MYST_DIR=/opt/conda/share/jupyter/labextensions/jupyterlab-myst" in dockerfile
    assert "cp -a \"${MYST_LABEXT_DIR}\" \"${APP_MYST_DIR}\"" in dockerfile
