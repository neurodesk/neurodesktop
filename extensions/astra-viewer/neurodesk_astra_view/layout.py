"""Semantic layer assignment for the ASTRA provenance graph.

Layering by hop count from the nearest root — what generic breadth-first
graph layouts do — puts a producer and its consumer on the same row whenever
they sit the same distance from *different* roots, so the graph draws
dataflow arrows pointing backwards along a row, claiming an ordering it
immediately contradicts. Discovery-order siblings are as bad: a decision that
parameterizes several outputs crosses its own edges by construction.

This module answers both with a rank and an order per node. Every ASTRA edge
already points from the earlier node to the later one — a producer to its
consumer, a decision to the output it parameterizes, an insight to the
decision it justifies, evidence to the claim it supports, an output to the
artifact it publishes — so a single longest-path pass over the whole edge set
lays the graph out correctly, and barycenter sweeps then order each rank.

The SVG renderer only turns the pair into coordinates, compacting rows over
whatever the active mode draws. Keeping the arithmetic here rather than in
the frontend is what makes it testable; projection.py runs it once per view
over that view's subgraph.
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from typing import Any


#: Barycenter passes. Ordering settles within two or three on graphs this
#: size; a fourth is cheap and keeps wider graphs stable.
_SWEEPS = 4

#: Compound parents are positioned by their children, never placed directly.
_CONTAINER_KIND = "analysis"


def _adjacency(
    ids: list[str], edges: list[dict[str, Any]]
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    known = set(ids)
    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source = edge["source"]
        target = edge["target"]
        if source == target or source not in known or target not in known:
            continue
        if target in outgoing[source]:
            continue
        outgoing[source].append(target)
        incoming[target].append(source)
    return incoming, outgoing


def _ranks(
    ids: list[str],
    incoming: dict[str, list[str]],
    outgoing: dict[str, list[str]],
) -> dict[str, int]:
    """Longest-path rank per node, tolerating a cycle rather than hanging.

    A consumer must outrank every producer it depends on, so the rank is the
    longest path to the node rather than the shortest.
    """
    pending = {node_id: len(incoming[node_id]) for node_id in ids}
    rank = dict.fromkeys(ids, 0)
    ready = [node_id for node_id in ids if not pending[node_id]]
    heapq.heapify(ready)
    resolved: set[str] = set()
    while ready:
        node_id = heapq.heappop(ready)
        resolved.add(node_id)
        for target in outgoing[node_id]:
            rank[target] = max(rank[target], rank[node_id] + 1)
            pending[target] -= 1
            if not pending[target]:
                heapq.heappush(ready, target)

    # Anything left sits on a cycle the released validators should have
    # rejected. Rank it under whatever did resolve so the graph still draws
    # instead of the viewer hanging on a malformed spec.
    for node_id in sorted(set(ids) - resolved):
        settled = [rank[source] for source in incoming[node_id] if source in resolved]
        rank[node_id] = max(settled, default=-1) + 1

    # Longest paths strand every source on the top row, so a citation backing
    # a late finding would sit rows above the outputs beside it, trailing an
    # edge across the whole graph. A node nothing feeds belongs directly above
    # the earliest thing it feeds. Only sources move, and only downwards, so
    # one pass settles it: a source's successors all have a predecessor and
    # are therefore not themselves sources.
    for node_id in ids:
        if incoming[node_id] or not outgoing[node_id]:
            continue
        rank[node_id] = min(rank[target] for target in outgoing[node_id]) - 1

    # The renderer compacts rows over what it draws, so ranks only have to be
    # ordered — but contiguity keeps `rank - 1` meaningful to the sweeps below.
    used = sorted(set(rank.values()))
    compact = {value: index for index, value in enumerate(used)}
    return {node_id: compact[value] for node_id, value in rank.items()}


def _ordered(
    sequence: list[str],
    neighbours: dict[str, list[str]],
    reference: dict[str, int],
    parent_of: dict[str, str | None],
) -> list[str]:
    """Reorder one rank by the mean position of each node's neighbours.

    A node with no neighbour in the reference rank keeps its current place
    rather than collapsing to one end.
    """
    current = {node_id: index for index, node_id in enumerate(sequence)}
    barycenter = {}
    for node_id in sequence:
        positions = [
            reference[other] for other in neighbours[node_id] if other in reference
        ]
        barycenter[node_id] = (
            sum(positions) / len(positions) if positions else float(current[node_id])
        )

    # Keep one analysis's members contiguous within a rank: interleaving two
    # analyses draws their compound boxes overlapping each other.
    grouped: dict[str | None, list[float]] = defaultdict(list)
    for node_id in sequence:
        grouped[parent_of.get(node_id)].append(barycenter[node_id])
    group_mean = {
        parent: sum(values) / len(values) for parent, values in grouped.items()
    }

    return sorted(
        sequence,
        key=lambda node_id: (
            group_mean[parent_of.get(node_id)],
            barycenter[node_id],
            current[node_id],
        ),
    )


def assign_layout(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    """Give every placed node a ``rank`` and an ``order``, in place.

    Compound ``analysis`` containers are sized by their children, so they are
    ranked ``None``; the projection stands a placed ``stage`` node in for
    each of them instead of drawing the container.
    """
    placed = [node for node in nodes if node["kind"] != _CONTAINER_KIND]
    ids = [node["id"] for node in placed]
    incoming, outgoing = _adjacency(ids, edges)
    rank = _ranks(ids, incoming, outgoing)
    parent_of = {node["id"]: node.get("parent") for node in placed}
    declared = {node_id: index for index, node_id in enumerate(ids)}

    rows: dict[int, list[str]] = defaultdict(list)
    for node_id in ids:
        rows[rank[node_id]].append(node_id)
    sequences = {
        value: sorted(members, key=lambda node_id: declared[node_id])
        for value, members in rows.items()
    }
    levels = sorted(sequences)

    for _ in range(_SWEEPS):
        for level in levels[1:]:
            reference = {
                node_id: index
                for index, node_id in enumerate(sequences[level - 1])
            }
            sequences[level] = _ordered(
                sequences[level], incoming, reference, parent_of
            )
        for level in reversed(levels[:-1]):
            reference = {
                node_id: index
                for index, node_id in enumerate(sequences[level + 1])
            }
            sequences[level] = _ordered(
                sequences[level], outgoing, reference, parent_of
            )

    order = {
        node_id: index
        for members in sequences.values()
        for index, node_id in enumerate(members)
    }
    for node in nodes:
        if node["kind"] == _CONTAINER_KIND:
            node["rank"] = None
            node["order"] = None
        else:
            node["rank"] = rank[node["id"]]
            node["order"] = order[node["id"]]
