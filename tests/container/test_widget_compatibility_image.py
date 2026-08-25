"""Installed widget-manager compatibility with server-side notebook execution."""

import importlib.metadata
import json
import re
import sys
from pathlib import Path


def test_widget_manager_waits_for_a_late_model_registration():
    """Yjs output may arrive before the matching kernel ``comm_open``.

    ``jupyter-server-documents`` delivers notebook output over a different
    websocket from widget comms.  The shipped frontend must therefore retry a
    missing model briefly instead of permanently rendering ``model not found``
    (or waiting forever at ``Loading widget...``).
    """
    assert importlib.metadata.version("ipywidgets") == "8.1.9"
    assert importlib.metadata.version("jupyterlab_widgets") == "3.0.17"

    labextension = (
        Path(sys.prefix)
        / "share/jupyter/labextensions/@jupyter-widgets/jupyterlab-manager"
    )
    package = json.loads((labextension / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == "5.0.16"

    bundles = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((labextension / "static").glob("*.js"))
    )
    late_model_retry = re.compile(
        r"async get_model\([^)]*\).*?Date\.now\(\).*?"
        r"setTimeout\([^,]+,100\).*?widget model not found",
        re.DOTALL,
    )
    assert late_model_retry.search(bundles), (
        "the installed widget manager still fails immediately when a widget "
        "view reaches the browser before its model"
    )
