"""Contracts for opening `astra.yaml` from the file browser in the viewer.

Two halves make that gesture work. The `neurodesk_astra_view.serverext`
Jupyter server extension builds the graph JSON and serves the anywidget's own
frontend assets; the `neurodesk-launcher:astra-viewer` JupyterLab plugin
registers a pattern file type plus a default widget factory that consumes
them. The built-image side of this contract lives in
``tests/container/test_astra_view_image.py``.

The server-extension tests import ``jupyter_server`` and so skip on a bare
checkout; CI installs it (see ``.github/workflows/unit-tests.yml``) and the
image always has it.
"""

import json
import sys

import pytest

from testlib import repo_path


VIEWER = repo_path("extensions/astra-viewer")
LAUNCHER = repo_path("extensions/neurodesk-launcher")
FRONTEND = (LAUNCHER / "src/astraViewer.ts").read_text(encoding="utf-8")

sys.path.insert(0, str(VIEWER))


# ---------------------------------------------------------------------------
# Server extension


@pytest.fixture(scope="module")
def serverext():
    pytest.importorskip(
        "jupyter_server",
        reason="jupyter-server is not installed; see docs/testing.md",
    )
    from neurodesk_astra_view import serverext

    return serverext


def test_workspace_relative_paths_resolve_under_the_root(tmp_path, serverext):
    (tmp_path / "project").mkdir()
    (tmp_path / "project/astra.yaml").write_text("analysis: {}\n")

    resolved = serverext.resolve_confined(tmp_path, "project/astra.yaml")
    assert resolved == (tmp_path / "project/astra.yaml").resolve()

    # A file that does not exist yet still resolves: build_graph is the one
    # that turns a missing spec into a renderable invalid graph.
    missing = serverext.resolve_confined(tmp_path, "project/new.astra.yaml")
    assert missing.name == "new.astra.yaml"


def test_escaping_paths_are_rejected_before_anything_is_read(
    tmp_path, serverext
):
    for raw in ("", "/etc/passwd", "../outside.yaml", "project/../../up.yaml"):
        with pytest.raises(ValueError):
            serverext.resolve_confined(tmp_path, raw)


def test_a_symlink_out_of_the_root_is_rejected(tmp_path, serverext):
    outside = tmp_path / "outside"
    root = tmp_path / "root"
    outside.mkdir()
    root.mkdir()
    (outside / "astra.yaml").write_text("analysis: {}\n")
    (root / "link").symlink_to(outside)

    with pytest.raises(ValueError):
        serverext.resolve_confined(root, "link/astra.yaml")


def test_extension_point_and_config_enable_the_serverext_module(serverext):
    assert serverext._jupyter_server_extension_points() == [
        {"module": "neurodesk_astra_view.serverext"}
    ]

    config = json.loads(
        (
            VIEWER
            / "jupyter-config/jupyter_server_config.d/neurodesk_astra_view.json"
        ).read_text(encoding="utf-8")
    )
    assert config["ServerApp"]["jpserver_extensions"] == {
        "neurodesk_astra_view.serverext": True
    }


def test_the_config_ships_in_the_wheel_and_sdist():
    pyproject = (VIEWER / "pyproject.toml").read_text(encoding="utf-8")

    assert (
        '"jupyter-config/jupyter_server_config.d" = '
        '"etc/jupyter/jupyter_server_config.d"'
    ) in pyproject
    assert '"jupyter-config/**"' in pyproject
    assert '"jupyter-server>=2.0"' in pyproject


# ---------------------------------------------------------------------------
# JupyterLab plugin


def test_viewer_plugin_is_registered_alongside_the_launcher():
    index = (LAUNCHER / "src/index.ts").read_text(encoding="utf-8")

    assert "import astraViewerPlugin from './astraViewer';" in index
    assert (
        "export default [plugin, workspaceLinksPlugin, astraViewerPlugin, t3CodePlugin];"
        in index
    )
    assert "id: 'neurodesk-launcher:astra-viewer'" in FRONTEND


def test_image_build_cannot_reuse_stale_launcher_artifacts():
    """The wheel must compile the checkout, not ignored local build output."""
    package = json.loads((LAUNCHER / "package.json").read_text(encoding="utf-8"))
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    assert package["scripts"]["build"] == (
        "tsc --build --force && node build_ext.js"
    )
    assert package["scripts"]["build:prod"] == (
        "tsc --build --force && node build_ext.js"
    )
    for generated in (
        "/tmp/neurodesk-launcher/lib",
        "/tmp/neurodesk-launcher/neurodesk_launcher/labextension",
        "/tmp/neurodesk-launcher/tsconfig.tsbuildinfo",
    ):
        assert generated in dockerfile, generated


def test_frontend_is_fetched_from_the_server_extension_not_bundled():
    """The launcher must not carry a second copy of the viewer frontend."""
    package = json.loads((LAUNCHER / "package.json").read_text(encoding="utf-8"))
    assert "cytoscape" not in package["dependencies"]
    for name in ("index.ts", "workspaceLinks.ts", "astraViewer.ts"):
        source = (LAUNCHER / "src" / name).read_text(encoding="utf-8")
        assert "from 'cytoscape'" not in source, name
        assert "cytoscape.min" not in source, name

def test_ambiguity_is_still_refused_by_the_server(tmp_path):
    """The rule the frontend defers to has to actually be there."""
    from neurodesk_astra_view.manifest import RunManifestError, load_run

    (tmp_path / "run-manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "status.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RunManifestError, match="ambiguous"):
        load_run(tmp_path, project_root=tmp_path, universe_id="baseline")
