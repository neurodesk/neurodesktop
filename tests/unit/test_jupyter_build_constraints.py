"""Guard isolated Python build dependencies for both Jupyter source builds."""

import tomllib

from testlib import repo_path


def test_launcher_and_image_share_compatible_build_dependencies():
    constraints = {
        line
        for line in repo_path("config/jupyter/build-constraints.txt").read_text().splitlines()
        if line and not line.startswith("#")
    }
    launcher = tomllib.loads(
        repo_path("extensions/neurodesk-launcher/pyproject.toml").read_text()
    )
    assert constraints <= set(launcher["build-system"]["requires"])
    hook = launcher["tool"]["hatch"]["build"]["hooks"]["jupyter-builder"]
    assert set(hook["dependencies"]) <= constraints

    dockerfile = repo_path("Dockerfile").read_text()
    markers = (
        '"jupyterlab-slurm @ file:///tmp/jupyterlab-slurm"',
        "npm_config_cache=/tmp/neurodesk-launcher-npm-cache",
    )
    for marker in markers:
        start = dockerfile.rfind("\nRUN ", 0, dockerfile.index(marker))
        layer = dockerfile[start:].split("\n\n", 1)[0]
        assert (
            "source=config/jupyter/build-constraints.txt,"
            "target=/tmp/build-constraints.txt,ro"
        ) in layer
        assert "pip install --build-constraint /tmp/build-constraints.txt" in layer
