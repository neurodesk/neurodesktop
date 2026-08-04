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
from neurodesk_astra_view.layout import assign_layout  # noqa: E402
from neurodesk_astra_view.manifest import _trust_label, spec_only_trust  # noqa: E402
from neurodesk_astra_view.preview import PreviewError, preview_artifact  # noqa: E402
from neurodesk_astra_view.projection import project_graph  # noqa: E402


FIXTURES = repo_path("tests/fixtures/astra-viewer")
# The shipped worked example doubles as the richest spec fixture: one spec,
# one place to edit, and the image test asserts the installed copy of it.
BET = repo_path("tests/fixtures/astra-bet")


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


def test_trust_labels_name_the_run_state_not_a_selection():
    """The badge sits under a universe picker, so it must not read as one.

    Its old `spec-only` label, "Selected analysis", parsed as a restatement of
    the universe the reader had just chosen rather than as the claim it makes:
    that nothing was executed.
    """
    assert _trust_label("spec-only") == "Not executed"
    assert spec_only_trust()["label"] == "Not executed"
    for level in ("executed-unverified", "executed-verified", "provenance-mismatch"):
        assert "analysis" not in _trust_label(level).lower()


def test_a_retired_narrative_is_read_as_a_description_not_rejected(tmp_path):
    """A spec written before astra-spec RFC-0002 still draws.

    RFC-0002 retired `narrative` and `authors` in favour of one `description`.
    A spec that predates it is not wrong about its own analysis, only spelled
    against a schema that moved; refusing to draw it tells the reader nothing
    they can act on, and the viewer is often where they find out at all.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "astra.yaml").write_text(
        'version: "0.0.12"\nname: drifted\nauthors: [someone]\n'
        "narrative:\n"
        "  summary: |\n    What this analysis is.\n"
        "  methods: |\n    How it was done.\n"
        "inputs: []\noutputs: []\n"
    )

    graph = build_graph(project / "astra.yaml")

    assert graph["errors"] == []
    assert graph["meta"]["valid"] is True
    root = _node(graph, "analysis:root")
    # The write-up is the part the reader most wants on screen, so it is
    # folded into `description` rather than discarded with the field name.
    assert "What this analysis is." in root["description"]
    assert "How it was done." in root["description"]

    warnings = " ".join(graph["warnings"])
    assert "narrative" in warnings and "description" in warnings
    assert "authors" in warnings


def test_a_retired_field_in_an_inline_sub_analysis_is_adopted_too(tmp_path):
    """An inline child is validated as part of its parent.

    By the time the recursion reaches that child, the parent would already
    have been rejected — so the child's retired spelling has to be adopted
    from the parent's pass, not the child's.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "astra.yaml").write_text(
        'version: "0.0.12"\nname: parent\ninputs: []\noutputs: []\n'
        "analyses:\n"
        "  child:\n"
        "    name: Child\n"
        "    narrative:\n      summary: |\n        Child prose.\n"
        "    inputs:\n      - id: child_input\n        type: data\n"
        "    outputs:\n      - id: child_result\n        type: metric\n"
        "        inputs: [child_input]\n"
    )

    graph = build_graph(project / "astra.yaml")

    assert graph["errors"] == []
    assert "Child prose." in _node(graph, "analysis:root/child")["description"]


def test_option_insights_naming_an_ancestor_insight_still_resolve(tmp_path):
    """astra-tools scoped `Option.insights` to the declaring analysis.

    A bare reference in an older spec meant "search upwards", so it is read as
    a `../` reference rather than failing the whole graph. One that resolves
    locally is left alone, so the two readings only differ where the old spec
    had no other possible target.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "astra.yaml").write_text(
        'version: "0.0.12"\nname: parent\ninputs: []\noutputs: []\n'
        "prior_insights:\n"
        "  upstairs:\n"
        '    label: "Known upstairs"\n'
        '    claim: "An insight declared on the root."\n'
        '    created_at: "2026-07-30T00:00:00Z"\n'
        "    evidence:\n      - id: a_paper\n"
        '        doi: "10.1000/example"\n'
        "analyses:\n"
        "  child:\n"
        "    name: Child\n"
        "    inputs:\n      - id: child_input\n        type: data\n"
        "    outputs:\n      - id: child_result\n        type: metric\n"
        "        inputs: [child_input]\n        decisions: [pick]\n"
        "    decisions:\n"
        "      pick:\n"
        '        label: "Pick"\n'
        "        default: a\n"
        "        options:\n"
        "          a:\n"
        '            label: "A"\n'
        "            insights: [upstairs]\n"
        "          b:\n"
        '            label: "B"\n'
    )

    graph = build_graph(project / "astra.yaml")

    assert graph["errors"] == []
    # The reference survives as a real edge, not just a silenced error.
    justifies = {
        (edge["source"], edge["target"])
        for edge in graph["edges"]
        if edge["kind"] == "justifies"
    }
    assert ("insight:root/upstairs", "decision:root/child/pick") in justifies
    assert "option insight reference" in " ".join(graph["warnings"])


def test_a_genuine_schema_error_is_still_an_error(tmp_path):
    """Adoption covers retired spellings, not authoring mistakes.

    A stray key deep inside a decision is far likelier to be a typo than
    schema drift, and silently discarding content there would hide it.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "astra.yaml").write_text(
        'version: "0.0.12"\nname: broken\ninputs: []\noutputs: []\n'
        "decisions:\n"
        "  pick:\n"
        '    label: "Pick"\n'
        "    default: a\n"
        "    nonsense: true\n"
        "    options:\n      a:\n        label: \"A\"\n"
    )

    graph = build_graph(project / "astra.yaml")

    assert graph["meta"] == {"valid": False}
    assert "nonsense" in graph["errors"][0]


