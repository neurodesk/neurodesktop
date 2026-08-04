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

    # The drawn picture is the presentation projection: stages, decision
    # clusters, a synthetic result — with its own per-view layered ranking.
    projection = graph["projection"]
    assert {node["kind"] for node in projection["nodes"]} >= {
        "stage",
        "decision-cluster",
        "result",
    }
    view_rank = {node["id"]: node.get("rank") for node in projection["nodes"]}
    for edge in projection["edges"]:
        if edge["kind"] in ("flow", "produces", "supports", "concludes"):
            assert view_rank[edge["source"]] < view_rank[edge["target"]], edge

    for mode in ("flow", "decisions", "evidence"):
        widget = AstraView(
            EXAMPLE / "astra.yaml",
            universe=EXAMPLE / "universes/bet-f-0-5.yaml",
            mode=mode,
        )
        assert widget.mode == mode
        assert widget.graph["errors"] == []


def test_widget_frontend_is_self_contained_with_no_network_loader():
    import neurodesk_astra_view.widget as widget_module

    # The frontend is a self-contained SVG renderer: no vendored graph
    # library, no runtime fetch, no dynamic network import.
    assert "createElementNS" in widget_module.AstraView._esm
    assert "cytoscape" not in widget_module.AstraView._esm.lower()
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

    # Run-evidence discovery is what lets a double-clicked spec report
    # anything but spec-only; the names it looks for are string literals, so
    # they survive minification and a build that dropped them is visible here.
    # tests/unit/test_astra_view_filebrowser.py is what holds this list to
    # the one manifest.py accepts.
    for name in (
        "run-manifest.json",
        "manifest.json",
        "status.json",
        "ro-crate-metadata.json",
    ):
        assert name in bundle, name
    assert "Refresh" in bundle


def test_run_evidence_beside_a_spec_is_read_by_the_installed_viewer(tmp_path):
    """The contract behind discovery: a manifest has to change the badge.

    The file-browser plugin finds the manifest and passes it as ``run=``;
    this is the server-side half it hands that path to.
    """
    import json
    import shutil

    from neurodesk_astra_view import build_graph

    project = tmp_path / "astra-bet"
    shutil.copytree(EXAMPLE, project)
    universe = project / "universes/bet-f-0-5.yaml"

    assert build_graph(project / "astra.yaml", universe)["trust"][
        "level"
    ] == "spec-only"

    artifact = project / "results/boundary_qc.png"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    (project / "run-manifest.json").write_text(
        json.dumps(
            {
                "runtime": "slurm",
                "outputs": [
                    {
                        "output_id": "boundary_qc",
                        "universe_id": "bet-f-0-5",
                        "status": "ok",
                        "artifact": "results/boundary_qc.png",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    graph = build_graph(
        project / "astra.yaml", universe, project / "run-manifest.json"
    )
    assert graph["errors"] == []
    assert graph["trust"]["level"] == "executed-unverified"
    assert graph["trust"]["label"] == "Executed, unverified"
    assert graph["meta"]["run_source"] == "lightcone-run"
    assert any(node["kind"] == "artifact" for node in graph["nodes"])
