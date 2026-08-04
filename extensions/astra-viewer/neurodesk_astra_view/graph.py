"""Pure, JSON-serializable ASTRA provenance graph construction."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .adapter import AdapterError, adapt_project
from .gaps import detect_gaps
from .layout import assign_layout
from .manifest import RunManifestError, load_run, spec_only_trust
from .preview import PreviewError, preview_artifact
from .projection import project_graph


def _invalid(message: str) -> dict[str, Any]:
    return {
        "nodes": [],
        "edges": [],
        "projection": {"nodes": [], "edges": []},
        "gaps": [],
        "warnings": [],
        "errors": [message],
        "trust": spec_only_trust(),
        "meta": {"valid": False},
    }


def _node(entity: dict[str, Any], kind: str, **extra: Any) -> dict[str, Any]:
    excluded = {
        "scope",
        "local_id",
        "input_refs",
        "decision_refs",
        "effective_selections",
        "when_all_universes",
        "from",
        "has_inputs",
        "has_decisions",
        "has_outputs",
        "owner",
        "owner_kind",
        "artifact_ref",
    }
    value = {key: item for key, item in entity.items() if key not in excluded}
    value.update(
        {
            "kind": kind,
            "sub_kind": value.get("sub_kind"),
            "description": value.get("description"),
            "recipe": value.get("recipe"),
            "when": value.get("when"),
            "when_satisfied": value.get("when_satisfied"),
            "resources": value.get("resources"),
            "selected_decisions": value.get("selected_decisions", []),
            "published": value.get("published"),
            "status": value.get("status", "unknown"),
            "artifact": value.get("artifact"),
            "units": value.get("units"),
            "warnings": list(value.get("warnings") or []),
        }
    )
    value.update(extra)
    return value


def _scope_tuple(entity: dict[str, Any]) -> tuple[str, ...]:
    return tuple(entity["scope"])


def _reference_index(project: dict[str, Any]):
    index: dict[tuple[str, tuple[str, ...], str], str] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for kind, collection in (
        ("input", "inputs"),
        ("output", "outputs"),
        ("decision", "decisions"),
        ("insight", "insights"),
    ):
        for entity in project[collection]:
            entity_kind = entity.get("kind", kind)
            key_kind = "insight" if entity_kind == "insight" else entity_kind
            index[(key_kind, _scope_tuple(entity), entity["local_id"])] = entity["id"]
            by_id[entity["id"]] = entity
    return index, by_id


def _resolve_reference(
    reference: str,
    *,
    scope: tuple[str, ...],
    kinds: tuple[str, ...],
    index: dict[tuple[str, tuple[str, ...], str], str],
) -> str | None:
    if reference.startswith(("input:", "output:", "decision:", "insight:")):
        return reference if any(reference.startswith(f"{kind}:") for kind in kinds) else None

    remaining = reference
    target_scope = scope
    while remaining.startswith("../"):
        if len(target_scope) <= 1:
            return None
        target_scope = target_scope[:-1]
        remaining = remaining[3:]

    pieces = remaining.split(".")
    local_id = pieces[-1]
    scope_suffix = tuple(pieces[:-1])
    candidates = []
    if scope_suffix:
        candidates.extend(((*target_scope, *scope_suffix), ("root", *scope_suffix)))
    else:
        candidates.append(target_scope)
    for candidate_scope in candidates:
        for kind in kinds:
            found = index.get((kind, candidate_scope, local_id))
            if found:
                return found
    return None


def _edge(
    edges: list[dict[str, Any]],
    kind: str,
    source: str,
    target: str,
    *,
    label: str | None = None,
    option_id: str | None = None,
) -> None:
    identity = f"{kind}:{source}->{target}"
    if option_id:
        identity += f":{option_id}"
    value = {"id": identity, "kind": kind, "source": source, "target": target}
    if label is not None:
        value["label"] = label
    if option_id is not None:
        value["option_id"] = option_id
    if not any(item["id"] == identity for item in edges):
        edges.append(value)


def _match_run_records(
    outputs: list[dict[str, Any]], records: dict[str, dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    by_local: dict[str, list[str]] = defaultdict(list)
    by_qualified: dict[str, list[str]] = defaultdict(list)
    output_ids = {output["id"] for output in outputs}
    for output in outputs:
        by_local[output["local_id"]].append(output["id"])
        scope = tuple(output["scope"])[1:]
        qualified = ".".join((*scope, output["local_id"]))
        by_qualified[qualified].append(output["id"])

    matched: dict[str, dict[str, Any]] = {}
    canonical_run_ids: set[str] = set()
    for run_id, record in records.items():
        canonical = run_id if run_id in output_ids else None
        if canonical is None and len(by_local.get(run_id, [])) == 1:
            canonical = by_local[run_id][0]
        if canonical is None and len(by_qualified.get(run_id, [])) == 1:
            canonical = by_qualified[run_id][0]
        if canonical is None:
            canonical_run_ids.add(f"run:{run_id}")
            continue
        if canonical in matched:
            raise RunManifestError(f"multiple run records map to {canonical}")
        matched[canonical] = record
        canonical_run_ids.add(canonical)
    return matched, canonical_run_ids


def _build_valid_graph(project: dict[str, Any], run_path: str | Path | None):
    index, by_id = _reference_index(project)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for analysis in project["analyses"]:
        nodes.append(
            _node(
                analysis,
                "analysis",
                has_inputs=analysis["has_inputs"],
                has_decisions=analysis["has_decisions"],
                has_outputs=analysis["has_outputs"],
            )
        )
    for item in project["inputs"]:
        nodes.append(_node(item, "input", source=item.get("source"), from_ref=item.get("from")))
    for item in project["decisions"]:
        nodes.append(
            _node(
                item,
                "decision",
                options=item.get("options") or [],
                selected=item.get("selected"),
                from_ref=item.get("from"),
            )
        )
    for item in project["insights"]:
        nodes.append(_node(item, item["kind"], created_at=item.get("created_at")))
    for item in project["evidence"]:
        nodes.append(
            _node(
                item,
                "evidence",
                doi=item.get("doi"),
                artifact_ref=item.get("artifact_ref"),
            )
        )

    output_nodes: dict[str, dict[str, Any]] = {}
    for item in project["outputs"]:
        selected = []
        for reference in item["decision_refs"]:
            decision_id = _resolve_reference(
                reference,
                scope=_scope_tuple(item),
                kinds=("decision",),
                index=index,
            )
            if decision_id:
                decision = by_id[decision_id]
                selected.append(
                    {"decision_id": decision_id, "value": decision.get("selected")}
                )
                _edge(
                    edges,
                    "parameterizes",
                    decision_id,
                    item["id"],
                    label=decision.get("selected"),
                )
        node = _node(
            item,
            "output",
            input_refs=item["input_refs"],
            from_ref=item.get("from"),
            selected_decisions=selected,
            when_all_universes=item["when_all_universes"],
        )
        nodes.append(node)
        output_nodes[item["id"]] = node

    for item in (*project["inputs"], *project["outputs"]):
        references = list(item.get("input_refs") or [])
        if item.get("from"):
            references.append(item["from"])
        for reference in references:
            source = _resolve_reference(
                reference,
                scope=_scope_tuple(item),
                kinds=("input", "output"),
                index=index,
            )
            if source:
                _edge(edges, "dataflow", source, item["id"])

    insight_lookup = {
        (tuple(item["scope"]), item["local_id"]): item["id"]
        for item in project["insights"]
        if item["kind"] == "insight"
    }
    for decision in project["decisions"]:
        for option in decision.get("options") or []:
            for reference in option.get("insights") or []:
                insight_id = _resolve_reference(
                    reference,
                    scope=_scope_tuple(decision),
                    kinds=("insight",),
                    index=index,
                ) or insight_lookup.get((_scope_tuple(decision), reference))
                if insight_id:
                    _edge(
                        edges,
                        "justifies",
                        insight_id,
                        decision["id"],
                        label=option.get("label"),
                        option_id=option["id"],
                    )
    for evidence in project["evidence"]:
        _edge(edges, "supports", evidence["id"], evidence["owner"])
        if evidence["owner_kind"] == "finding" and evidence.get("artifact_ref"):
            output_id = _resolve_reference(
                evidence["artifact_ref"],
                scope=_scope_tuple(evidence),
                kinds=("output",),
                index=index,
            )
            if output_id:
                _edge(edges, "claims", output_id, evidence["owner"])

    overlay = None
    matched: dict[str, dict[str, Any]] = {}
    canonical_run_ids: set[str] = set()
    if run_path is not None:
        overlay = load_run(
            run_path,
            project_root=project["project_root"],
            universe_id=project["universe_id"],
        )
        matched, canonical_run_ids = _match_run_records(
            project["outputs"], overlay["records"]
        )
        for output_id, record in matched.items():
            node = output_nodes[output_id]
            node["status"] = record["status"]
            node["published"] = bool(record.get("artifact_path"))
            if record.get("units") is not None:
                node["units"] = record["units"]
            node["run"] = {
                key: record.get(key)
                for key in (
                    "started_at",
                    "ended_at",
                    "command",
                    "container",
                    "log_excerpt",
                )
                if record.get(key) is not None
            }
            if record.get("artifact_path"):
                artifact = {
                    "path": record["artifact_relative"],
                    "sha256": record.get("sha256"),
                    "size": record.get("size"),
                    "media_type": record.get("media_type"),
                }
                try:
                    artifact["preview"] = preview_artifact(
                        record["artifact_path"],
                        record.get("preview_root") or project["project_root"],
                        node.get("sub_kind"),
                        units=node.get("units"),
                    )
                except PreviewError as error:
                    node["warnings"].append(str(error))
                node["artifact"] = artifact
                artifact_id = f"artifact:{output_id.removeprefix('output:')}"
                nodes.append(
                    {
                        "id": artifact_id,
                        "kind": "artifact",
                        "sub_kind": node.get("sub_kind"),
                        "label": record["artifact_relative"],
                        "parent": node.get("parent"),
                        "description": None,
                        "recipe": None,
                        "when": None,
                        "when_satisfied": None,
                        "resources": None,
                        "selected_decisions": [],
                        "published": True,
                        "status": record["status"],
                        "artifact": artifact,
                        "units": record.get("units"),
                        "warnings": [],
                    }
                )
                _edge(edges, "publishes", output_id, artifact_id)

    run_present = overlay is not None
    spec_output_ids = set(output_nodes)
    gaps = detect_gaps(
        nodes,
        edges,
        run_present=run_present,
        run_output_ids=canonical_run_ids,
        spec_output_ids=spec_output_ids,
    )
    warnings_by_node: dict[str, list[str]] = defaultdict(list)
    for gap in gaps:
        if gap.get("node_id"):
            warnings_by_node[gap["node_id"]].append(gap["id"])
    for node in nodes:
        node["warnings"].extend(warnings_by_node.get(node["id"], []))
        node.pop("input_refs", None)
        node.pop("from_ref", None)
        node.pop("when_all_universes", None)
        node.pop("has_inputs", None)
        node.pop("has_decisions", None)
        node.pop("has_outputs", None)

    assign_layout(nodes, edges)

    root_analysis = next(
        (item for item in project["analyses"] if item["parent"] is None), None
    )
    meta = {
        "valid": True,
        "schema_version": project["schema_version"],
        "universe_id": project["universe_id"],
        "analysis_name": root_analysis["label"] if root_analysis else None,
        "baseline": project["baseline"],
        "run_source": overlay["source"] if overlay else None,
    }
    return {
        "nodes": nodes,
        "edges": edges,
        "projection": project_graph(nodes, edges, meta),
        "gaps": gaps,
        "warnings": list(project.get("version_warnings") or []),
        "errors": [],
        "trust": overlay["trust"] if overlay else spec_only_trust(),
        "meta": meta,
    }


def build_graph(
    spec_path: str | Path,
    universe_path: str | Path | None = None,
    run_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the viewer's complete JSON model without any widget state."""

    project = None
    try:
        project = adapt_project(spec_path, universe_path)
        return _build_valid_graph(project, run_path)
    except (AdapterError, RunManifestError, PreviewError, OSError, ValueError) as error:
        invalid = _invalid(str(error))
        # A declared-version drift is the context that explains many
        # validation failures, so it survives into the invalid graph.
        drift = getattr(error, "version_warnings", None)
        if drift is None and project is not None:
            drift = project.get("version_warnings")
        invalid["warnings"] = list(drift or [])
        return invalid
