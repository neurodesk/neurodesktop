"""Presentation projection of the semantic ASTRA graph.

The semantic graph built by :mod:`neurodesk_astra_view.graph` keeps one node
per declared record. That is the faithful model — the inspector, the gap
rules, and the run overlay all depend on it — but drawn directly it stops
being readable somewhere around a dozen outputs: every input fans an edge to
every consumer, every decision crosses the canvas once per output it
parameterizes, and the picture the reader came for drowns in plumbing.

This module derives the *drawn* graph from the semantic one:

- one ``stage`` node per analysis, through which that analysis's inputs flow
  and from which its outputs are produced, so cross products collapse into
  paths through a stage;
- ``from``-aliased inputs folded into their canonical source, so a record a
  child scope re-exports draws once instead of once per scope;
- inputs grouped by id family once they would crowd a row, outputs grouped by
  (type, recipe family, decision contract) with a second compaction pass
  bounding the total, findings grouped past a handful;
- one collapsed, expandable ``decision-cluster`` per stage holding that
  stage's decisions with their selected values;
- evidence folded into the finding or insight it backs rather than drawn as
  nodes of its own;
- a single synthetic ``result`` node the findings conclude in, so the graph
  reads top-to-bottom as data -> stages -> outputs -> findings -> result.

Every projection node carries ``members``: the semantic node ids it stands
for. The renderer draws the projection and uses the semantic nodes only for
the inspector.

Layout stays in Python (see :mod:`neurodesk_astra_view.layout`): each node
carries a ``rank``/``order`` pair for the dataflow-centred views and a
separate ``evidence_rank``/``evidence_order`` pair for the evidence view,
which draws a different subgraph. Decisions, decision clusters, and nodes
absent from a view rank ``None`` in it; the renderer places clusters beside
their stage.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .layout import assign_layout


#: Past this many canonical inputs, same-family inputs collapse into groups.
MAX_INPUT_NODES = 8

#: Outputs sharing type, recipe family, and decision contract collapse into
#: one group node once at least this many of them agree.
OUTPUT_GROUP_THRESHOLD = 4

#: Hard bound on drawn output nodes; a compaction pass merges same-category
#: nodes until the picture fits.
MAX_OUTPUT_NODES = 12

#: Past this many findings per analysis, they draw as one group node.
MAX_FINDING_NODES = 6

#: Projection edge kinds that participate in the dataflow layout.
PRIMARY_EDGE_KINDS = ("flow", "produces", "supports", "concludes")


def _humanize(value: str) -> str:
    return re.sub(
        r"\b\w", lambda match: match.group(0).upper(), re.sub(r"[_-]+", " ", value)
    )


def _local(semantic_id: str) -> str:
    """``output:root/bet_brain`` -> ``bet_brain``."""
    return semantic_id.rsplit("/", 1)[-1]


def _input_family(local_id: str) -> str:
    normalized = re.sub(r"_(?:pre|post)$", "", local_id)
    tokens = normalized.split("_")
    return "_".join(tokens[:-1]) if len(tokens) > 1 else normalized


def _recipe_family(recipe: str | None) -> str | None:
    command = (recipe or "").strip()
    if not command:
        return None
    tokens = command.split()
    for token in tokens:
        if re.search(r"\.(?:py|r|jl|sh)$", token, re.IGNORECASE):
            return token
    return " ".join(tokens[:2])


def _common_prefix(sequences: list[list[str]]) -> list[str]:
    common: list[str] = []
    for index, token in enumerate(sequences[0] if sequences else []):
        if any(len(parts) <= index or parts[index] != token for parts in sequences):
            break
        common.append(token)
    return common


def _output_group_label(members: list[dict[str, Any]]) -> str:
    if len(members) == 1:
        return members[0]["label"]
    prefix = "_".join(_common_prefix([_local(m["id"]).split("_") for m in members]))
    noun = (
        _humanize(prefix)
        if prefix
        else f"{_humanize(members[0].get('sub_kind') or 'output')} outputs"
    )
    return f"{noun} ×{len(members)}"


def _compact_label(labels: list[str], fallback: str) -> str:
    prefix = " ".join(_common_prefix([label.split() for label in labels]))
    useful = prefix if len(prefix.split()) >= 2 else fallback
    return f"{useful} ×{len(labels)}"


def _aggregate_status(members: list[dict[str, Any]]) -> str:
    statuses = {member.get("status") or "unknown" for member in members}
    if len(statuses) == 1:
        return statuses.pop()
    if any("fail" in status or "mismatch" in status for status in statuses):
        return "failed"
    return "mixed"


class _Projection:
    """Accumulates projection nodes and de-duplicated edges."""

    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self._edge_seen: set[str] = set()
        self._node_ids: set[str] = set()

    def add_node(self, node: dict[str, Any]) -> dict[str, Any]:
        base = node["id"]
        suffix = 2
        while node["id"] in self._node_ids:
            node["id"] = f"{base}-{suffix}"
            suffix += 1
        self._node_ids.add(node["id"])
        self.nodes.append(node)
        return node

    def add_edge(
        self,
        kind: str,
        source: str | None,
        target: str | None,
        *,
        parent: str | None = None,
        label: str | None = None,
    ) -> None:
        if not source or not target or source == target:
            return
        signature = f"{kind}\0{source}\0{target}\0{parent or ''}"
        if signature in self._edge_seen:
            return
        self._edge_seen.add(signature)
        edge: dict[str, Any] = {
            "id": f"view-edge:{len(self.edges) + 1}",
            "kind": kind,
            "source": source,
            "target": target,
        }
        if parent is not None:
            edge["parent"] = parent
        if label is not None:
            edge["label"] = label
        self.edges.append(edge)


def _view_node(
    semantic: dict[str, Any], kind: str, **extra: Any
) -> dict[str, Any]:
    node = {
        "id": f"view:{semantic['id']}",
        "kind": kind,
        "label": semantic["label"],
        "description": semantic.get("description"),
        "scope": semantic.get("parent"),
        "meta": [],
        "members": [semantic["id"]],
        "status": semantic.get("status") or "unknown",
    }
    node.update(extra)
    return node


def _group_node(
    identifier: str,
    kind: str,
    label: str,
    members: list[dict[str, Any]],
    *,
    description: str | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "kind": kind,
        "label": label,
        "description": description
        or "Presentation group generated to keep the provenance overview readable.",
        "scope": members[0].get("parent"),
        "meta": [f"members: {', '.join(_local(m['id']) for m in members)}"],
        "members": [member["id"] for member in members],
        "status": _aggregate_status(members),
    }


def _project_inputs(view: _Projection, inputs: list[dict[str, Any]]):
    """Input nodes, grouped by id family once they would crowd a row."""
    node_by_semantic: dict[str, str] = {}
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    if len(inputs) > MAX_INPUT_NODES:
        for item in inputs:
            key = (
                item.get("parent"),
                item.get("sub_kind"),
                _input_family(_local(item["id"])),
            )
            groups[key].append(item)
    grouped = {
        member["id"]
        for members in groups.values()
        if len(members) > 1
        for member in members
    }
    for key, members in groups.items():
        if len(members) < 2:
            continue
        family = key[2]
        node = view.add_node(
            _group_node(
                f"view:input-group:{family}",
                "input-group",
                _compact_label([m["label"] for m in members], _humanize(family)),
                members,
            )
        )
        for member in members:
            node_by_semantic[member["id"]] = node["id"]
    for item in inputs:
        if item["id"] in grouped:
            continue
        node = view.add_node(_view_node(item, "input"))
        if item.get("sub_kind"):
            node["meta"].append(f"type: {item['sub_kind']}")
        if item.get("source"):
            node["meta"].append(f"source: {item['source']}")
        node_by_semantic[item["id"]] = node["id"]
    return node_by_semantic


def _project_outputs(view: _Projection, outputs: list[dict[str, Any]]):
    """Outputs grouped by contract, then compacted to a drawable count."""
    node_by_semantic: dict[str, str] = {}
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for item in outputs:
        family = _recipe_family(item.get("recipe"))
        decisions = tuple(
            sorted(
                entry["decision_id"]
                for entry in item.get("selected_decisions") or []
            )
        )
        key = (
            (item.get("parent"), item.get("sub_kind"), family, decisions)
            if family
            else (item["id"],)
        )
        groups[key].append(item)

    batches: list[list[dict[str, Any]]] = []
    for members in groups.values():
        if len(members) >= OUTPUT_GROUP_THRESHOLD:
            batches.append(members)
        else:
            batches.extend([member] for member in members)

    # Compaction: merge same-category batches until the picture fits.
    if len(batches) > MAX_OUTPUT_NODES:
        def _category(item: dict[str, Any]) -> str:
            sub_kind = item.get("sub_kind")
            if sub_kind in ("figure", "table", "metric"):
                return sub_kind
            family = _recipe_family(item.get("recipe"))
            if family:
                stem = family.rsplit("/", 1)[-1]
                return re.sub(r"\.[^.]+$", "", stem)
            return sub_kind or "outputs"

        candidates: dict[tuple, list[list[dict[str, Any]]]] = defaultdict(list)
        for batch in batches:
            candidates[(batch[0].get("parent"), _category(batch[0]))].append(batch)
        mergeable = sorted(
            (entry for entry in candidates.items() if len(entry[1]) > 1),
            key=lambda entry: -len(entry[1]),
        )
        for (_, category), batch_list in mergeable:
            if len(batches) <= MAX_OUTPUT_NODES:
                break
            merged = [item for batch in batch_list for item in batch]
            batches = [batch for batch in batches if batch not in batch_list]
            batches.append(merged)

    for batch in batches:
        first = batch[0]
        if len(batch) == 1:
            node = view.add_node(_view_node(first, "output"))
            if first.get("sub_kind"):
                node["meta"].append(f"type: {first['sub_kind']}")
            if first.get("recipe"):
                node["meta"].append(f"recipe: {first['recipe']}")
        else:
            family = _recipe_family(first.get("recipe"))
            node = view.add_node(
                _group_node(
                    f"view:output-group:{_input_family(_local(first['id']))}",
                    "output-group",
                    _output_group_label(batch),
                    batch,
                    description=(
                        "Outputs sharing type, recipe family, and decision "
                        "contract, collapsed to keep the overview readable."
                    ),
                )
            )
            if family:
                node["meta"].insert(0, f"recipe: {family}")
        for member in batch:
            node_by_semantic[member["id"]] = node["id"]
    return node_by_semantic


def _fold_evidence(
    owner_view: dict[str, Any] | None, evidence: dict[str, Any]
) -> None:
    if owner_view is None:
        return
    line = evidence.get("doi") or evidence.get("label") or _local(evidence["id"])
    owner_view["meta"].append(f"evidence: {line}")
    owner_view.setdefault("evidence", []).append(evidence["id"])
    owner_view["members"].append(evidence["id"])


def _assign_view_layout(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    members: set[str],
    edge_kinds: tuple[str, ...],
    rank_key: str,
    order_key: str,
) -> None:
    """Run :func:`assign_layout` over one view's subgraph, in place.

    ``assign_layout`` writes ``rank``/``order``; copies keep the projection
    node untouched until the result is copied back under the view's keys.
    """
    placeable = [
        {"id": node["id"], "kind": "placed", "parent": node.get("scope")}
        for node in nodes
        if node["id"] in members
    ]
    view_edges = [
        edge
        for edge in edges
        if edge["kind"] in edge_kinds
        and edge["source"] in members
        and edge["target"] in members
    ]
    assign_layout(placeable, view_edges)
    placed = {node["id"]: node for node in placeable}
    for node in nodes:
        entry = placed.get(node["id"])
        node[rank_key] = entry["rank"] if entry else None
        node[order_key] = entry["order"] if entry else None


def project_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Derive the drawn presentation graph from the semantic graph."""
    by_id = {node["id"]: node for node in nodes}
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        by_kind[node["kind"]].append(node)

    view = _Projection()

    stage_by_analysis: dict[str, str] = {}
    for analysis in by_kind["analysis"]:
        stage = view.add_node(
            _view_node(
                analysis,
                "stage",
                id=f"view:stage:{analysis['id'].removeprefix('analysis:')}",
            )
        )
        stage_by_analysis[analysis["id"]] = stage["id"]

    # An input that is the target of a dataflow edge is a `from` alias:
    # inputs declare no dependencies of their own, so the edge can only mean
    # "this scope re-exports that record". Drawing the alias repeats the same
    # name once per scope; folding it into its canonical source is what keeps
    # a deep workflow from showing three copies of one catalogue.
    alias_source: dict[str, str] = {}
    for edge in edges:
        if edge["kind"] != "dataflow":
            continue
        target = by_id.get(edge["target"])
        if target is not None and target["kind"] == "input":
            alias_source[target["id"]] = edge["source"]

    def _canonical(identifier: str) -> str:
        seen: set[str] = set()
        while identifier in alias_source and identifier not in seen:
            seen.add(identifier)
            identifier = alias_source[identifier]
        return identifier

    canonical_inputs = [
        item for item in by_kind["input"] if item["id"] not in alias_source
    ]
    input_view = _project_inputs(view, canonical_inputs)
    output_view = _project_outputs(view, by_kind["output"])
    for aliased in alias_source:
        root = _canonical(aliased)
        resolved = input_view.get(root) or output_view.get(root)
        if resolved:
            input_view[aliased] = resolved

    # Dataflow: inputs and remote outputs flow into a stage; local
    # producer/consumer pairs keep a direct edge; everything else the stage
    # produces.
    has_local_producer: set[str] = set()
    for edge in edges:
        if edge["kind"] != "dataflow":
            continue
        source = by_id.get(edge["source"])
        target = by_id.get(edge["target"])
        if source is None or target is None:
            continue
        if target["kind"] != "output":
            continue  # alias edges are folded above
        target_node = output_view.get(target["id"])
        stage = stage_by_analysis.get(target.get("parent"))
        if source["kind"] == "output":
            source_node = output_view.get(source["id"])
            if source.get("parent") == target.get("parent"):
                if source_node != target_node:
                    view.add_edge("flow", source_node, target_node)
                    has_local_producer.add(target["id"])
            else:
                view.add_edge("flow", source_node, stage)
        else:
            view.add_edge("flow", input_view.get(source["id"]), stage)

    produced: dict[str, str] = {}
    for output in by_kind["output"]:
        if output["id"] in has_local_producer:
            continue
        produced[output_view[output["id"]]] = stage_by_analysis.get(
            output.get("parent")
        )
    for target_node, stage in produced.items():
        view.add_edge("produces", stage, target_node)

    # Decisions: one collapsed cluster per stage; members configure the
    # outputs they parameterize, or the stage when they parameterize nothing.
    parameterizes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if edge["kind"] == "parameterizes":
            parameterizes[edge["source"]].append(edge)
    decision_view: dict[str, str] = {}
    decisions_by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in by_kind["decision"]:
        decisions_by_scope[decision.get("parent")].append(decision)
    for analysis_id, scope_decisions in decisions_by_scope.items():
        stage = stage_by_analysis.get(analysis_id)
        cluster = view.add_node(
            {
                "id": f"view:decisions:{analysis_id}",
                "kind": "decision-cluster",
                "label": (
                    f"{len(scope_decisions)} decision"
                    f"{'' if len(scope_decisions) == 1 else 's'}"
                ),
                "description": (
                    "Methodological choices that parameterize outputs of "
                    "this analysis stage. Click to expand."
                ),
                "scope": analysis_id,
                "meta": [],
                "members": [],
                "target": stage,
                "status": "unknown",
            }
        )
        view.add_edge("configures", cluster["id"], stage)
        for decision in scope_decisions:
            selected = decision.get("selected")
            chosen = next(
                (
                    option
                    for option in decision.get("options") or []
                    if option.get("selected")
                ),
                None,
            )
            value = (chosen or {}).get("label") or selected or "unresolved"
            node = view.add_node(
                _view_node(
                    decision,
                    "decision",
                    label=f"{decision['label']} = {value}",
                    parent=cluster["id"],
                    target=stage,
                    selected=selected,
                    options=decision.get("options") or [],
                )
            )
            cluster["members"].append(node["id"])
            decision_view[decision["id"]] = node["id"]
            targets = [
                output_view.get(edge["target"])
                for edge in parameterizes[decision["id"]]
            ]
            for target_node in dict.fromkeys(filter(None, targets)):
                view.add_edge(
                    "configures", node["id"], target_node, parent=cluster["id"]
                )
            if not any(targets):
                view.add_edge("configures", node["id"], stage, parent=cluster["id"])

    insight_view: dict[str, str] = {}
    for insight in by_kind["insight"]:
        node = view.add_node(_view_node(insight, "insight"))
        insight_view[insight["id"]] = node["id"]

    finding_view: dict[str, str] = {}
    findings = by_kind["finding"]
    if len(findings) > MAX_FINDING_NODES:
        group = view.add_node(
            _group_node(
                "view:finding-group",
                "finding-group",
                f"Findings ×{len(findings)}",
                findings,
                description=" · ".join(
                    finding.get("description") or finding["label"]
                    for finding in findings
                ),
            )
        )
        for finding in findings:
            finding_view[finding["id"]] = group["id"]
    else:
        for finding in findings:
            node = view.add_node(_view_node(finding, "finding"))
            finding_view[finding["id"]] = node["id"]

    view_of = {
        **{
            semantic_id: node_id
            for mapping in (input_view, output_view, decision_view, insight_view)
            for semantic_id, node_id in mapping.items()
        },
        **finding_view,
    }
    node_index = {node["id"]: node for node in view.nodes}
    for edge in edges:
        if edge["kind"] == "justifies":
            view.add_edge(
                "informs",
                insight_view.get(edge["source"]),
                decision_view.get(edge["target"]),
            )
        elif edge["kind"] == "claims":
            view.add_edge(
                "supports",
                output_view.get(edge["source"]),
                finding_view.get(edge["target"]),
            )
        elif edge["kind"] == "supports":
            evidence = by_id.get(edge["source"])
            if evidence is not None and evidence["kind"] == "evidence":
                owner = node_index.get(view_of.get(edge["target"], ""))
                _fold_evidence(owner, evidence)

    result = view.add_node(
        {
            "id": "view:result",
            "kind": "result",
            "label": f"{meta.get('analysis_name') or 'Analysis'} result",
            "description": (
                "The terminal result assembled from this ASTRA analysis."
            ),
            "scope": None,
            "meta": (
                [f"universe: {meta['universe_id']}"]
                if meta.get("universe_id")
                else []
            ),
            "members": [],
            "status": "unknown",
        }
    )
    concluding = list(dict.fromkeys(finding_view.values()))
    if not concluding:
        with_outgoing = {
            edge["source"]
            for edge in view.edges
            if edge["kind"] in ("flow", "supports")
        }
        concluding = [
            node["id"]
            for node in view.nodes
            if node["kind"] in ("output", "output-group")
            and node["id"] not in with_outgoing
        ] or list(stage_by_analysis.values())
    for node_id in concluding:
        view.add_edge("concludes", node_id, result["id"])

    base_kinds = (
        "stage",
        "input",
        "input-group",
        "output",
        "output-group",
        "finding",
        "finding-group",
        "result",
    )
    base_members = {
        node["id"] for node in view.nodes if node["kind"] in base_kinds
    }
    _assign_view_layout(
        view.nodes, view.edges, base_members, PRIMARY_EDGE_KINDS, "rank", "order"
    )

    evidence_members = {
        node["id"]
        for node in view.nodes
        if node["kind"] in ("insight", "finding", "finding-group", "result")
    }
    evidence_members.update(
        edge["target"] for edge in view.edges if edge["kind"] == "informs"
    )
    evidence_members.update(
        edge["source"] for edge in view.edges if edge["kind"] == "supports"
    )
    _assign_view_layout(
        view.nodes,
        view.edges,
        evidence_members,
        ("informs", "configures", "supports", "concludes"),
        "evidence_rank",
        "evidence_order",
    )

    return {"nodes": view.nodes, "edges": view.edges}
