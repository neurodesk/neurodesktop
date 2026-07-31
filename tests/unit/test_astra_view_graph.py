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
import receipt_fixtures
import rfc8785

from testlib import repo_path


pytest.importorskip(
    "astra.validation",
    reason="astra-tools/astra-spec are not installed; see docs/testing.md",
)

sys.path.insert(0, str(repo_path("extensions/astra-viewer")))

from neurodesk_astra_view.adapter import AdapterError, adapt_project  # noqa: E402
from neurodesk_astra_view.graph import build_graph  # noqa: E402
from neurodesk_astra_view.manifest import (  # noqa: E402
    RunManifestError,
    _trust_label,
    _validate_finalized_receipt,
)
from neurodesk_astra_view.preview import PreviewError, preview_artifact  # noqa: E402


PILOT = repo_path("pilots/astra-lightcone-bet/project")
FIXTURES = repo_path("tests/fixtures/astra-viewer")


def _node(graph, identifier):
    return next(node for node in graph["nodes"] if node["id"] == identifier)


def test_bet_graph_selects_exactly_one_universe_and_keeps_evidence_distinct():
    baseline = build_graph(
        PILOT / "astra.yaml", PILOT / "universes/bet-f-0-5.yaml"
    )
    alternative = build_graph(
        PILOT / "astra.yaml", PILOT / "universes/bet-f-0-3.yaml"
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


def test_graph_returns_errors_and_no_partial_graph_for_wrong_schema_version(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(PILOT, project)
    spec = project / "astra.yaml"
    spec.write_text(spec.read_text().replace('version: "0.0.12"', 'version: "9.9.9"'))

    graph = build_graph(spec)

    assert graph["meta"] == {"valid": False}
    assert graph["nodes"] == []
    assert "expected '0.0.12'" in graph["errors"][0]


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
    shutil.copytree(PILOT, project)
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


def test_finalized_module_receipt_stays_amber_and_tampering_fails_closed(
    tmp_path, monkeypatch
):
    fixture = json.loads(
        repo_path(
            "tests/fixtures/pilot-execution-receipts/valid-success.json"
        ).read_text()
    )
    fixture["analysis"]["universes"] = fixture["analysis"]["universes"][:1]
    fixture["outputs"] = [
        item for item in fixture["outputs"] if item["universeId"] == "bet-f-0.5"
    ]
    fixture["lightcone"]["manifests"] = [
        item
        for item in fixture["lightcone"]["manifests"]
        if item["outputKey"].startswith("bet-f-0.5:")
    ]
    verification = fixture["lightcone"]["verification"]
    verification["expectedOutputKeys"] = [
        value
        for value in verification["expectedOutputKeys"]
        if value.startswith("bet-f-0.5:")
    ]
    verification["results"] = [
        item
        for item in verification["results"]
        if item["outputKey"].startswith("bet-f-0.5:")
    ]
    replacements = {
        "bet-f-0.5": "bet-f-0-5",
        "bet-brain": "bet_brain",
        "bet-mask": "bet_mask",
        "mask-volume": "mask_volume",
        "boundary-qc": "boundary_qc",
    }

    def replace_strings(value):
        if isinstance(value, str):
            for old, new in replacements.items():
                value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [replace_strings(item) for item in value]
        if isinstance(value, dict):
            return {key: replace_strings(item) for key, item in value.items()}
        return value

    fixture = replace_strings(fixture)
    run_id = fixture["receiptId"]
    fixture["analysis"]["spec"]["relativePath"] = f"runs/{run_id}/project/astra.yaml"
    universe = fixture["analysis"]["universes"][0]
    universe["definition"]["relativePath"] = (
        f"runs/{run_id}/project/universes/bet-f-0-5.yaml"
    )
    universe["decisions"] = {"bet_fractional_threshold": "f_0_5"}

    custom_fixtures = tmp_path / "receipt-fixtures"
    custom_fixtures.mkdir()
    (custom_fixtures / "valid-success.json").write_text(json.dumps(fixture))
    monkeypatch.setattr(receipt_fixtures, "FIXTURE_DIR", custom_fixtures)
    receipt_path, workspace, module_root = receipt_fixtures.materialize_success_receipt(
        tmp_path / "materialized"
    )
    monkeypatch.setenv("NEURODESKTOP_LOCAL_CONTAINERS", str(module_root))
    receipt = json.loads(receipt_path.read_text())

    spec_path = workspace / receipt["analysis"]["spec"]["relativePath"]
    receipt["analysis"]["spec"].update(
        receipt_fixtures.write_hashed_file(
            spec_path, (PILOT / "astra.yaml").read_bytes()
        )
    )
    universe_path = workspace / receipt["analysis"]["universes"][0]["definition"][
        "relativePath"
    ]
    receipt["analysis"]["universes"][0]["definition"].update(
        receipt_fixtures.write_hashed_file(
            universe_path, (PILOT / "universes/bet-f-0-5.yaml").read_bytes()
        )
    )
    crate = receipt["roCrate"]
    crate_root = workspace / crate["rootRelativePath"]
    (crate_root / "payload/000-astra.yaml").write_bytes(spec_path.read_bytes())
    (crate_root / "payload/001-bet-f-0-5.yaml").write_bytes(
        universe_path.read_bytes()
    )
    inventory = []
    for path in crate_root.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            inventory.append(
                {
                    "relativePath": path.relative_to(crate_root).as_posix(),
                    "sizeBytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
    inventory.sort(key=lambda item: item["relativePath"].encode("utf-8"))
    inventory_path = workspace / crate["inventory"]["relativePath"]
    crate["inventory"].update(
        receipt_fixtures.write_hashed_file(
            inventory_path,
            json.dumps(
                inventory,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode(),
        )
    )
    crate["treeSha256"] = hashlib.sha256(rfc8785.dumps(inventory)).hexdigest()
    receipt_path.write_text(json.dumps(receipt))
    final_receipt = workspace / "runs" / run_id / "receipt" / "receipt.json"
    final_receipt.parent.mkdir(parents=True)
    receipt_path.replace(final_receipt)

    graph = build_graph(spec_path, universe_path, final_receipt)
    assert graph["errors"] == []
    assert graph["trust"]["level"] == "executed-unverified"
    assert graph["trust"]["output_integrity"] == "passed"
    output = _node(graph, "output:root/bet_brain")
    assert output["published"] is True
    assert output["status"] == "ok"

    tampered = workspace / receipt["outputs"][0]["artifact"]["relativePath"]
    tampered.write_bytes(tampered.read_bytes() + b"tampered")
    rejected = build_graph(spec_path, universe_path, final_receipt)
    assert rejected["nodes"] == []
    assert "finalized receipt validation failed" in rejected["errors"][0]


def test_finalized_receipt_cannot_authorize_an_arbitrary_module_root(
    tmp_path, monkeypatch
):
    receipt_path, workspace, module_root = (
        receipt_fixtures.materialize_success_receipt(tmp_path)
    )
    monkeypatch.setenv("NEURODESKTOP_LOCAL_CONTAINERS", str(module_root))
    receipt = json.loads(receipt_path.read_text())

    _validate_finalized_receipt(receipt_path, receipt, workspace)

    outside_modulefile = tmp_path / "untrusted-modules" / "fsl.lua"
    receipt["execution"]["module"]["modulefile"].update(
        {
            "path": str(outside_modulefile),
            **receipt_fixtures.write_hashed_file(
                outside_modulefile, b"untrusted modulefile\n"
            ),
        }
    )
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(RunManifestError, match="escapes its allowed roots"):
        _validate_finalized_receipt(receipt_path, receipt, workspace)


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
