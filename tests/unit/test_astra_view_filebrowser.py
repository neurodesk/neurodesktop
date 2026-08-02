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
import re
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


def test_served_frontend_is_exactly_the_anywidget_frontend(serverext):
    """One frontend, two transports: nothing for the two viewers to drift on."""
    static = VIEWER / "neurodesk_astra_view/static"
    expected_esm = (static / "index.js").read_text(encoding="utf-8")

    assets = serverext._assets()
    assert assets["esm"][1] == expected_esm
    assert assets["css"][1] == (static / "style.css").read_text(encoding="utf-8")
    assert assets["esm"][0].startswith("text/javascript")
    assert assets["css"][0].startswith("text/css")


def test_both_handlers_require_an_authenticated_request(serverext):
    for handler in (serverext.AstraGraphHandler, serverext.AstraAssetHandler):
        # tornado's @web.authenticated wraps with functools.wraps, so the
        # undecorated method is reachable as __wrapped__.
        assert hasattr(handler.get, "__wrapped__"), handler.__name__


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


def frontend_pattern() -> str:
    match = re.search(
        r"export const ASTRA_FILENAME_PATTERN = '([^']+)';", FRONTEND
    )
    assert match, "ASTRA_FILENAME_PATTERN not found in astraViewer.ts"
    # The TypeScript literal escapes backslashes once.
    return match.group(1).replace("\\\\", "\\")


def test_viewer_plugin_is_registered_alongside_the_launcher():
    index = (LAUNCHER / "src/index.ts").read_text(encoding="utf-8")

    assert "import astraViewerPlugin from './astraViewer';" in index
    assert (
        "export default [plugin, workspaceLinksPlugin, astraViewerPlugin];"
        in index
    )
    assert "id: 'neurodesk-launcher:astra-viewer'" in FRONTEND


def test_filename_pattern_claims_astra_specs_and_nothing_else():
    """`name.match(pattern)` in the registry is an unanchored search."""
    pattern = re.compile(frontend_pattern())

    for name in ("astra.yaml", "astra.yml", "bet.astra.yaml", "bet.astra.yml"):
        assert pattern.search(name), name
    for name in (
        "config.yaml",
        "myastra.yaml",
        "astra.yaml.bak",
        "astra.json",
        "universes/bet-f-0-5.yaml",
    ):
        assert not pattern.search(name), name


def test_file_type_matches_by_pattern_only():
    """An extensions entry would claim every ordinary .yaml file."""
    assert "extensions: []," in FRONTEND
    assert "pattern: ASTRA_FILENAME_PATTERN," in FRONTEND


def test_factory_is_the_default_and_read_only_for_astra_specs():
    """Double-click renders; editing stays on Open With > Editor.

    `defaultFor` on the pattern file type is also what routes agent-authored
    chat links here: the workspace-links plugin opens with the `'default'`
    factory, and a missing viewer degrades to the text editor.
    """
    assert "export const ASTRA_FACTORY = 'ASTRA Viewer';" in FRONTEND
    assert "fileTypes: [ASTRA_FILE_TYPE]," in FRONTEND
    assert "defaultFor: [ASTRA_FILE_TYPE]," in FRONTEND
    assert "readOnly: true" in FRONTEND


def test_frontend_is_fetched_from_the_server_extension_not_bundled():
    """The launcher must not carry a second copy of the viewer frontend."""
    package = json.loads((LAUNCHER / "package.json").read_text(encoding="utf-8"))
    assert "cytoscape" not in package["dependencies"]
    for name in ("index.ts", "workspaceLinks.ts", "astraViewer.ts"):
        source = (LAUNCHER / "src" / name).read_text(encoding="utf-8")
        assert "from 'cytoscape'" not in source, name
        assert "cytoscape.min" not in source, name

    assert "'neurodesk-astra-view'" in FRONTEND
    assert "'graph'" in FRONTEND
    assert "'asset'" in FRONTEND
    # The served ESM must be imported at runtime, not resolved by webpack.
    assert "import(/* webpackIgnore: true */ blobUrl)" in FRONTEND
    assert "URL.revokeObjectURL(blobUrl)" in FRONTEND


