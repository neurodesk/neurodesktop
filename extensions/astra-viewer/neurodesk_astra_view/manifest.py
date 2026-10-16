"""Fail-closed Lightcone manifest and RO-Crate ingestion."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


STATUS_VALUES = {"unknown", "not_run", "ok", "behind", "stale", "failed"}


class RunManifestError(ValueError):
    """Raised when run evidence is ambiguous, invalid, or tampered with."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RunManifestError(f"cannot read run JSON from {path}: {error}") from error


def _sha256_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise RunManifestError(f"cannot hash run artifact {path}: {error}") from error
    return digest.hexdigest(), size


def _annex_pointer(path: Path) -> bool:
    if path.stat().st_size > 32 * 1024:
        return False
    with path.open("rb") as stream:
        return stream.read(len(b"/annex/objects/")) == b"/annex/objects/"


def _confined_artifact(path: Path, root: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RunManifestError(f"{label} is unavailable: {path}: {error}") from error
    if not resolved.is_relative_to(root):
        raise RunManifestError(f"{label} escapes the ASTRA project root: {path}")
    if not resolved.is_file():
        raise RunManifestError(f"{label} is not a regular file: {path}")
    return resolved


def _normalize_status(value: Any, *, artifact_present: bool = False) -> str:
    if value is None:
        return "ok" if artifact_present else "unknown"
    normalized = str(value).strip().lower().replace("-", "_")
    aliases = {
        "success": "ok",
        "succeeded": "ok",
        "complete": "ok",
        "completed": "ok",
        "pending": "not_run",
        "notrun": "not_run",
        # `lc status` spells an unmaterialized output "missing"; without this
        # it read as "unknown", which claims we could not tell.
        "missing": "not_run",
        "error": "failed",
        "cancelled": "failed",
        "canceled": "failed",
        "timeout": "failed",
        "timed_out": "failed",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in STATUS_VALUES else "unknown"


#: "Selected analysis" read as a restatement of the universe picker sitting
#: directly above the badge, rather than as a claim about execution. Every
#: label now names the run state, which is the only thing trust measures.
def _trust_label(level: str) -> str:
    return {
        "spec-only": "Not executed",
        "executed-unverified": "Executed, unverified",
        "executed-verified": "Executed and verified",
        "provenance-mismatch": "Provenance mismatch",
    }.get(level, f"Unknown trust level: {level}")


def _record_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    universes = data.get("universes")
    if isinstance(universes, list):
        records = []
        for universe in universes:
            if not isinstance(universe, dict) or not isinstance(
                universe.get("outputs"), list
            ):
                continue
            for item in universe["outputs"]:
                if isinstance(item, dict):
                    records.append(
                        {
                            "universe_id": universe.get("universe_id"),
                            "output_id": item.get("output_id"),
                            "status": item.get("status"),
                            "command": item.get("recipe_command"),
                            **item,
                        }
                    )
        return records
    outputs = data.get("outputs")
    if isinstance(outputs, list):
        return [item for item in outputs if isinstance(item, dict)]
    if isinstance(outputs, dict):
        records = []
        for output_id, item in outputs.items():
            if isinstance(item, dict):
                records.append({"output_id": output_id, **item})
        return records
    if any(key in data for key in ("output_id", "outputId", "node_id", "id")):
        return [data]
    manifests = data.get("manifests")
    if isinstance(manifests, list):
        return [item for item in manifests if isinstance(item, dict)]
    return []


def _rocrate_record_list(
    data: dict[str, Any], metadata_path: Path
) -> list[dict[str, Any]]:
    graph = data.get("@graph")
    if not isinstance(graph, list):
        return []
    entities = {
        item.get("@id"): item
        for item in graph
        if isinstance(item, dict) and item.get("@id")
    }
    records = []
    for action in entities.values():
        types = action.get("@type")
        types = [types] if isinstance(types, str) else list(types or [])
        if "CreateAction" not in types:
            continue
        match = re.fullmatch(
            r"Run of (?P<output>.+) \(universe=(?P<universe>[^)]+)\)",
            str(action.get("name", "")),
        )
        if not match:
            continue
        results = action.get("result") or []
        results = [results] if isinstance(results, dict) else results
        if len(results) != 1 or not isinstance(results[0], dict):
            continue
        dataset_id = results[0].get("@id")
        dataset = entities.get(dataset_id, {})
        parts = dataset.get("hasPart") or []
        parts = [parts] if isinstance(parts, dict) else parts
        part_ids = [part.get("@id") for part in parts if isinstance(part, dict)]
        if not part_ids and dataset_id:
            part_ids = [
                entity_id
                for entity_id in entities
                if str(entity_id).startswith(str(dataset_id))
                and entity_id != dataset_id
            ]
        payloads = [
            value
            for value in part_ids
            if value and not str(value).endswith(".lightcone-manifest.json")
        ]
        artifact = None
        if len(payloads) == 1:
            artifact = metadata_path.parent / str(payloads[0])
        status_ref = action.get("actionStatus")
        if isinstance(status_ref, dict):
            status_ref = status_ref.get("@id")
        records.append(
            {
                "output_id": match.group("output"),
                "universe_id": match.group("universe"),
                "artifact": str(artifact) if artifact else None,
                "status": "ok"
                if str(status_ref).endswith("CompletedActionStatus")
                else "unknown",
                "ended_at": action.get("endTime"),
            }
        )
    return records


def _artifact_candidate(record: dict[str, Any], run_file: Path) -> Path | None:
    artifact = record.get("artifact")
    if isinstance(artifact, dict):
        value = artifact.get("path") or artifact.get("relativePath")
    else:
        value = artifact
    value = value or record.get("path") or record.get("output_path")
    if not value:
        return None
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = run_file.parent / candidate
    return candidate


def _refs(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    if any(
        not isinstance(item, dict) or not isinstance(item.get("@id"), str)
        for item in values
    ):
        raise RunManifestError("invalid RO-Crate relationship")
    return [item["@id"] for item in values]


def _identifier(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(char in value for char in (":", "\0"))
    ):
        raise RunManifestError(f"invalid Lightcone identifier {value!r}")
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise RunManifestError("invalid Lightcone content identity")
    return value.removeprefix("sha256:")


def _v05_output_id(value: Any) -> str:
    value = _identifier(value)
    if "/" in value or "." in value:
        raise RunManifestError(
            "Lightcone 0.5 output id must be one component without dots"
        )
    return value


def _canonical_record(path: Path, root: Path) -> dict[str, Any] | None:
    # Unfetched annex objects retain their identity but cannot supply evidence bytes.
    if not path.exists():
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            raise RunManifestError("manifest escapes the ASTRA project root")
        return None
    path = _confined_artifact(path, root, "Lightcone manifest")
    if _annex_pointer(path):
        return None
    data = _load_json(path)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise RunManifestError("unsupported Lightcone canonical manifest")
    output = _v05_output_id(data.get("output_id"))
    universe = _identifier(data.get("universe_id"))
    if "/" in universe:
        raise RunManifestError("invalid Lightcone universe")
    digest = _digest(data.get("data_version"))
    if not re.fullmatch(r"[0-9a-f]{40,64}", str(data.get("git_sha", ""))):
        raise RunManifestError("Lightcone manifest has no source commit")
    if not isinstance(data.get("recipe"), str) or not data["recipe"]:
        raise RunManifestError("Lightcone manifest has no recipe")
    image = data.get("image")
    if image is not None and not isinstance(image, dict):
        raise RunManifestError("invalid Lightcone image attestation")
    return {
        "output_id": output,
        "universe_id": universe,
        "status": "ok",
        "executed": True,
        "sha256": digest,
        "source_commit": data["git_sha"],
        "command": data["recipe"],
        "started_at": data.get("started_at"),
        "ended_at": data.get("finished_at"),
        "container": image.get("id") if image else None,
        "hermeticity": data.get("hermeticity"),
        "artifact_path": None,
    }


def _status_v05(data: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    rows = data.get("outputs")
    if not isinstance(rows, list):
        raise RunManifestError("Lightcone status outputs must be a list")
    records = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RunManifestError("invalid Lightcone status row")
        qualified = _identifier(row.get("output"))
        universe, separator, output = qualified.partition("/")
        if not separator or qualified in seen:
            raise RunManifestError(
                f"malformed or duplicate Lightcone output {qualified}"
            )
        _v05_output_id(output)
        seen.add(qualified)
        if row.get("status") not in {"current", "behind", "stale"}:
            raise RunManifestError("invalid Lightcone freshness state")
        historical = bool(row.get("git_sha") and row.get("data_version"))
        record = (
            _canonical_record(
                root / "results" / universe / f".{output}.manifest.json", root
            )
            if historical
            else None
        )
        if historical:
            _digest(row["data_version"])
        if record and (
            record["output_id"] != output
            or record["universe_id"] != universe
            or record["source_commit"] != row.get("git_sha")
            or record["sha256"] != str(row.get("data_version")).removeprefix("sha256:")
        ):
            raise RunManifestError(
                f"status snapshot disagrees with canonical manifest for {qualified}"
            )
        record = record or {
            "output_id": output,
            "universe_id": universe,
            "executed": False,
        }
        record.update(
            status=(
                "stale"
                if row.get("foreign_write")
                else {"current": "ok", "behind": "behind", "stale": "stale"}[
                    row["status"]
                ]
            )
            if record["executed"]
            else "not_run",
            source_commit=row.get("git_sha") or None,
            foreign_write=row.get("foreign_write") or None,
            log_excerpt=row.get("why"),
        )
        records.append(record)
    return records


def _crate_v05(data: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    entities = {}
    for entity in data["@graph"]:
        if not isinstance(entity, dict) or not isinstance(entity.get("@id"), str):
            raise RunManifestError("invalid RO-Crate entity")
        if entity["@id"] in entities:
            raise RunManifestError("duplicate RO-Crate entity ID")
        entities[entity["@id"]] = entity
    records = []
    seen = set()
    for control in entities.values():
        if "ControlAction" not in _types(control):
            continue
        for action_id in _refs(control.get("object")):
            action = entities.get(action_id, {})
            if "CreateAction" not in _types(action):
                raise RunManifestError(
                    "RO-Crate control does not reference a CreateAction"
                )
            status = action.get("actionStatus")
            if isinstance(status, dict):
                status = status.get("@id")
            if status not in {
                "http://schema.org/CompletedActionStatus",
                "https://schema.org/CompletedActionStatus",
            }:
                continue
            for result_id in _refs(action.get("result")):
                result = entities.get(result_id, {})
                if "File" not in _types(result) or not result.get("subjectOf"):
                    continue
                manifests = _refs(result["subjectOf"])
                if len(manifests) != 1:
                    raise RunManifestError(
                        "output must reference one canonical manifest"
                    )
                manifest_id = _identifier(manifests[0])
                manifest_entity = entities.get(manifest_id, {})
                if _refs(manifest_entity.get("about")) != [result_id]:
                    raise RunManifestError(
                        "manifest relationship does not point back to output"
                    )
                path = root / manifest_id
                record = _canonical_record(path, root)
                if record is None:
                    continue
                if manifest_entity.get("sha256") != _sha256_size(path)[0]:
                    raise RunManifestError("RO-Crate manifest has a stale hash")
                result_id = _identifier(result_id)
                if (
                    Path(result_id).parent != Path("results") / record["universe_id"]
                    or not Path(result_id).name.startswith(record["output_id"] + ".")
                    or Path(manifest_id)
                    != Path(result_id).parent / f".{record['output_id']}.manifest.json"
                ):
                    raise RunManifestError(
                        "RO-Crate path disagrees with manifest identity"
                    )
                if result.get("version") != "sha256:" + record["sha256"]:
                    raise RunManifestError(
                        "RO-Crate output version disagrees with manifest"
                    )
                key = (record["universe_id"], record["output_id"])
                if key in seen:
                    raise RunManifestError("duplicate Lightcone output relationship")
                seen.add(key)
                artifact = root / result_id
                if artifact.exists():
                    resolved = _confined_artifact(artifact, root, "Lightcone output")
                    if _annex_pointer(resolved):
                        records.append(record)
                        continue
                    digest, size = _sha256_size(resolved)
                    if digest != record["sha256"] or result.get("sha256") != digest:
                        raise RunManifestError("Lightcone output has a stale hash")
                    if str(result.get("contentSize")) != str(size):
                        raise RunManifestError("Lightcone output has a stale size")
                    record.update(
                        artifact_path=str(resolved),
                        artifact_relative=result_id,
                        preview_root=str(root),
                        size=size,
                        media_type=result.get("encodingFormat"),
                    )
                elif not artifact.resolve().is_relative_to(root):
                    raise RunManifestError(
                        "Lightcone output escapes the ASTRA project root"
                    )
                records.append(record)
    return records


def _types(entity: dict[str, Any]) -> list[str]:
    value = entity.get("@type", [])
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RunManifestError("invalid RO-Crate entity type")
    return value


def _v05_overlay(records: list[dict[str, Any]], universe: str) -> dict[str, Any]:
    selected = {}
    for record in records:
        if record["universe_id"] != universe:
            continue
        key = record["output_id"]
        if key in selected:
            raise RunManifestError(f"duplicate Lightcone output {key}")
        selected[key] = record
    trust = spec_only_trust()
    if any(record["executed"] for record in selected.values()):
        trust.update(
            level="executed-unverified",
            label=_trust_label("executed-unverified"),
            message="Canonical manifests record historical execution. This snapshot does not verify outputs or current freshness.",
        )
    return {
        "present": True,
        "source": "lightcone-0.5",
        "records": selected,
        "run_node_ids": sorted(selected),
        "trust": trust,
    }


def _generic_overlay(
    run_file: Path,
    data: dict[str, Any],
    *,
    project_root: Path,
    universe_id: str,
) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    declared_containers: list[str] = []
    raw_records = (
        _rocrate_record_list(data, run_file)
        if isinstance(data.get("@graph"), list)
        else _record_list(data)
    )
    for raw in raw_records:
        if raw.get("universe_id") not in {None, universe_id}:
            continue
        output_id = (
            raw.get("output_id")
            or raw.get("outputId")
            or raw.get("node_id")
            or raw.get("id")
        )
        if not output_id:
            continue
        output_id = str(output_id)
        if output_id in records:
            raise RunManifestError(f"run evidence repeats output {output_id}")
        container = (
            raw.get("container_image")
            or raw.get("worker_image")
            or raw.get("container")
        )
        if container:
            declared_containers.append(str(container))
        artifact_path = _artifact_candidate(raw, run_file)
        artifact_relative = None
        artifact_value = raw.get("artifact")
        artifact_record = artifact_value if isinstance(artifact_value, dict) else {}
        digest = raw.get("sha256") or raw.get("hash") or artifact_record.get("sha256")
        size = (
            raw.get("size") or raw.get("sizeBytes") or artifact_record.get("sizeBytes")
        )
        if artifact_path is not None:
            artifact_path = _confined_artifact(
                artifact_path, project_root, f"run output {output_id}"
            )
            actual_digest, actual_size = _sha256_size(artifact_path)
            if digest and str(digest).removeprefix("sha256:") != actual_digest:
                raise RunManifestError(f"run output {output_id} has a stale hash")
            if size is not None and int(size) != actual_size:
                raise RunManifestError(f"run output {output_id} has a stale size")
            digest, size = actual_digest, actual_size
            artifact_relative = str(artifact_path.relative_to(project_root))
        records[output_id] = {
            "output_id": output_id,
            "status": _normalize_status(
                raw.get("status"), artifact_present=artifact_path is not None
            ),
            "artifact_path": str(artifact_path) if artifact_path else None,
            "artifact_relative": artifact_relative,
            "preview_root": str(project_root),
            "sha256": str(digest).removeprefix("sha256:") if digest else None,
            "size": int(size) if size is not None else None,
            "media_type": raw.get("media_type") or raw.get("mediaType"),
            "units": raw.get("units") or raw.get("unit"),
            "started_at": raw.get("started_at") or raw.get("startedAt"),
            "ended_at": raw.get("finished_at")
            or raw.get("ended_at")
            or raw.get("endedAt"),
            "command": raw.get("recipe") or raw.get("command"),
            "container": str(container) if container else None,
            "log_excerpt": raw.get("log_excerpt") or raw.get("log"),
        }

    execution = data.get("execution") if isinstance(data.get("execution"), dict) else {}
    runtime = data.get("runtime") or execution.get("runtime")
    verification = data.get("verification")
    if isinstance(verification, dict):
        verification = verification.get("status")
    verified = (
        verification is True
        or (
            isinstance(verification, str)
            and verification.lower() in {"passed", "ok", "verified"}
        )
        or data.get("verified") is True
    )
    mismatch = str(runtime).lower() == "none" and bool(declared_containers)
    executed = any(
        record["status"] not in {"unknown", "not_run"} or record.get("artifact_path")
        for record in records.values()
    )
    if not executed:
        level = "spec-only"
        message = "No matching materialization evidence is present."
        verified = False
    elif mismatch:
        level = "provenance-mismatch"
        message = (
            "The manifest declares a container but records runtime none; the "
            "declared image did not run."
        )
    elif verified:
        level = "executed-verified"
        message = "The supplied run includes passing output verification."
    else:
        level = "executed-unverified"
        message = "Execution evidence is present, but passing verification is absent."
    return {
        "present": True,
        "source": "lightcone-run",
        "records": records,
        "run_node_ids": sorted(records),
        "trust": {
            "level": level,
            "label": _trust_label(level),
            "message": message,
            "dismissible": level != "provenance-mismatch",
            "output_integrity": "passed" if verified else "not-run",
            "declared_containers": sorted(set(declared_containers)),
            "runtime": runtime,
        },
    }


def _directory_run_file(directory: Path) -> Path:
    candidates = (
        directory / "run-manifest.json",
        directory / "manifest.json",
        directory / "status.json",
        directory / "ro-crate-metadata.json",
    )
    existing = [path for path in candidates if path.is_file()]
    if len(existing) == 1:
        return existing[0]
    if len(existing) > 1:
        raise RunManifestError(f"run directory is ambiguous: {directory}")
    raise RunManifestError(f"run directory contains no supported evidence: {directory}")


def load_run(
    run_path: str | Path,
    *,
    project_root: str | Path,
    universe_id: str,
) -> dict[str, Any]:
    """Load one supported run-evidence surface into a normalized overlay."""

    candidate = Path(run_path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise RunManifestError(
            f"run evidence is unavailable: {run_path}: {error}"
        ) from error
    if resolved.is_dir():
        resolved = _directory_run_file(resolved)
    if not resolved.is_file():
        raise RunManifestError(f"run evidence is not a file: {resolved}")
    data = _load_json(resolved)
    if not isinstance(data, dict):
        raise RunManifestError("run evidence must be a JSON object")
    root = Path(project_root).resolve(strict=True)
    if "counts" in data and "outputs" in data and "mode" in data:
        return _v05_overlay(_status_v05(data, root), universe_id)
    if isinstance(data.get("@graph"), list) and any(
        isinstance(item, dict) and "ControlAction" in _types(item)
        for item in data["@graph"]
    ):
        return _v05_overlay(_crate_v05(data, root), universe_id)
    if data.get("lc_version") == "0.5.0" and "data_version" in data:
        record = _canonical_record(resolved, root)
        return _v05_overlay([record] if record else [], universe_id)
    return _generic_overlay(resolved, data, project_root=root, universe_id=universe_id)


def spec_only_trust() -> dict[str, Any]:
    return {
        "level": "spec-only",
        "label": _trust_label("spec-only"),
        "message": "Shows declared intent. Nothing here was executed.",
        "dismissible": True,
        "output_integrity": "not-run",
        "declared_containers": [],
        "runtime": None,
    }
