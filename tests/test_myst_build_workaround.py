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


def test_myst_build_has_safe_regex_test_tsconfig_fallback(dockerfile: str) -> None:
    """Node 24 can fail to resolve @ljharb/tsconfig from safe-regex-test's nested
    dependency tree. The build must create the sibling directory and copy the
    tsconfig before invoking `jupyter labextension build`.
    """
    assert "mkdir -p /tmp/myst/node_modules/safe-regex-test/node_modules/@ljharb/tsconfig" in dockerfile
    assert (
        "cp /tmp/myst/node_modules/@ljharb/tsconfig/tsconfig.json "
        "/tmp/myst/node_modules/safe-regex-test/node_modules/@ljharb/tsconfig/tsconfig.json"
    ) in dockerfile


def test_myst_build_fallback_runs_before_labextension_build(dockerfile: str) -> None:
    """The tsconfig fallback must precede the webpack-based labextension build."""
    fallback_marker = "safe-regex-test/node_modules/@ljharb/tsconfig/tsconfig.json"
    build_marker = "jupyter labextension build --core-path=/tmp/rise/app"
    assert dockerfile.find(fallback_marker) < dockerfile.find(build_marker)


def test_myst_build_copies_rebuilt_labextension(dockerfile: str) -> None:
    """After rebuilding, the labextension artifacts must replace the pip-installed
    copies in both the package directory and the JupyterLab app directory.
    """
    assert "cp -a /tmp/myst/jupyterlab_myst/labextension" in dockerfile
    assert "APP_MYST_DIR=/opt/conda/share/jupyter/labextensions/jupyterlab-myst" in dockerfile
    assert "cp -a \"${MYST_LABEXT_DIR}\" \"${APP_MYST_DIR}\"" in dockerfile
