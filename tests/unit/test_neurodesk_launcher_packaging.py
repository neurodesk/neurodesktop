"""Packaging contracts for the frontend-only Neurodesk launcher."""

import json
import subprocess
import sys
import tomllib

from testlib import repo_path


LAUNCHER = repo_path("extensions/neurodesk-launcher")


def test_launcher_package_does_not_expose_unused_version_module():
    """Static build metadata must not leave a discoverable version module."""
    pyproject = tomllib.loads(
        (LAUNCHER / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["project"]["version"]

    probe = """
import json
import pkgutil
import sys

sys.path.insert(0, sys.argv[1])
import neurodesk_launcher

print(json.dumps(sorted(
    module.name for module in pkgutil.iter_modules(neurodesk_launcher.__path__)
)))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(LAUNCHER)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "_version" not in json.loads(result.stdout)


def test_launcher_source_has_one_package_manager_contract():
    """The image and checkout builds must share npm's locked dependency tree."""
    pyproject = tomllib.loads(
        (LAUNCHER / "pyproject.toml").read_text(encoding="utf-8")
    )
    build = pyproject["tool"]["hatch"]["build"]["hooks"]["jupyter-builder"]

    assert build["build-kwargs"]["npm"] == ["npm"]
    assert build["editable-build-kwargs"]["npm"] == ["npm"]
    assert (LAUNCHER / "package-lock.json").is_file()
    assert not (LAUNCHER / "yarn.lock").exists()
    assert not (LAUNCHER / ".yarnrc.yml").exists()