def test_the_header_can_name_the_analysis_rather_than_only_its_universe():
    graph = build_graph(BET / "astra.yaml", BET / "universes/bet-f-0-5.yaml")

    assert graph["meta"]["analysis_name"] == "BET threshold sensitivity"
    assert graph["meta"]["universe_id"] == "bet-f-0-5"


# ---------------------------------------------------------------------------
# Layered layout


def test_every_edge_points_strictly_down_a_rank():
    """The layering defect the viewer shipped with, stated as a contract.

    Cytoscape's `breadthfirst` layers by hop count from the nearest root, so a
    producer one hop from an input landed on the same row as a consumer one
    hop from a decision — and the graph drew its dataflow arrows pointing
    backwards along that row.
    """
    graph = build_graph(BET / "astra.yaml", BET / "universes/bet-f-0-5.yaml")
    rank = {node["id"]: node["rank"] for node in graph["nodes"]}

    for node in graph["nodes"]:
        # Compound containers are sized by their children, never placed.
        if node["kind"] == "analysis":
            assert node["rank"] is None and node["order"] is None
        else:
            assert isinstance(node["rank"], int)
            assert isinstance(node["order"], int)

    for edge in graph["edges"]:
        source, target = rank[edge["source"]], rank[edge["target"]]
        assert source is not None and target is not None
        assert source < target, f"{edge['kind']} {edge['source']} -> {edge['target']}"


def test_a_source_sits_directly_above_what_it_feeds():
    """Longest paths alone strand every source on the top row.

    The citation backing a late finding would then sit rows above the outputs
    beside it, trailing an edge across the whole graph.
    """
    graph = build_graph(BET / "astra.yaml", BET / "universes/bet-f-0-5.yaml")
    rank = {node["id"]: node["rank"] for node in graph["nodes"]}

    spans = [
        rank[edge["target"]] - rank[edge["source"]] for edge in graph["edges"]
    ]
    assert spans and max(spans) == 1

    # The artifact evidence for the finding belongs beside the outputs, not
    # on the top row with the citation that informed the decision.
    artifact_evidence = rank[
        "evidence:root/findings/boundary_is_inspectable/boundary_overlay_artifact"
    ]
    assert artifact_evidence == rank["output:root/boundary_qc"]


def _laid_out(nodes, edges):
    prepared = [
        {"id": node, "kind": "output", "parent": "analysis:root"} for node in nodes
    ]
    assign_layout(
        prepared,
        [{"source": source, "target": target} for source, target in edges],
    )
    return {node["id"]: (node["rank"], node["order"]) for node in prepared}


def test_a_rank_is_ordered_to_reduce_crossings_not_left_in_declaration_order():
    """Sibling order is what made a decision cross its own edges.

    Declaration order puts `o1` before `o2` while their producers arrive in
    the opposite order; a barycenter pass swaps them so the two edges run
    parallel instead of crossing.
    """
    placed = _laid_out(
        ["d1", "d2", "o1", "o2"], [("d1", "o2"), ("d2", "o1")]
    )

    assert placed["d1"][0] == placed["d2"][0] == 0
    assert placed["o1"][0] == placed["o2"][0] == 1
    # Whatever absolute order the sweep settles on, each edge must join nodes
    # at the same offset within their rank.
    assert placed["d1"][1] == placed["o2"][1]
    assert placed["d2"][1] == placed["o1"][1]


