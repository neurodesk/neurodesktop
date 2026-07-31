"""Released-schema and graph contracts for the Neurodesktop ASTRA viewer.

These tests run the *released* ASTRA validators, so they need `astra-tools` and
`astra-spec` installed. That is a heavier dependency than the rest of the unit
tier carries, so the module skips rather than fails when they are absent; CI
installs them (see ``.github/workflows/unit-tests.yml``) and the image always
has them.
"""

import hashlib
import json
import shutil
import sys

import pytest

from testlib import repo_path


pytest.importorskip(
    "astra.validation",
    reason="astra-tools/astra-spec are not installed; see docs/testing.md",
)

sys.path.insert(0, str(repo_path("extensions/astra-viewer")))

from neurodesk_astra_view.adapter import AdapterError, adapt_project  # noqa: E402
from neurodesk_astra_view.graph import build_graph  # noqa: E402
from neurodesk_astra_view.manifest import _trust_label  # noqa: E402
from neurodesk_astra_view.preview import PreviewError, preview_artifact  # noqa: E402


FIXTURES = repo_path("tests/fixtures/astra-viewer")
# The shipped worked example doubles as the richest spec fixture: one spec,
# one place to edit, and the image test asserts the installed copy of it.
BET = repo_path("examples/astra-bet")


def _node(graph, identifier):
    return next(node for node in graph["nodes"] if node["id"] == identifier)


def test_bet_graph_selects_exactly_one_universe_and_keeps_evidence_distinct():
    baseline = build_graph(
        BET / "astra.yaml", BET / "universes/bet-f-0-5.yaml"
    )
    alternative = build_graph(
        BET / "astra.yaml", BET / "universes/bet-f-0-3.yaml"
    )

    assert baseline["errors"] == []
    assert baseline["trust"]["level"] == "spec-only"
    assert baseline["meta"]["universe_id"] == "bet-f-0-5"
    assert {
        edge["label"]
        for edge in baseline["edges"]
        if edge["kind"] == "parameterizes"
    } == {"f_0_5"}
    assert {
        edge["label"]
        for edge in alternative["edges"]
        if edge["kind"] == "parameterizes"
    } == {"f_0_3"}

    kinds = {node["kind"] for node in baseline["nodes"]}
    assert {"insight", "finding", "evidence"} <= kinds
    edge_kinds = {edge["kind"] for edge in baseline["edges"]}
    assert {"supports", "justifies", "claims"} <= edge_kinds
    finding = _node(baseline, "finding:root/boundary_is_inspectable")
    assert finding["description"].startswith("Each universe materializes")


def test_wrong_declared_version_warns_but_still_renders(tmp_path):
    """Match `astra validate`: warn and validate against the installed release.

    Agent-scaffolded specs routinely copy a stale version string from example
    docs; refusing to render such a spec surfaced nothing the validators would
    not catch anyway. The drift stays visible as a warning banner.
    """
    project = tmp_path / "project"
    shutil.copytree(BET, project)
    spec = project / "astra.yaml"
    spec.write_text(spec.read_text().replace('version: "0.0.12"', 'version: "9.9.9"'))

    graph = build_graph(spec)

    assert graph["meta"]["valid"] is True
    assert graph["errors"] == []
    assert len(graph["nodes"]) > 0
    assert len(graph["warnings"]) == 1
    assert "'9.9.9'" in graph["warnings"][0]
    assert "0.0.12" in graph["warnings"][0]


def test_matching_version_renders_without_warnings():
    graph = build_graph(BET / "astra.yaml", BET / "universes/bet-f-0-5.yaml")

    assert graph["errors"] == []
    assert graph["warnings"] == []


