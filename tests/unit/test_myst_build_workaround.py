"""Regression tests for the jupyterlab-myst rebuild workaround in Dockerfile.

The workaround step rebuilds jupyterlab-myst against RISE's core path so that
@jupyterlab/markdownviewer is bundled into MyST's federated extension. These
tests guard the shell commands in that Dockerfile RUN so they are not accidentally
broken by future refactors.
"""

import json
from pathlib import Path

import pytest

from testlib import repo_path

DOCKERFILE = repo_path("Dockerfile")


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
    """MyST 2.7.0 ships pnpm-lock.yaml, so reproduce it with pinned pnpm."""
    assert "jupyterlab_myst==2.7.0" in DOCKERFILE.read_text()
    assert 'ARG MYST_PNPM_VERSION="11.21.0"' in dockerfile
    assert "pnpm@${MYST_PNPM_VERSION} install --frozen-lockfile" in dockerfile
    assert "npm install" not in dockerfile
    assert "npx --yes" not in dockerfile


def test_myst_build_install_runs_before_labextension_build(dockerfile: str) -> None:
    """The frozen dependency install must precede the webpack-based build."""
    install_marker = "pnpm@${MYST_PNPM_VERSION} install --frozen-lockfile"
    build_marker = "jupyter labextension build --core-path=/tmp/rise/app"
    assert dockerfile.find(install_marker) < dockerfile.find(build_marker)


def test_myst_build_updates_ydoc_for_jupyterlab_46(dockerfile: str) -> None:
    """MyST 2.7's ydoc 3 dependency must be rebuilt for JupyterLab's ydoc 4."""
    assert 'ARG MYST_YDOC_VERSION="4.1.1"' in dockerfile
    assert 'pnpm@${MYST_PNPM_VERSION} add --save-exact "@jupyter/ydoc@${MYST_YDOC_VERSION}"' in dockerfile
    assert 'dependencies.@jupyter/ydoc=^4.0.0' not in dockerfile


def test_myst_build_keeps_ydoc_exact_during_dependency_refresh(dockerfile: str) -> None:
    """The exact add must update the manifest and lockfile before the build."""
    exact_add_marker = 'pnpm@${MYST_PNPM_VERSION} add --save-exact "@jupyter/ydoc@${MYST_YDOC_VERSION}"'
    build_marker = "pnpm@${MYST_PNPM_VERSION} run build:css"
    assert "CI=true COREPACK_HOME=/tmp/myst-corepack" in dockerfile
    assert dockerfile.find(exact_add_marker) < dockerfile.find(build_marker)
    assert "install --no-frozen-lockfile" not in dockerfile


def test_myst_build_does_not_copy_transitive_tsconfigs(dockerfile: str) -> None:
    """The build must not return to package-specific tsconfig workarounds."""
    assert "safe-regex-test/node_modules/@ljharb/tsconfig" not in dockerfile
    assert "khroma/node_modules/tsex" not in dockerfile


def test_myst_build_removes_temporary_package_manager_state(dockerfile: str) -> None:
    """The temporary pnpm and Corepack state must not remain in the image."""
    assert "/tmp/myst-corepack" in dockerfile
    assert "/tmp/myst-pnpm-store" in dockerfile
    cleanup = dockerfile.rsplit("rm -rf", maxsplit=1)[-1]
    assert "/tmp/myst-corepack" in cleanup
    assert "/tmp/myst-pnpm-store" in cleanup


def test_myst_build_copies_rebuilt_labextension(dockerfile: str) -> None:
    """After rebuilding, the labextension artifacts must replace the pip-installed
    copies in both the package directory and the JupyterLab app directory.
    """
    assert "cp -a /tmp/myst/jupyterlab_myst/labextension" in dockerfile
    assert "APP_MYST_DIR=/opt/conda/share/jupyter/labextensions/jupyterlab-myst" in dockerfile
    assert "cp -a \"${MYST_LABEXT_DIR}\" \"${APP_MYST_DIR}\"" in dockerfile


def test_legacy_mathjax3_frontend_is_not_exposed_to_jupyterlab():
    """The MyST/RISE rebuild must retire the mathjax3 labextension.

    Absorbed from the former test_myst_rise_build.py, whose other assertions
    duplicated the pin and lockfile checks above.
    """
    dockerfile = DOCKERFILE.read_text()
    legacy_extension = (
        "/opt/conda/share/jupyter/labextensions/"
        "@jupyterlab/mathjax3-extension"
    )
    assert f'rm -rf "{legacy_extension}"' in dockerfile

    app_package = Path("/opt/conda/share/jupyter/lab/static/package.json")
    if app_package.exists():
        assert not Path(legacy_extension).exists()
        dependencies = json.loads(app_package.read_text())["dependencies"]
        assert "@jupyterlab/mathjax-extension" in dependencies