def test_a_cycle_is_ranked_rather_than_hung_on():
    """The released validators reject cycles; the renderer must not hang if
    one ever reaches it, because a wedged canvas reports nothing at all."""
    placed = _laid_out(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "a")])

    assert set(placed) == {"a", "b", "c"}
    assert all(isinstance(rank, int) for rank, _ in placed.values())


# ---------------------------------------------------------------------------
# Presentation projection
#
# The drawn graph is derived from the semantic one: stages, grouped inputs and
# outputs, one expandable decision cluster per stage, evidence folded into the
# claim it backs, and a synthetic result node. See projection.py.


def _view(projection, identifier):
    return next(node for node in projection["nodes"] if node["id"] == identifier)


def test_projection_draws_stages_clusters_and_a_result():
    graph = build_graph(BET / "astra.yaml", BET / "universes/bet-f-0-5.yaml")
    projection = graph["projection"]
    kinds = {node["kind"] for node in projection["nodes"]}
    edge_kinds = {edge["kind"] for edge in projection["edges"]}

    assert {"stage", "input", "output", "decision-cluster", "decision",
            "insight", "finding", "result"} <= kinds
    assert {"flow", "produces", "configures", "informs", "supports",
            "concludes"} <= edge_kinds

    # The stage stands in for the analysis: its inputs flow into it and it
    # produces its outputs, so cross products collapse into paths.
    assert any(
        edge["kind"] == "flow" and edge["target"] == "view:stage:root"
        for edge in projection["edges"]
    )
    assert any(
        edge["kind"] == "produces" and edge["source"] == "view:stage:root"
        for edge in projection["edges"]
    )

    # The cluster is collapsed by default; its member carries the selected
    # value so the collapsed picture still answers "what was chosen".
    cluster = _view(projection, "view:decisions:analysis:root")
    assert cluster["label"] == "1 decision"
    member = _view(projection, cluster["members"][0])
    assert member["label"].endswith("= f = 0.5")
    assert member["parent"] == cluster["id"]

    result = _view(projection, "view:result")
    assert result["label"].startswith("BET threshold sensitivity")
    assert any(
        edge["kind"] == "concludes" and edge["target"] == "view:result"
        for edge in projection["edges"]
    )


def test_projection_folds_evidence_into_the_claim_it_backs():
    graph = build_graph(BET / "astra.yaml", BET / "universes/bet-f-0-5.yaml")
    projection = graph["projection"]

    assert not any(node["kind"] == "evidence" for node in projection["nodes"])
    finding = _view(projection, "view:finding:root/boundary_is_inspectable")
    assert any(line.startswith("evidence:") for line in finding["meta"])
    assert any(member.startswith("evidence:") for member in finding["members"])
    insight = _view(
        projection, "view:insight:root/bet_threshold_affects_boundary"
    )
    assert "evidence: 10.1002/hbm.10062" in insight["meta"]


def test_projection_primary_edges_point_down_their_view_ranks():
    """The projection ships one rank/order pair per view, both from
    layout.py, so each mode's picture keeps arrows pointing forward."""
    graph = build_graph(BET / "astra.yaml", BET / "universes/bet-f-0-5.yaml")
    projection = graph["projection"]
    rank = {node["id"]: node.get("rank") for node in projection["nodes"]}
    evidence = {
        node["id"]: node.get("evidence_rank") for node in projection["nodes"]
    }

    for edge in projection["edges"]:
        if edge["kind"] in ("flow", "produces", "supports", "concludes"):
            assert rank[edge["source"]] < rank[edge["target"]], edge
        if (
            edge["kind"] in ("informs", "configures", "supports", "concludes")
            and evidence[edge["source"]] is not None
            and evidence[edge["target"]] is not None
        ):
            assert evidence[edge["source"]] < evidence[edge["target"]], edge

    # Decision clusters are placed beside their stage by the renderer, never
    # ranked into the dataflow rows.
    cluster = _view(projection, "view:decisions:analysis:root")
    assert cluster["rank"] is None and cluster["target"] == "view:stage:root"