def test_version_drift_survives_into_an_invalid_graph(tmp_path):
    """When the installed schema rejects the spec, the drift explains why."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "astra.yaml").write_text(
        'version: "9.9.9"\nname: broken\noutputs: "not a list"\n'
    )

    graph = build_graph(project / "astra.yaml")

    assert graph["meta"] == {"valid": False}
    assert graph["errors"]
    assert any("'9.9.9'" in warning for warning in graph["warnings"])


def test_unknown_trust_level_has_a_safe_label():
    assert _trust_label("future-level") == "Unknown trust level: future-level"


def test_external_analysis_and_child_universe_are_resolved_with_qualified_ids():
    project = FIXTURES / "external"
    adapted = adapt_project(project / "astra.yaml", "universes/child-a.yaml")

    child = next(
        output for output in adapted["outputs"] if output["local_id"] == "child_result"
    )
    assert child["id"] == "output:root/child/child_result"
    decision = next(
        item for item in adapted["decisions"] if item["local_id"] == "child_choice"
    )
    assert decision["selected"] == "a"


def test_external_analysis_escape_is_rejected_before_loading(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "astra.yaml").write_text("not: valid\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / "astra.yaml").write_text(
        'version: "0.0.12"\nname: escape\ninputs: []\noutputs: []\n'
        "analyses:\n  child:\n    path: ../outside\n"
    )

    with pytest.raises(AdapterError, match="escapes the ASTRA project root"):
        adapt_project(project / "astra.yaml")


def test_gap_rules_g1_through_g6_are_stable_and_g5_is_run_only(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "gaps", project)
    spec = project / "astra.yaml"
    universe = project / "universes/only-a.yaml"

    spec_only = build_graph(spec, universe)
    spec_gap_ids = {gap["id"] for gap in spec_only["gaps"]}
    assert {"G1", "G3", "G4", "G6"} <= spec_gap_ids
    assert "G5" not in spec_gap_ids
    disconnected = build_graph(FIXTURES / "g2/astra.yaml")
    assert "G2" in {gap["id"] for gap in disconnected["gaps"]}

    artifact = project / "processed.bin"
    artifact.write_bytes(b"processed")
    manifest = project / "run-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "verification": {"status": "passed"},
                "outputs": [
                    {
                        "output_id": "processed",
                        "artifact": "processed.bin",
                        "sha256": hashlib.sha256(b"processed").hexdigest(),
                    }
                ],
            }
        )
    )
    executed = build_graph(spec, universe, manifest)
    executed_gap_ids = {gap["id"] for gap in executed["gaps"]}
    assert {"G5", "G7"} <= executed_gap_ids
    assert executed["trust"]["level"] == "executed-verified"
    # A run record without units must leave the node's units untouched rather
    # than overwrite them with None; one that declares units must apply them.
    assert _node(executed, "output:root/processed")["units"] is None
    with_units = json.loads(manifest.read_text())
    with_units["outputs"][0]["units"] = "mm^3"
    manifest.write_text(json.dumps(with_units))
    executed_with_units = build_graph(spec, universe, manifest)
    assert _node(executed_with_units, "output:root/processed")["units"] == "mm^3"


def test_explicit_runtime_none_with_declared_container_is_red_and_non_dismissible(
    tmp_path,
):
    project = tmp_path / "project"
    shutil.copytree(BET, project)
    output = project / "brain.nii.gz"
    output.write_bytes(b"brain")
    manifest = project / "run-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "execution": {"runtime": "none"},
                "verified": True,
                "outputs": [
                    {
                        "output_id": "bet_brain",
                        "artifact": "brain.nii.gz",
                        "container_image": "docker://example.invalid/fsl@sha256:deadbeef",
                    }
                ],
            }
        )
    )

    graph = build_graph(
        project / "astra.yaml", project / "universes/bet-f-0-5.yaml", manifest
    )

    assert graph["trust"]["level"] == "provenance-mismatch"
    assert graph["trust"]["dismissible"] is False
    assert "did not run" in graph["trust"]["message"]


def _bet_run(project, manifest_body):
    """Write a run manifest next to a copy of the worked example."""
    manifest = project / "run-manifest.json"
    manifest.write_text(json.dumps(manifest_body))
    return manifest


def test_execution_without_passing_verification_stays_amber(tmp_path):
    """The third trust level: evidence of a run, no verification to trust.

    Promoting an unverified run to `executed-verified` is the failure this
    guards; a manifest that simply omits verification must stay amber.
    """
    project = tmp_path / "project"
    shutil.copytree(BET, project)
    (project / "brain.nii.gz").write_bytes(b"brain")
    manifest = _bet_run(
        project,
        {
            "execution": {"runtime": "apptainer"},
            "outputs": [
                {"output_id": "bet_brain", "artifact": "brain.nii.gz"}
            ],
        },
    )

    graph = build_graph(
        project / "astra.yaml", project / "universes/bet-f-0-5.yaml", manifest
    )

    assert graph["errors"] == []
    assert graph["trust"]["level"] == "executed-unverified"
    assert graph["trust"]["output_integrity"] == "not-run"
    # Amber is dismissible; only a provenance mismatch is not.
    assert graph["trust"]["dismissible"] is True


def test_a_stale_recorded_hash_or_size_fails_closed(tmp_path):
    """A recorded digest that no longer matches the artifact is not rendered.

    The manifest is the only claim that a byte on disk is the byte that was
    produced. Rendering it anyway would attach real provenance to an artifact
    nobody verified, so the graph reports the error instead of the run.
    """
    project = tmp_path / "project"
    shutil.copytree(BET, project)
    (project / "brain.nii.gz").write_bytes(b"brain")

    stale_hash = _bet_run(
        project,
        {
            "verification": {"status": "passed"},
            "outputs": [
                {
                    "output_id": "bet_brain",
                    "artifact": "brain.nii.gz",
                    "sha256": hashlib.sha256(b"different bytes").hexdigest(),
                }
            ],
        },
    )
    graph = build_graph(
        project / "astra.yaml", project / "universes/bet-f-0-5.yaml", stale_hash
    )
    assert graph["meta"] == {"valid": False}
    assert "stale hash" in graph["errors"][0]

    stale_size = _bet_run(
        project,
        {
            "verification": {"status": "passed"},
            "outputs": [
                {
                    "output_id": "bet_brain",
                    "artifact": "brain.nii.gz",
                    "size": 999_999,
                }
            ],
        },
    )
    graph = build_graph(
        project / "astra.yaml", project / "universes/bet-f-0-5.yaml", stale_size
    )
    assert graph["meta"] == {"valid": False}
    assert "stale size" in graph["errors"][0]


def test_a_run_artifact_outside_the_project_is_rejected(tmp_path):
    """A manifest must not be able to name a path it does not own."""
    project = tmp_path / "project"
    shutil.copytree(BET, project)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    manifest = _bet_run(
        project,
        {
            "verification": {"status": "passed"},
            "outputs": [
                {"output_id": "bet_brain", "artifact": "../secret.txt"}
            ],
        },
    )

    graph = build_graph(
        project / "astra.yaml", project / "universes/bet-f-0-5.yaml", manifest
    )

    assert graph["meta"] == {"valid": False}
    assert "escapes" in graph["errors"][0]


def test_previews_are_confined_and_cover_metric_table_report_and_figure(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    metric = project / "metric.json"
    metric.write_text('{"value": 42, "units": "mm3"}')
    table = project / "table.csv"
    table.write_text("name,value\na,1\nb,2\n")
    report = project / "report.md"
    report.write_text("# Report\n")
    figure = project / "figure.png"
    figure.write_bytes(b"\x89PNG\r\n\x1a\n")

    assert preview_artifact(metric, project, "metric")["units"] == "mm3"
    assert preview_artifact(table, project, "table")["rows"] == [["a", "1"], ["b", "2"]]
    assert preview_artifact(report, project, "report")["text"] == "# Report\n"
    assert preview_artifact(figure, project, "figure")["data_url"].startswith(
        "data:image/png;base64,"
    )

    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    with pytest.raises(PreviewError, match="escapes"):
        preview_artifact(outside, project, "report")
