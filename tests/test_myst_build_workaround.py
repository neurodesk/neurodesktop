"""Regression tests for the jupyterlab-myst rebuild workaround in Dockerfile.

The workaround step rebuilds jupyterlab-myst against RISE's core path so that
@jupyterlab/markdownviewer is bundled into MyST's federated extension. These
tests guard the shell commands in that Dockerfile RUN so they are not accidentally
broken by future refactors.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"


@pytest.fixture
def dockerfile() -> str:
    text = DOCKERFILE.read_text()
    # Locate the MyST rebuild RUN
    start = text.find("RUN MYST_VERSION=")
    if start == -1:
        pytest.fail("MyST rebuild RUN not found in Dockerfile")
    # Dockerfile RUNs are backslash-continued lines; stop at the next blank line.
    end = text.find("\n\n", start)
    return text[start:end]


def test_myst_build_installs_ljharb_tsconfig_dev_dependency(dockerfile: str) -> None:
    """Node 24 / npm hoisting leaves @ljharb/tsconfig unavailable to packages that
    extend it (e.g. safe-regex-test, for-each). The rebuild must install it as a
    direct devDependency before invoking `jupyter labextension build`.
    """
    assert "npm install --save-dev @ljharb/tsconfig@0.3.2" in dockerfile


def test_myst_build_labextension_build_runs_after_tsconfig_install(dockerfile: str) -> None:
    """The @ljharb/tsconfig install must precede the webpack-based labextension build."""
    tsconfig_marker = "npm install --save-dev @ljharb/tsconfig@0.3.2"
    build_marker = "jupyter labextension build --core-path=/tmp/rise/app"
    assert dockerfile.find(tsconfig_marker) < dockerfile.find(build_marker)


def test_myst_build_copies_rebuilt_labextension(dockerfile: str) -> None:
    """After rebuilding, the labextension artifacts must replace the pip-installed
    copies in both the package directory and the JupyterLab app directory.
    """
    assert "cp -a /tmp/myst/jupyterlab_myst/labextension" in dockerfile
    assert "APP_MYST_DIR=/opt/conda/share/jupyter/labextensions/jupyterlab-myst" in dockerfile
    assert 'cp -a "${MYST_LABEXT_DIR}" "${APP_MYST_DIR}"' in dockerfile