def _synthetic_project(inputs, outputs):
    nodes = [
        {
            "id": "analysis:root",
            "kind": "analysis",
            "parent": None,
            "label": "Pipeline",
            "description": None,
            "status": "unknown",
        }
    ]
    edges = []
    for index, local in enumerate(inputs):
        nodes.append(
            {
                "id": f"input:root/{local}",
                "kind": "input",
                "parent": "analysis:root",
                "label": local,
                "sub_kind": "data",
                "status": "unknown",
            }
        )
    for local, recipe in outputs:
        identifier = f"output:root/{local}"
        nodes.append(
            {
                "id": identifier,
                "kind": "output",
                "parent": "analysis:root",
                "label": local,
                "sub_kind": "dataset",
                "recipe": recipe,
                "selected_decisions": [],
                "status": "unknown",
            }
        )
        for local_input in inputs:
            edges.append(
                {
                    "id": f"dataflow:input:root/{local_input}->{identifier}",
                    "kind": "dataflow",
                    "source": f"input:root/{local_input}",
                    "target": identifier,
                }
            )
    projection = project_graph(
        nodes, edges, {"analysis_name": "Pipeline", "universe_id": "baseline"}
    )
    return projection


def test_projection_groups_crowded_outputs_by_recipe_family():
    """A complex workflow's fan of same-recipe outputs draws as one node."""
    projection = _synthetic_project(
        ["catalog"],
        [(f"xi_lrg{index}", "python src/compute_xi.py {output}") for index in range(6)],
    )

    groups = [n for n in projection["nodes"] if n["kind"] == "output-group"]
    assert len(groups) == 1
    assert len(groups[0]["members"]) == 6
    assert groups[0]["label"].endswith("×6")
    assert not any(node["kind"] == "output" for node in projection["nodes"])
    assert any(
        edge["kind"] == "produces" and edge["target"] == groups[0]["id"]
        for edge in projection["edges"]
    )
    # With no findings, the terminal outputs conclude in the result.
    assert any(
        edge["kind"] == "concludes" and edge["source"] == groups[0]["id"]
        for edge in projection["edges"]
    )


def test_projection_groups_crowded_inputs_by_family():
    projection = _synthetic_project(
        [f"cov_lrg{index}" for index in range(10)],
        [("xi", "python src/compute_xi.py {output}")],
    )

    groups = [n for n in projection["nodes"] if n["kind"] == "input-group"]
    assert len(groups) == 1
    assert len(groups[0]["members"]) == 10
    assert groups[0]["label"].endswith("×10")
    # The whole family flows into the stage once, not ten times.
    flows = [
        edge
        for edge in projection["edges"]
        if edge["kind"] == "flow" and edge["source"] == groups[0]["id"]
    ]
    assert [edge["target"] for edge in flows] == ["view:stage:root"]


def test_projection_folds_alias_inputs_into_their_canonical_source():
    """A record a child scope re-exports draws once, not once per scope."""
    nodes = [
        {"id": "analysis:root", "kind": "analysis", "parent": None,
         "label": "Pipeline", "description": None, "status": "unknown"},
        {"id": "analysis:root/child", "kind": "analysis",
         "parent": "analysis:root", "label": "Child", "description": None,
         "status": "unknown"},
        {"id": "input:root/catalog", "kind": "input", "parent": "analysis:root",
         "label": "Catalog", "sub_kind": "data", "status": "unknown"},
        {"id": "input:root/child/catalog", "kind": "input",
         "parent": "analysis:root/child", "label": "Catalog", "sub_kind": "data",
         "status": "unknown"},
        {"id": "output:root/child/xi", "kind": "output",
         "parent": "analysis:root/child", "label": "Xi", "sub_kind": "dataset",
         "recipe": "python src/xi.py", "selected_decisions": [],
         "status": "unknown"},
    ]
    edges = [
        {"id": "a", "kind": "dataflow", "source": "input:root/catalog",
         "target": "input:root/child/catalog"},
        {"id": "b", "kind": "dataflow", "source": "input:root/child/catalog",
         "target": "output:root/child/xi"},
    ]

    projection = project_graph(
        nodes, edges, {"analysis_name": "Pipeline", "universe_id": "baseline"}
    )

    inputs = [node for node in projection["nodes"] if node["kind"] == "input"]
    assert [node["id"] for node in inputs] == ["view:input:root/catalog"]
    # The canonical input flows into the consuming stage directly.
    assert any(
        edge["kind"] == "flow"
        and edge["source"] == "view:input:root/catalog"
        and edge["target"] == "view:stage:root/child"
        for edge in projection["edges"]
    )


def test_a_small_analysis_is_not_grouped():
    """Grouping exists for crowded pictures; the worked example keeps every
    output distinct."""
    graph = build_graph(BET / "astra.yaml", BET / "universes/bet-f-0-5.yaml")
    projection = graph["projection"]

    assert not any(
        node["kind"] in ("input-group", "output-group", "finding-group")
        for node in projection["nodes"]
    )
    assert sum(node["kind"] == "output" for node in projection["nodes"]) == 4


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
