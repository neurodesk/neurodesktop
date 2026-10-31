"""Check that the image serves its pinned JupyterLab rather than the base image's."""

import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

from packaging.version import Version


def test_jupyterlab_server_and_served_frontend_are_one_4_6_release():
    installed = importlib.metadata.version("jupyterlab")
    app_dir = subprocess.check_output(
        [sys.executable, "-m", "jupyterlab.labapp", "path"], text=True
    )
    static = Path(
        next(line for line in app_dir.splitlines() if line.startswith("Application directory:"))
        .split(":", 1)[1]
        .strip()
    ) / "static/package.json"

    assert Version("4.6.5") <= Version(installed) < Version("4.7")
    assert json.loads(static.read_text(encoding="utf-8"))["version"] == installed
