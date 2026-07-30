"""The only viewer module coupled to the released ASTRA YAML schema."""

from __future__ import annotations

import copy
import importlib.metadata
from pathlib import Path
from typing import Any


ASTRA_SPEC_VERSION = "0.0.12"


class AdapterError(ValueError):
    """Raised when an ASTRA project cannot be adapted safely."""


def _astra_api():
    try:
        from astra.helpers import load_yaml
        from astra.validation import (
            validate_analysis,
            validate_analysis_data,
            validate_universe,
            validate_universe_data,
        )
    except ImportError as error:  # pragma: no cover - packaging failure in the image
        raise AdapterError(
            "astra-tools is unavailable; Neurodesktop requires astra-tools==0.2.11"
        ) from error
    return (
        load_yaml,
        validate_analysis_data,
        validate_analysis,
        validate_universe_data,
        validate_universe,
    )


def _messages(errors: list[Any]) -> list[str]:
    return [str(error) for error in errors]


def _require_no_errors(errors: list[Any], label: str) -> None:
    if errors:
        raise AdapterError(f"{label}: {'; '.join(_messages(errors))}")


def _confined_file(path: Path, root: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise AdapterError(f"{label} is unavailable: {path}: {error}") from error
    if not resolved.is_relative_to(root):
        raise AdapterError(f"{label} escapes the ASTRA project root: {path}")
    if not resolved.is_file():
        raise AdapterError(f"{label} is not a regular file: {path}")
    return resolved


def _installed_schema_version() -> str:
    try:
        installed = importlib.metadata.version("astra-spec")
    except importlib.metadata.PackageNotFoundError as error:
        raise AdapterError("astra-spec is unavailable") from error
    if installed != ASTRA_SPEC_VERSION:
        raise AdapterError(
            f"unsupported astra-spec package {installed}; expected {ASTRA_SPEC_VERSION}"
        )
    return installed


def _check_version(data: dict[str, Any], label: str, *, required: bool) -> None:
    version = data.get("version")
    if version is None and not required:
        return
    if version != ASTRA_SPEC_VERSION:
        raise AdapterError(
            f"{label} declares ASTRA version {version!r}; expected {ASTRA_SPEC_VERSION!r}"
        )


def _resolve_analysis_tree(
    data: dict[str, Any],
    *,
    source_dir: Path,
    project_root: Path,
    scope: tuple[str, ...],
    source_dirs: dict[tuple[str, ...], Path],
    active_files: set[Path],
) -> dict[str, Any]:
    load_yaml, validate_analysis_data, _, _, _ = _astra_api()
    _require_no_errors(validate_analysis_data(data), f"invalid ASTRA schema at {'/'.join(scope)}")
    _check_version(data, f"analysis {'/'.join(scope)}", required=scope == ("root",))
    source_dirs[scope] = source_dir

    resolved = copy.deepcopy(data)
    children: dict[str, Any] = {}
    for child_id, child in (data.get("analyses") or {}).items():
        child_scope = (*scope, child_id)
        external_path = child.get("path") if isinstance(child, dict) else None
        if external_path:
            child_file = _confined_file(
                source_dir / str(external_path) / "astra.yaml",
                project_root,
                f"analysis {child_id}",
            )
            if child_file in active_files:
                raise AdapterError(f"external analysis cycle reaches {child_file}")
            loaded = load_yaml(child_file)
            if not isinstance(loaded, dict):
                raise AdapterError(f"analysis {child_id} is not a YAML mapping")
            active_files.add(child_file)
            child_resolved = _resolve_analysis_tree(
                loaded,
                source_dir=child_file.parent,
                project_root=project_root,
                scope=child_scope,
                source_dirs=source_dirs,
                active_files=active_files,
            )
            active_files.remove(child_file)
            child_resolved["path"] = str(external_path)
            children[child_id] = child_resolved
        else:
            if not isinstance(child, dict):
                raise AdapterError(f"inline analysis {child_id} is not a mapping")
            children[child_id] = _resolve_analysis_tree(
                child,
                source_dir=source_dir,
                project_root=project_root,
                scope=child_scope,
                source_dirs=source_dirs,
                active_files=active_files,
            )
    if children:
        resolved["analyses"] = children
    return resolved


def _merge_mapping(
    inherited: dict[str, Any], inline: dict[str, Any], label: str
) -> dict[str, Any]:
    result = copy.deepcopy(inherited)
    for key, value in inline.items():
        if key in result and result[key] != value:
            raise AdapterError(f"{label} conflicts for {key}")
        result[key] = copy.deepcopy(value)
    return result


def _universe_reference_path(name: str, analysis_dir: Path, project_root: Path) -> Path:
    candidate = Path(name)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise AdapterError(f"child universe reference is not confined: {name}")
    if candidate.suffix not in {".yaml", ".yml"}:
        candidate = candidate.with_suffix(".yaml")
    return _confined_file(
        analysis_dir / "universes" / candidate,
        project_root,
        f"child universe {name}",
    )


def _resolve_universe_tree(
    universe: dict[str, Any],
    analysis: dict[str, Any],
    *,
    project_root: Path,
    source_dirs: dict[tuple[str, ...], Path],
    scope: tuple[str, ...] = ("root",),
    active_files: set[Path] | None = None,
) -> dict[str, Any]:
    load_yaml, _, _, validate_universe_data, _ = _astra_api()
    active_files = active_files if active_files is not None else set()
    result = copy.deepcopy(universe)
    result_children: dict[str, Any] = {}
    analysis_children = analysis.get("analyses") or {}

    for child_id, child_node in (universe.get("analyses") or {}).items():
        if child_id not in analysis_children:
            result_children[child_id] = copy.deepcopy(child_node)
            continue
        if not isinstance(child_node, dict):
            raise AdapterError(f"universe node {child_id} is not a mapping")
        child_scope = (*scope, child_id)
        merged = copy.deepcopy(child_node)
        reference = child_node.get("universe")
        active_reference = None
        if reference:
            ref_path = _universe_reference_path(
                str(reference), source_dirs[child_scope], project_root
            )
            if ref_path in active_files:
                raise AdapterError(f"child universe cycle reaches {ref_path}")
            referenced = load_yaml(ref_path)
            if not isinstance(referenced, dict):
                raise AdapterError(f"child universe {ref_path} is not a YAML mapping")
            _require_no_errors(
                validate_universe_data(referenced),
                f"invalid child universe schema at {ref_path}",
            )
            active_files.add(ref_path)
            active_reference = ref_path
            merged["decisions"] = _merge_mapping(
                referenced.get("decisions") or {},
                child_node.get("decisions") or {},
                f"child universe {child_id} decision",
            )
            merged["analyses"] = _merge_mapping(
                referenced.get("analyses") or {},
                child_node.get("analyses") or {},
                f"child universe {child_id} analysis",
            )
        merged = _resolve_universe_tree(
            merged,
            analysis_children[child_id],
            project_root=project_root,
            source_dirs=source_dirs,
            scope=child_scope,
            active_files=active_files,
        )
        if active_reference is not None:
            active_files.remove(active_reference)
        result_children[child_id] = merged
    if result_children:
        result["analyses"] = result_children
    return result


def _condition_state(when: Any, selections: dict[str, str]) -> bool | None:
    if not when:
        return True
    conditions = [when] if isinstance(when, str) else list(when)
    result = True
    for condition in conditions:
        negate = str(condition).startswith("~")
        reference = str(condition).lstrip("~")
        if "." not in reference:
            return None
        decision_id, option_id = reference.split(".", 1)
        if decision_id not in selections:
            return None
        matched = selections[decision_id] == option_id
        result = result and (not matched if negate else matched)
    return result


def _baseline_universe_node(
    analysis: dict[str, Any], ancestor_selections: dict[str, str] | None = None
) -> dict[str, Any]:
    ancestor_selections = dict(ancestor_selections or {})
    local: dict[str, str] = {}
    pending = dict(analysis.get("decisions") or {})
    while pending:
        progressed = False
        for decision_id, decision in list(pending.items()):
            if decision.get("from"):
                pending.pop(decision_id)
                progressed = True
                continue
            state = _condition_state(
                decision.get("when"), {**ancestor_selections, **local}
            )
            if state is None:
                continue
            pending.pop(decision_id)
            progressed = True
            if not state:
                continue
            default = decision.get("default")
            if default is None:
                raise AdapterError(
                    f"baseline universe needs a default for active decision {decision_id}"
                )
            local[decision_id] = default
        if not progressed:
            raise AdapterError(
                "baseline universe contains decisions whose conditions cannot be evaluated"
            )

    node: dict[str, Any] = {"decisions": local}
    children = {}
    effective = {**ancestor_selections, **local}
    for child_id, child in (analysis.get("analyses") or {}).items():
        children[child_id] = _baseline_universe_node(child, effective)
    if children:
        node["analyses"] = children
    return node


def _scope_name(scope: tuple[str, ...]) -> str:
    return "/".join(scope)


def _entity_id(kind: str, scope: tuple[str, ...], local_id: str) -> str:
    return f"{kind}:{_scope_name(scope)}/{local_id}"


def _universe_nodes_by_scope(
    node: dict[str, Any], scope: tuple[str, ...] = ("root",)
) -> dict[tuple[str, ...], dict[str, Any]]:
    result = {scope: node}
    for child_id, child in (node.get("analyses") or {}).items():
        result.update(_universe_nodes_by_scope(child, (*scope, child_id)))
    return result


def _collect_entities(
    analysis: dict[str, Any],
    universe_nodes: dict[tuple[str, ...], dict[str, Any]],
    all_universe_nodes: list[dict[tuple[str, ...], dict[str, Any]]],
    *,
    scope: tuple[str, ...] = ("root",),
    parent: str | None = None,
    inherited: dict[str, str] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = result or {"analyses": [], "inputs": [], "outputs": [], "decisions": [], "insights": [], "evidence": []}
    inherited = dict(inherited or {})
    scope_id = _scope_name(scope)
    analysis_id = f"analysis:{scope_id}"
    universe_node = universe_nodes.get(scope, {})
    local_selections = dict(universe_node.get("decisions") or {})
    effective = {**inherited, **local_selections}
    for decision_id, decision in (analysis.get("decisions") or {}).items():
        reference = decision.get("from")
        if reference:
            target_id = str(reference).rsplit("/", 1)[-1]
            if target_id in inherited:
                effective[decision_id] = inherited[target_id]

    result["analyses"].append(
        {
            "id": analysis_id,
            "local_id": scope[-1],
            "scope": scope_id,
            "parent": parent,
            "label": analysis.get("name") or scope[-1],
            "description": analysis.get("description"),
            "has_inputs": bool(analysis.get("inputs")),
            "has_decisions": bool(analysis.get("decisions")),
            "has_outputs": bool(analysis.get("outputs")),
        }
    )

    for item in analysis.get("inputs") or []:
        result["inputs"].append(
            {
                "id": _entity_id("input", scope, item["id"]),
                "local_id": item["id"],
                "scope": scope,
                "parent": analysis_id,
                "label": item.get("label") or item["id"],
                "description": item.get("description"),
                "sub_kind": item.get("type"),
                "source": item.get("source"),
                "from": item.get("from"),
            }
        )

    for decision_id, decision in (analysis.get("decisions") or {}).items():
        selected = local_selections.get(decision_id)
        if decision.get("from"):
            selected = effective.get(decision_id)
        result["decisions"].append(
            {
                "id": _entity_id("decision", scope, decision_id),
                "local_id": decision_id,
                "scope": scope,
                "parent": analysis_id,
                "label": decision.get("label") or decision_id,
                "description": decision.get("rationale"),
                "when": decision.get("when"),
                "when_satisfied": _condition_state(decision.get("when"), effective),
                "selected": selected,
                "options": [
                    {
                        "id": option_id,
                        "label": option.get("label") or option_id,
                        "description": option.get("description"),
                        "selected": option_id == selected,
                        "insights": list(option.get("insights") or []),
                    }
                    for option_id, option in (decision.get("options") or {}).items()
                ],
                "from": decision.get("from"),
            }
        )

    all_effective: list[dict[str, str]] = []
    for nodes in all_universe_nodes:
        selections: dict[str, str] = {}
        for depth in range(1, len(scope) + 1):
            selections.update((nodes.get(scope[:depth], {}).get("decisions") or {}))
        all_effective.append(selections)

    for item in analysis.get("outputs") or []:
        recipe = item.get("recipe") or None
        result["outputs"].append(
            {
                "id": _entity_id("output", scope, item["id"]),
                "local_id": item["id"],
                "scope": scope,
                "parent": analysis_id,
                "label": item.get("label") or item["id"],
                "description": item.get("description"),
                "sub_kind": "dataset" if item.get("type") == "data" else item.get("type"),
                "input_refs": list(item.get("inputs") or []),
                "decision_refs": list(item.get("decisions") or []),
                "from": item.get("from"),
                "recipe": recipe.get("command") if recipe else None,
                "container": (recipe or {}).get("container") or analysis.get("container"),
                "resources": copy.deepcopy((recipe or {}).get("resources")),
                "when": item.get("when"),
                "when_satisfied": _condition_state(item.get("when"), effective),
                "when_all_universes": [
                    _condition_state(item.get("when"), selections)
                    for selections in all_effective
                ],
                "effective_selections": effective,
            }
        )

    for insight_kind, collection_name in (("insight", "prior_insights"), ("finding", "findings")):
        for insight_id, insight in (analysis.get(collection_name) or {}).items():
            internal_id = _entity_id(insight_kind, scope, insight_id)
            result["insights"].append(
                {
                    "id": internal_id,
                    "local_id": insight_id,
                    "scope": scope,
                    "parent": analysis_id,
                    "kind": insight_kind,
                    "label": insight.get("label") or insight_id,
                    "description": insight.get("claim"),
                    "created_at": str(insight.get("created_at")),
                }
            )
            for evidence in insight.get("evidence") or []:
                sources = int(bool(evidence.get("doi"))) + int(bool(evidence.get("artifact")))
                warnings = [] if sources == 1 else ["evidence-source-cardinality"]
                result["evidence"].append(
                    {
                        "id": _entity_id(
                            "evidence", scope, f"{collection_name}/{insight_id}/{evidence['id']}"
                        ),
                        "local_id": evidence["id"],
                        "scope": scope,
                        "parent": analysis_id,
                        "owner": internal_id,
                        "owner_kind": insight_kind,
                        "label": evidence.get("doi") or evidence.get("artifact") or evidence["id"],
                        "description": (evidence.get("quote") or {}).get("exact"),
                        "doi": evidence.get("doi"),
                        "artifact_ref": evidence.get("artifact"),
                        "warnings": warnings,
                    }
                )

    for child_id, child in (analysis.get("analyses") or {}).items():
        _collect_entities(
            child,
            universe_nodes,
            all_universe_nodes,
            scope=(*scope, child_id),
            parent=analysis_id,
            inherited=effective,
            result=result,
        )
    return result


def _load_universe(
    path: Path,
    *,
    project_root: Path,
    analysis: dict[str, Any],
    source_dirs: dict[tuple[str, ...], Path],
) -> dict[str, Any]:
    load_yaml, _, _, validate_universe_data, validate_universe = _astra_api()
    universe_path = _confined_file(path, project_root, "universe")
    data = load_yaml(universe_path)
    if not isinstance(data, dict):
        raise AdapterError(f"universe {universe_path} is not a YAML mapping")
    _require_no_errors(validate_universe_data(data), f"invalid universe schema at {universe_path}")
    resolved = _resolve_universe_tree(
        data,
        analysis,
        project_root=project_root,
        source_dirs=source_dirs,
        active_files={universe_path},
    )
    _require_no_errors(validate_universe(resolved, analysis), f"invalid universe at {universe_path}")
    return resolved


def adapt_project(
    spec_path: str | Path, universe_path: str | Path | None = None
) -> dict[str, Any]:
    """Validate, confine, resolve, and normalize an ASTRA project."""

    load_yaml, _, validate_analysis, _, validate_universe = _astra_api()
    _installed_schema_version()
    try:
        spec = Path(spec_path).expanduser().resolve(strict=True)
    except OSError as error:
        raise AdapterError(f"ASTRA spec is unavailable: {spec_path}: {error}") from error
    if not spec.is_file():
        raise AdapterError(f"ASTRA spec is not a regular file: {spec}")
    project_root = spec.parent
    raw = load_yaml(spec)
    if not isinstance(raw, dict):
        raise AdapterError("ASTRA spec is not a YAML mapping")
    source_dirs: dict[tuple[str, ...], Path] = {}
    resolved_analysis = _resolve_analysis_tree(
        raw,
        source_dir=project_root,
        project_root=project_root,
        scope=("root",),
        source_dirs=source_dirs,
        active_files={spec},
    )
    _require_no_errors(
        validate_analysis(raw, base_path=project_root),
        "invalid ASTRA semantics",
    )

    baseline = universe_path is None
    if baseline:
        universe = {"id": "baseline", **_baseline_universe_node(resolved_analysis)}
        _require_no_errors(
            validate_universe(universe, resolved_analysis),
            "invalid derived baseline universe",
        )
        selected_path = None
    else:
        candidate = Path(universe_path).expanduser()
        if not candidate.is_absolute():
            candidate = project_root / candidate
        selected_path = _confined_file(candidate, project_root, "universe")
        universe = _load_universe(
            selected_path,
            project_root=project_root,
            analysis=resolved_analysis,
            source_dirs=source_dirs,
        )

    all_universes = [universe]
    universe_dir = project_root / "universes"
    if universe_dir.is_dir():
        for candidate in sorted((*universe_dir.glob("*.yaml"), *universe_dir.glob("*.yml"))):
            resolved_candidate = candidate.resolve(strict=True)
            if selected_path is not None and resolved_candidate == selected_path:
                continue
            loaded = _load_universe(
                resolved_candidate,
                project_root=project_root,
                analysis=resolved_analysis,
                source_dirs=source_dirs,
            )
            all_universes.append(loaded)

    universe_nodes = _universe_nodes_by_scope(universe)
    all_universe_nodes = [_universe_nodes_by_scope(item) for item in all_universes]
    entities = _collect_entities(
        resolved_analysis,
        universe_nodes,
        all_universe_nodes,
    )
    entities.update(
        {
            "project_root": str(project_root),
            "spec_path": str(spec),
            "universe_id": universe.get("id") or "baseline",
            "universe_path": str(selected_path) if selected_path else None,
            "baseline": baseline,
            "schema_version": ASTRA_SPEC_VERSION,
        }
    )
    return entities
