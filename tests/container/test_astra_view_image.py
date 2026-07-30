"""Built-image contract for the offline ASTRA provenance widget."""

import importlib.metadata
from pathlib import Path

from testlib import run_cmd


PILOT = Path("/opt/neurodesktop/pilots/astra-lightcone-bet/project")


def test_astra_viewer_and_schema_dependencies_are_installed_at_exact_versions():
    assert importlib.metadata.version("neurodesk-astra-view") == "0.1.0"
    assert importlib.metadata.version("astra-spec") == "0.0.12"
    assert importlib.metadata.version("astra-tools") == "0.2.11"
    assert importlib.metadata.version("anywidget") == "0.11.0"
    assert importlib.metadata.version("ipywidgets") == "8.1.8"

    import neurodesk_astra_view

    assert neurodesk_astra_view.__version__ == "0.1.0"


def test_shipped_bet_example_validates_and_builds_all_three_viewer_modes():
    code, output = run_cmd("astra validate astra.yaml", cwd=PILOT)
    assert code == 0, output
    assert "Schema validation passed" in output
    assert "version mismatch" not in output.lower()

    from neurodesk_astra_view import AstraView, build_graph

    graph = build_graph(
        PILOT / "astra.yaml", PILOT / "universes/bet-f-0-5.yaml"
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
    for mode in ("flow", "decisions", "evidence"):
        widget = AstraView(
            PILOT / "astra.yaml",
            universe=PILOT / "universes/bet-f-0-5.yaml",
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