def test_requests_go_through_the_authenticated_server_connection():
    assert "ServerConnection.makeRequest" in FRONTEND
    assert "fetch(" not in FRONTEND.replace("ServerConnection.makeRequest", "")


def test_a_save_from_the_shared_context_rerenders_the_graph():
    assert "context.fileChanged.connect(this._onFileChanged, this);" in FRONTEND
    assert (
        "this._context.fileChanged.disconnect(this._onFileChanged, this);"
        in FRONTEND
    )


def test_a_failed_asset_load_is_forgotten_so_a_later_open_can_retry():
    assert "viewerModulePromise = null;" in FRONTEND


# ---------------------------------------------------------------------------
# Run-evidence discovery
#
# The server extension has always accepted `run=`; until the file browser
# discovered one, nothing sent it, so every double-clicked spec rendered
# spec-only and the whole trust ladder in manifest.py was unreachable here.


def frontend_run_evidence_names() -> list[str]:
    match = re.search(
        r"export const ASTRA_RUN_EVIDENCE_NAMES = \[(.*?)\];", FRONTEND, re.S
    )
    assert match, "ASTRA_RUN_EVIDENCE_NAMES not found in astraViewer.ts"
    return re.findall(r"'([^']+)'", match.group(1))


def test_discovered_names_are_exactly_the_ones_manifest_py_accepts():
    """A name only the frontend knows is a manifest that never loads."""
    from neurodesk_astra_view import manifest

    source = (
        VIEWER / "neurodesk_astra_view/manifest.py"
    ).read_text(encoding="utf-8")
    body = re.search(
        r"def _directory_run_file.*?candidates = \((.*?)\)", source, re.S
    )
    assert body, "_directory_run_file candidates not found in manifest.py"
    python_names = re.findall(r'directory / "([^"]+)"', body.group(1))

    assert python_names, "no candidate filenames parsed from manifest.py"
    assert frontend_run_evidence_names() == python_names
    # The parse above is only trustworthy while the function still exists.
    assert callable(manifest._directory_run_file)


def test_the_graph_request_carries_the_discovered_run():
    assert "run: string | null" in FRONTEND
    assert "query.run = run;" in FRONTEND
    assert "const run = await this._discoverRun();" in FRONTEND


def test_discovery_sends_nothing_when_no_evidence_sits_beside_the_spec():
    """No manifest is a true spec-only reading, not an error."""
    discover = re.search(
        r"private async _discoverRun\(\).*?\n  \}", FRONTEND, re.S
    )
    assert discover, "_discoverRun not found in astraViewer.ts"
    body = discover.group(0)

    assert "if (present.length === 0) {\n      return null;\n    }" in body
    # An unreadable directory must degrade to spec-only, never break the view.
    assert "} catch {\n      return null;\n    }" in body


def test_ambiguous_evidence_is_left_for_the_server_to_refuse():
    """Two manifests is a choice this side must not make silently."""
    discover = re.search(
        r"private async _discoverRun\(\).*?\n  \}", FRONTEND, re.S
    )
    assert discover
    body = discover.group(0)

    assert "if (present.length > 1) {" in body
    # '' is the dirname of a spec at the workspace root, which resolve_confined
    # rejects; '.' resolves to the root itself.
    assert "return directory || '.';" in body


def test_ambiguity_is_still_refused_by_the_server(tmp_path):
    """The rule the frontend defers to has to actually be there."""
    from neurodesk_astra_view.manifest import RunManifestError, load_run

    (tmp_path / "run-manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "status.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RunManifestError, match="ambiguous"):
        load_run(tmp_path, project_root=tmp_path, universe_id="baseline")


def test_a_refresh_rereads_the_directory_for_evidence_written_since():
    """`fileChanged` never fires for a manifest a finished job dropped in."""
    assert "void this._refresh();" in FRONTEND
    assert "private async _refresh(): Promise<void> {" in FRONTEND
    assert "nd-astra-refresh" in FRONTEND


def test_a_refresh_keeps_the_universe_the_user_was_looking_at():
    assert "this._universeSelect.options.length > 0" in FRONTEND
    assert "this._universeSelect.value = previous as string;" in FRONTEND
