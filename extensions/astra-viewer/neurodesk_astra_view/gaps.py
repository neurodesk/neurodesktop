"""Stable ASTRA provenance-gap rules G1 through G7."""

from __future__ import annotations

from typing import Any


def _gap(identifier: str, severity: str, message: str, node_id: str | None = None):
    value = {"id": identifier, "severity": severity, "message": message}
    if node_id:
        value["node_id"] = node_id
    return value


def detect_gaps(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    run_present: bool,
    run_output_ids: set[str] | None = None,
    spec_output_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Compute deterministic, non-fatal graph-quality signals."""

    gaps: list[dict[str, Any]] = []
    outgoing_dataflow = {
        edge["source"] for edge in edges if edge["kind"] == "dataflow"
    }
    parameterizing = {
        edge["source"] for edge in edges if edge["kind"] == "parameterizes"
    }

    for node in nodes:
        if (
            node["kind"] == "output"
            and not node.get("input_refs")
            and not node.get("from_ref")
        ):
            gaps.append(
                _gap(
                    "G1",
                    "warning",
                    "Output has no declared input or incoming dataflow.",
                    node["id"],
                )
            )
        if (
            node["kind"] == "analysis"
            and node.get("has_outputs")
            and not node.get("has_inputs")
            and not node.get("has_decisions")
        ):
            gaps.append(
                _gap(
                    "G2",
                    "warning",
                    "Analysis declares outputs but no inputs or decisions.",
                    node["id"],
                )
            )
        if node["kind"] == "input" and node["id"] not in outgoing_dataflow:
            gaps.append(
                _gap("G3", "info", "Declared input is unused.", node["id"])
            )
        if node["kind"] == "decision" and node["id"] not in parameterizing:
            gaps.append(
                _gap("G4", "warning", "Decision parameterizes no output.", node["id"])
            )
        if (
            run_present
            and node["kind"] == "output"
            and node["id"] not in outgoing_dataflow
            and not node.get("artifact")
        ):
            gaps.append(
                _gap(
                    "G5",
                    "info",
                    "Terminal output has no canonical materialized run artifact.",
                    node["id"],
                )
            )
        states = node.get("when_all_universes") or []
        if (
            node["kind"] == "output"
            and node.get("when")
            and states
            and None not in states
            and not any(states)
        ):
            gaps.append(
                _gap(
                    "G6",
                    "warning",
                    "Conditional output is inactive in every project universe.",
                    node["id"],
                )
            )

    if run_present:
        run_ids = run_output_ids or set()
        spec_ids = spec_output_ids or set()
        extra = sorted(run_ids - spec_ids)
        missing = sorted(spec_ids - run_ids)
        if extra or missing:
            details = []
            if extra:
                details.append(f"run-only={extra}")
            if missing:
                details.append(f"spec-only={missing}")
            gaps.append(
                _gap(
                    "G7",
                    "error",
                    "Run/spec output coverage differs: " + ", ".join(details),
                )
            )

    return gaps
