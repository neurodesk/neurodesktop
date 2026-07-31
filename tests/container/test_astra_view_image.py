"""Built-image contract for the offline ASTRA provenance widget."""

import importlib.metadata
from pathlib import Path

from testlib import run_cmd


EXAMPLE = Path("/opt/neurodesktop/examples/astra-bet")


def test_astra_viewer_and_schema_dependencies_are_installed_at_exact_versions():
    assert importlib.metadata.version("neurodesk-astra-view") == "0.1.0"
    assert importlib.metadata.version("astra-spec") == "0.0.12"
    assert importlib.metadata.version("astra-tools") == "0.2.11"
    assert importlib.metadata.version("anywidget") == "0.11.0"
    assert importlib.metadata.version("ipywidgets") == "8.1.8"

    import neurodesk_astra_view

    assert neurodesk_astra_view.__version__ == "0.1.0"


def test_shipped_bet_example_validates_and_builds_all_three_viewer_modes():
    code, output = run_cmd("astra validate astra.yaml", cwd=EXAMPLE)
    assert code == 0, output
    assert "Schema validation passed" in output
    assert "version mismatch" not in output.lower()

    from neurodesk_astra_view import AstraView, build_graph

    graph = build_graph(
        EXAMPLE / "astra.yaml", EXAMPLE / "universes/bet-f-0-5.yaml"
    )
    assert graph["errors"] == []
    assert graph["trust"]["level"] == "spec-only"
    assert {node["kind"] for node in graph["nodes"]} >= {
        "input",
        "output",
        "decision",
        "finding",
        "insight",
        "evidence",
    }
    # The renderer places nodes from these two fields alone, so the installed
    # copy has to carry a layered ranking, not just a node list.
    rank = {node["id"]: node["rank"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        assert rank[edge["source"]] < rank[edge["target"]], edge["id"]
    assert graph["meta"]["analysis_name"] == "BET threshold sensitivity"

    for mode in ("flow", "decisions", "evidence"):
        widget = AstraView(
            EXAMPLE / "astra.yaml",
            universe=EXAMPLE / "universes/bet-f-0-5.yaml",
            mode=mode,
        )
        assert widget.mode == mode
        assert widget.graph["errors"] == []


def test_widget_frontend_is_vendored_and_contains_no_network_loader():
    import neurodesk_astra_view.widget as widget_module

    assert "Cytoscape Consortium" in widget_module.AstraView._esm
    assert "fetch(" not in widget_module.AstraView._esm
    assert "import(\"http" not in widget_module.AstraView._esm
    assert "import('http" not in widget_module.AstraView._esm


def test_file_browser_server_extension_is_enabled_and_single_sourced():
    """The extension behind double-clicking astra.yaml in the file browser."""
    config = Path(
        "/opt/conda/etc/jupyter/jupyter_server_config.d/neurodesk_astra_view.json"
    )
    assert config.is_file()

    code, output = run_cmd("jupyter server extension list", timeout=60)
    assert code == 0, output
    assert "neurodesk_astra_view.serverext" in output
    listed = [
        line
        for line in output.splitlines()
        if "neurodesk_astra_view.serverext" in line
    ]
    assert any("enabled" in line for line in listed), output

    # The HTTP asset endpoint must serve exactly the anywidget frontend.
    from neurodesk_astra_view import serverext
    import neurodesk_astra_view.widget as widget_module

    assets = serverext._assets()
    assert assets["esm"][1] == widget_module.AstraView._esm
    assert assets["css"][1] == widget_module.AstraView._css


def test_file_browser_viewer_plugin_survived_the_labextension_build():
    bundle = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in Path(
            "/opt/conda/share/jupyter/labextensions/neurodesk-launcher/static"
        ).glob("*.js")
    )

    assert "neurodesk-launcher:astra-viewer" in bundle
    assert "astra-yaml" in bundle
    assert "ASTRA Viewer" in bundle
    assert "neurodesk-astra-view" in bundle
