#!/usr/bin/env python3
"""Generate and validate Neurodesktop pilot execution receipts."""

import argparse
import csv
import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import rfc8785
from jsonschema import Draft202012Validator


SCHEMA_FILENAME = "neurodesktop-pilot-execution-receipt-v1.0.0.schema.json"
LIGHTCONE_MANIFEST_FIELDS = {
    "schema_version",
    "output_id",
    "universe_id",
    "recipe",
    "decisions",
    "code_version",
    "container_image",
    "worker_image",
    "data_version",
    "input_versions",
    "git_sha",
    "git_remote",
    "lc_version",
    "finished_at",
    "host",
    "slurm_job_id",
}


class ReceiptValidationError(ValueError):
    """Raised when a receipt cannot be trusted or safely inspected."""


def _schema_path():
    installed = Path("/opt/neurodesktop/schemas") / SCHEMA_FILENAME
    if installed.is_file():
        return installed
    return Path(__file__).resolve().parents[1] / "schemas" / SCHEMA_FILENAME


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReceiptValidationError(f"cannot read JSON from {path}: {error}") from error


def _format_json_path(parts):
    return "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in parts
    )


def _validate_schema(receipt):
    schema = _load_json(_schema_path())
    validator = Draft202012Validator(
        schema,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    errors = sorted(
        validator.iter_errors(receipt),
        key=lambda error: _format_json_path(error.absolute_path),
    )
    if errors:
        error = errors[0]
        raise ReceiptValidationError(
            f"schema violation at {_format_json_path(error.absolute_path)}: "
            f"{error.message}"
        )


def _sha256_and_size(path):
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise ReceiptValidationError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest(), size


def _fsync_directory(path):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _resolve_regular_file(path, roots, label):
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReceiptValidationError(f"{label} is unavailable: {path}: {error}") from error

    if not any(resolved.is_relative_to(root) for root in roots):
        raise ReceiptValidationError(f"{label} escapes its allowed roots: {path}")
    if not resolved.is_file():
        raise ReceiptValidationError(f"{label} is not a regular file: {path}")
    return resolved


def _validate_artifact(artifact, workspace_root, label):
    path = _resolve_regular_file(
        workspace_root / artifact["relativePath"],
        [workspace_root],
        label,
    )
    sha256, size = _sha256_and_size(path)
    if artifact["sizeBytes"] != size:
        raise ReceiptValidationError(
            f"{label} size mismatch: expected {artifact['sizeBytes']}, found {size}"
        )
    if artifact["sha256"] != sha256:
        raise ReceiptValidationError(
            f"{label} SHA-256 mismatch: expected {artifact['sha256']}, found {sha256}"
        )
    return path


def _validate_resolved_file(resolved_file, module_roots, label):
    path = _resolve_regular_file(Path(resolved_file["path"]), module_roots, label)
    sha256, size = _sha256_and_size(path)
    if resolved_file["sizeBytes"] != size:
        raise ReceiptValidationError(
            f"{label} size mismatch: expected {resolved_file['sizeBytes']}, found {size}"
        )
    if resolved_file["sha256"] != sha256:
        raise ReceiptValidationError(
            f"{label} SHA-256 mismatch: expected {resolved_file['sha256']}, found {sha256}"
        )


def _validate_universe_hashes(receipt):
    for universe in receipt["analysis"]["universes"]:
        canonical_value = {
            "universeId": universe["universeId"],
            "decisions": universe["decisions"],
        }
        try:
            canonical = rfc8785.dumps(canonical_value)
        except rfc8785.CanonicalizationError as error:
            raise ReceiptValidationError(
                f"universe {universe['universeId']} cannot be canonicalized: {error}"
            ) from error
        sha256 = hashlib.sha256(canonical).hexdigest()
        if universe["canonicalSha256"] != sha256:
            raise ReceiptValidationError(
                f"universe {universe['universeId']} canonical SHA-256 mismatch: "
                f"expected {universe['canonicalSha256']}, found {sha256}"
            )


def _unique_key_set(records, key, label):
    values = [record[key] if isinstance(record, dict) else record for record in records]
    if len(values) != len(set(values)):
        identity = key if key is not None else "key"
        raise ReceiptValidationError(
            f"{label} contains duplicate {identity} values"
        )
    return set(values)


def _validate_success_coverage(receipt):
    if receipt["outcome"] != "succeeded":
        return

    verification = receipt["lightcone"]["verification"]
    key_sets = {
        "outputs": _unique_key_set(receipt["outputs"], "outputKey", "outputs"),
        "manifests": _unique_key_set(
            receipt["lightcone"]["manifests"],
            "outputKey",
            "Lightcone manifests",
        ),
        "expected outputs": _unique_key_set(
            verification["expectedOutputKeys"],
            None,
            "expected output keys",
        ),
        "verification results": _unique_key_set(
            verification["results"],
            "outputKey",
            "verification results",
        ),
    }
    expected = key_sets["outputs"]
    if any(keys != expected for keys in key_sets.values()):
        details = ", ".join(
            f"{label}={sorted(keys)}" for label, keys in key_sets.items()
        )
        raise ReceiptValidationError(
            f"successful receipt output coverage differs: {details}"
        )


def _validate_identities(receipt):
    universe_ids = _unique_key_set(
        receipt["analysis"]["universes"],
        "universeId",
        "universes",
    )
    _unique_key_set(receipt["inputs"], "inputId", "inputs")
    output_keys = _unique_key_set(receipt["outputs"], "outputKey", "outputs")
    _unique_key_set(
        receipt["lightcone"]["manifests"],
        "outputKey",
        "Lightcone manifests",
    )
    _unique_key_set(
        receipt["lightcone"]["verification"]["results"],
        "outputKey",
        "verification results",
    )

    for output in receipt["outputs"]:
        if output["universeId"] not in universe_ids:
            raise ReceiptValidationError(
                f"output {output['outputKey']} refers to an unknown universe"
            )
        expected_key = f"{output['universeId']}:{output['outputId']}"
        if output["outputKey"] != expected_key:
            raise ReceiptValidationError(
                f"output {output['outputKey']} identity should be {expected_key}"
            )

    manifest_keys = {
        manifest["outputKey"] for manifest in receipt["lightcone"]["manifests"]
    }
    result_keys = {
        result["outputKey"]
        for result in receipt["lightcone"]["verification"]["results"]
    }
    if not manifest_keys <= output_keys:
        raise ReceiptValidationError(
            "Lightcone manifests refer to outputs absent from the receipt"
        )
    if not result_keys <= manifest_keys:
        raise ReceiptValidationError(
            "verification results refer to absent Lightcone manifests"
        )


def _validate_module_execution(receipt):
    module = receipt["execution"]["module"]
    expected_specifier = f"{module['name']}/{module['version']}"
    if module["exactSpecifier"] != expected_specifier:
        raise ReceiptValidationError(
            "exact module specifier does not match name and version: "
            f"expected {expected_specifier!r}, found {module['exactSpecifier']!r}"
        )

    commands = module["commandSurface"]
    command_names = [command["name"] for command in commands]
    if len(command_names) != len(set(command_names)):
        raise ReceiptValidationError("module command surface contains duplicate names")
    wrapper_identity = (
        module["wrapper"]["path"],
        module["wrapper"]["sha256"],
        module["wrapper"]["sizeBytes"],
    )
    command_identities = {
        (
            command["resolvedFile"]["path"],
            command["resolvedFile"]["sha256"],
            command["resolvedFile"]["sizeBytes"],
        )
        for command in commands
    }
    if wrapper_identity not in command_identities:
        raise ReceiptValidationError(
            "module wrapper is absent from the recorded command surface"
        )


def _derived_trust_values(receipt):
    if receipt.get("outcome") == "succeeded":
        return "executed-unverified", "passed"
    level = (
        "executed-unverified"
        if receipt.get("lightcone", {}).get("manifests")
        else "spec-only"
    )
    verification_status = (
        receipt.get("lightcone", {}).get("verification", {}).get("status")
    )
    output_integrity = "failed" if verification_status == "failed" else "not-run"
    return level, output_integrity


def _validate_derived_trust(receipt):
    level, output_integrity = _derived_trust_values(receipt)
    if receipt["trust"]["level"] != level:
        raise ReceiptValidationError(
            f"derived trust level should be {level!r}, found "
            f"{receipt['trust']['level']!r}"
        )
    if receipt["trust"]["outputIntegrity"] != output_integrity:
        raise ReceiptValidationError(
            f"derived output integrity should be {output_integrity!r}, found "
            f"{receipt['trust']['outputIntegrity']!r}"
        )


def _validate_receipt_timeline(receipt):
    timeline = {
        "submittedAt": _parse_timestamp(receipt["slurm"]["submittedAt"], "submittedAt"),
        "startedAt": _parse_timestamp(
            receipt["slurm"]["terminal"]["startedAt"], "startedAt"
        ),
        "endedAt": _parse_timestamp(
            receipt["slurm"]["terminal"]["endedAt"], "endedAt"
        ),
        "createdAt": _parse_timestamp(receipt["createdAt"], "createdAt"),
        "finalizedAt": _parse_timestamp(
            receipt["generation"]["finalizedAt"], "finalizedAt"
        ),
    }
    ordered = [
        timeline["submittedAt"],
        timeline["startedAt"],
        timeline["endedAt"],
        timeline["createdAt"],
        timeline["finalizedAt"],
    ]
    if ordered != sorted(ordered):
        if timeline["finalizedAt"] < timeline["endedAt"]:
            raise ReceiptValidationError(
                "receipt was finalized before the Slurm job ended"
            )
        raise ReceiptValidationError(
            "receipt submission, execution, creation, and finalization times are out of order"
        )


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_lightcone_sha256(value):
    return isinstance(value, str) and value.startswith("sha256:") and _is_sha256(
        value.removeprefix("sha256:")
    )


def _validate_lightcone_manifests(receipt, workspace_root):
    outputs = {output["outputKey"]: output for output in receipt["outputs"]}
    universes = {
        universe["universeId"]: universe
        for universe in receipt["analysis"]["universes"]
    }
    verification_results = {
        result["outputKey"]: result
        for result in receipt["lightcone"]["verification"]["results"]
    }

    for recorded_manifest in receipt["lightcone"]["manifests"]:
        output_key = recorded_manifest["outputKey"]
        label = f"Lightcone manifest {output_key}"
        path = _resolve_regular_file(
            workspace_root / recorded_manifest["artifact"]["relativePath"],
            [workspace_root],
            label,
        )
        manifest = _load_json(path)
        if not isinstance(manifest, dict):
            raise ReceiptValidationError(f"{label} must contain a JSON object")

        missing = LIGHTCONE_MANIFEST_FIELDS - manifest.keys()
        unknown = manifest.keys() - LIGHTCONE_MANIFEST_FIELDS
        if missing or unknown:
            raise ReceiptValidationError(
                f"{label} fields differ from the v0.4.0 contract: "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        if manifest["schema_version"] != 1:
            raise ReceiptValidationError(
                f"{label} schema_version must be 1, found "
                f"{manifest['schema_version']!r}"
            )
        if output_key not in outputs:
            raise ReceiptValidationError(f"{label} has no matching receipt output")

        output = outputs[output_key]
        expected_output_key = f"{manifest['universe_id']}:{manifest['output_id']}"
        if output_key != expected_output_key:
            raise ReceiptValidationError(
                f"{label} identity does not match its outputKey"
            )
        if (
            manifest["output_id"] != output["outputId"]
            or manifest["universe_id"] != output["universeId"]
        ):
            raise ReceiptValidationError(
                f"{label} identity does not match the receipt output"
            )
        universe = universes.get(manifest["universe_id"])
        if universe is None or manifest["decisions"] != universe["decisions"]:
            raise ReceiptValidationError(
                f"{label} decisions do not match the selected universe"
            )

        if manifest["lc_version"] != receipt["lightcone"]["version"]:
            raise ReceiptValidationError(
                f"{label} Lightcone version does not match the receipt"
            )
        if str(manifest["slurm_job_id"]) != receipt["slurm"]["jobId"]:
            raise ReceiptValidationError(
                f"{label} Slurm job ID does not match the receipt"
            )
        if manifest["container_image"] is not None or manifest["worker_image"] is not None:
            raise ReceiptValidationError(
                f"{label} declares container evidence incompatible with the module pilot"
            )
        if not isinstance(manifest["recipe"], str) or not manifest["recipe"]:
            raise ReceiptValidationError(f"{label} recipe must be a non-empty string")
        if not _is_lightcone_sha256(
            manifest["code_version"]
        ) or not _is_lightcone_sha256(
            manifest["data_version"]
        ):
            raise ReceiptValidationError(
                f"{label} code_version and data_version must be SHA-256 values"
            )
        if not isinstance(manifest["input_versions"], dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in manifest["input_versions"].items()
        ):
            raise ReceiptValidationError(f"{label} input_versions must be a string map")
        if not isinstance(manifest["finished_at"], (int, float)) or isinstance(
            manifest["finished_at"], bool
        ):
            raise ReceiptValidationError(f"{label} finished_at must be numeric")
        try:
            manifest_finished_at = datetime.fromtimestamp(
                manifest["finished_at"], timezone.utc
            )
        except (OverflowError, OSError, ValueError) as error:
            raise ReceiptValidationError(
                f"{label} finished_at is outside the supported timestamp range"
            ) from error
        slurm_started_at = _parse_timestamp(
            receipt["slurm"]["terminal"]["startedAt"], "terminal startedAt"
        )
        slurm_ended_at = _parse_timestamp(
            receipt["slurm"]["terminal"]["endedAt"], "terminal endedAt"
        )
        if not slurm_started_at <= manifest_finished_at <= slurm_ended_at:
            raise ReceiptValidationError(
                f"{label} finished_at is outside the Slurm execution interval"
            )
        if not isinstance(manifest["host"], str) or not manifest["host"]:
            raise ReceiptValidationError(f"{label} host must be a non-empty string")

        verification = verification_results.get(output_key)
        if (
            verification is not None
            and verification["manifestSha256"]
            != recorded_manifest["artifact"]["sha256"]
        ):
            raise ReceiptValidationError(
                f"{label} hash does not match its verification result"
            )


def _validate_lightcone_status(receipt, workspace_root):
    status_artifact = receipt["lightcone"]["statusSnapshot"]
    if status_artifact is None:
        return
    path = _workspace_artifact_path(
        status_artifact, workspace_root, "Lightcone status snapshot"
    )
    status = _load_json(path)
    if not isinstance(status, dict) or set(status) != {"universes"} or not isinstance(
        status["universes"], list
    ):
        raise ReceiptValidationError(
            "Lightcone status snapshot must contain only a universes array"
        )

    manifest_recipes = {}
    for manifest_record in receipt["lightcone"]["manifests"]:
        manifest_path = _workspace_artifact_path(
            manifest_record["artifact"],
            workspace_root,
            f"Lightcone manifest {manifest_record['outputKey']}",
        )
        manifest_recipes[manifest_record["outputKey"]] = _load_json(manifest_path).get(
            "recipe"
        )

    status_by_output = {}
    for universe in status["universes"]:
        if not isinstance(universe, dict) or set(universe) != {
            "universe_id",
            "outputs",
        }:
            raise ReceiptValidationError("Lightcone status universe has invalid fields")
        if not isinstance(universe["universe_id"], str) or not isinstance(
            universe["outputs"], list
        ):
            raise ReceiptValidationError("Lightcone status universe has invalid values")
        for output in universe["outputs"]:
            if not isinstance(output, dict) or set(output) != {
                "output_id",
                "analysis_id",
                "status",
                "recipe_command",
            }:
                raise ReceiptValidationError("Lightcone status output has invalid fields")
            output_key = f"{universe['universe_id']}:{output['output_id']}"
            if output_key in status_by_output:
                raise ReceiptValidationError(
                    f"Lightcone status duplicates output {output_key}"
                )
            if output["analysis_id"] is not None:
                raise ReceiptValidationError(
                    "the provisional module pilot does not support nested analysis IDs"
                )
            if output["status"] not in {"ok", "stale", "missing", "alias"}:
                raise ReceiptValidationError(
                    f"Lightcone status for {output_key} is unknown"
                )
            recipe = manifest_recipes.get(output_key)
            if recipe is not None and output["recipe_command"] != recipe:
                raise ReceiptValidationError(
                    f"Lightcone status recipe for {output_key} differs from its manifest"
                )
            status_by_output[output_key] = output["status"]

    if receipt["outcome"] == "succeeded":
        output_keys = {output["outputKey"] for output in receipt["outputs"]}
        if set(status_by_output) != output_keys:
            raise ReceiptValidationError(
                "Lightcone status coverage differs from receipt outputs"
            )
        non_ok = sorted(
            output_key
            for output_key, output_status in status_by_output.items()
            if output_status != "ok"
        )
        if non_ok:
            raise ReceiptValidationError(
                f"successful receipt has non-ok Lightcone status: {non_ok}"
            )


def _validate_verification_evidence(receipt, workspace_root):
    verification = receipt["lightcone"]["verification"]
    evidence_artifact = verification["evidence"]
    if verification["status"] == "not-run":
        if (
            verification["performedAt"] is not None
            or verification["results"]
            or evidence_artifact is not None
            or verification["coverageComplete"]
        ):
            raise ReceiptValidationError(
                "not-run verification must not claim results, evidence, or coverage"
            )
        return

    if verification["performedAt"] is None or evidence_artifact is None:
        raise ReceiptValidationError(
            "performed verification requires a timestamp and evidence artifact"
        )
    performed_at = _parse_timestamp(
        verification["performedAt"], "verification performedAt"
    )
    started_at = _parse_timestamp(
        receipt["slurm"]["terminal"]["startedAt"], "terminal startedAt"
    )
    ended_at = _parse_timestamp(
        receipt["slurm"]["terminal"]["endedAt"], "terminal endedAt"
    )
    if not started_at <= performed_at <= ended_at:
        raise ReceiptValidationError(
            "verification performedAt is outside the Slurm execution interval"
        )

    for result in verification["results"]:
        if result["status"] == "passed" and result["failure"] is not None:
            raise ReceiptValidationError(
                f"passing verification result {result['outputKey']} has a failure"
            )
        if result["status"] == "failed" and not result["failure"]:
            raise ReceiptValidationError(
                f"failed verification result {result['outputKey']} lacks a failure"
            )

    path = _workspace_artifact_path(
        evidence_artifact, workspace_root, "Lightcone verification evidence"
    )
    evidence = _load_json(path)
    expected = {key: value for key, value in verification.items() if key != "evidence"}
    if evidence != expected:
        raise ReceiptValidationError(
            "Lightcone verification evidence differs from the receipt"
        )


def _workspace_artifact_path(artifact, workspace_root, label):
    return _resolve_regular_file(
        workspace_root / artifact["relativePath"],
        [workspace_root],
        label,
    )


def _read_text(path, label):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReceiptValidationError(f"cannot read {label}: {error}") from error


def _parse_timestamp(value, label):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ReceiptValidationError(f"{label} is not an ISO timestamp") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_slurm_memory_mib(value):
    match = re.fullmatch(r"([0-9]+)([KMGTP]?)", value or "")
    if match is None:
        raise ReceiptValidationError(f"unsupported Slurm memory value: {value!r}")
    amount = int(match.group(1))
    unit = match.group(2)
    multipliers = {"": 1, "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024**2, "P": 1024**3}
    return int(amount * multipliers[unit])


def _parse_slurm_duration_seconds(value):
    try:
        if "-" in value:
            day_text, value = value.split("-", 1)
            days = int(day_text)
        else:
            days = 0
        parts = [int(part) for part in value.split(":")]
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours = 0
            minutes, seconds = parts
        else:
            raise ValueError
    except (AttributeError, ValueError) as error:
        raise ReceiptValidationError(
            f"unsupported Slurm duration value: {value!r}"
        ) from error
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _validate_slurm_evidence(receipt, workspace_root):
    slurm = receipt["slurm"]
    terminal = slurm["terminal"]
    evidence = slurm["evidence"]
    submission = slurm["submissionArgv"]
    retained_script = receipt["execution"]["retainedScript"]["relativePath"]
    if (
        submission[0] != "sbatch"
        or "--parsable" not in submission[1:-1]
        or submission[-1] != retained_script
    ):
        raise ReceiptValidationError(
            "Slurm submission does not invoke the retained script with "
            "sbatch --parsable"
        )

    scontrol_path = _workspace_artifact_path(
        evidence["scontrol"], workspace_root, "scontrol evidence"
    )
    scontrol = {}
    for field in _read_text(scontrol_path, "scontrol evidence").split():
        if "=" in field:
            key, value = field.split("=", 1)
            scontrol[key] = value
    required_scontrol = {
        "JobId": slurm["jobId"],
        "JobState": terminal["state"],
        "ExitCode": terminal["exitCode"],
        "NodeList": terminal["node"],
        "Partition": slurm["partition"],
        "NumNodes": str(slurm["resources"]["nodes"]),
        "NumTasks": str(slurm["resources"]["tasks"]),
        "CPUs/Task": str(slurm["resources"]["cpusPerTask"]),
    }
    for field, expected in required_scontrol.items():
        if scontrol.get(field) != expected:
            raise ReceiptValidationError(
                f"scontrol {field} does not match the receipt: "
                f"expected {expected!r}, found {scontrol.get(field)!r}"
            )
    memory_mib = _parse_slurm_memory_mib(scontrol.get("MinMemoryNode"))
    if memory_mib != slurm["resources"]["memoryMiB"]:
        raise ReceiptValidationError(
            "scontrol MinMemoryNode does not match the receipt: "
            f"expected {slurm['resources']['memoryMiB']} MiB, found {memory_mib} MiB"
        )
    time_limit_seconds = _parse_slurm_duration_seconds(scontrol.get("TimeLimit"))
    if time_limit_seconds != slurm["resources"]["wallTimeSeconds"]:
        raise ReceiptValidationError(
            "scontrol TimeLimit does not match the receipt: "
            f"expected {slurm['resources']['wallTimeSeconds']} seconds, "
            f"found {time_limit_seconds} seconds"
        )

    sacct_path = _workspace_artifact_path(
        evidence["sacct"], workspace_root, "sacct evidence"
    )
    rows = list(
        csv.DictReader(
            _read_text(sacct_path, "sacct evidence").splitlines(),
            delimiter="|",
        )
    )
    row = next((candidate for candidate in rows if candidate.get("JobID") == slurm["jobId"]), None)
    if row is None:
        raise ReceiptValidationError("sacct evidence has no row for the receipt job ID")
    allocation_state = (row.get("State") or "").split()[0].rstrip("+")
    if allocation_state != terminal["state"]:
        raise ReceiptValidationError(
            "sacct State does not match the receipt: "
            f"expected {terminal['state']!r}, found {row.get('State')!r}"
        )
    allocation_exit_code = row.get("ExitCode")
    allocation_exit_is_normalized = (
        terminal["state"] in {"CANCELLED", "TIMEOUT"}
        and allocation_exit_code == "0:0"
    )
    if (
        allocation_exit_code != terminal["exitCode"]
        and not allocation_exit_is_normalized
    ):
        raise ReceiptValidationError(
            "sacct ExitCode does not match the receipt: "
            f"expected {terminal['exitCode']!r}, found {allocation_exit_code!r}"
        )
    required_sacct = {
        "ElapsedRaw": str(terminal["elapsedSeconds"]),
        "NodeList": terminal["node"],
    }
    for field, expected in required_sacct.items():
        if row.get(field) != expected:
            raise ReceiptValidationError(
                f"sacct {field} does not match the receipt: "
                f"expected {expected!r}, found {row.get(field)!r}"
            )

    started_at = _parse_timestamp(terminal["startedAt"], "terminal startedAt")
    ended_at = _parse_timestamp(terminal["endedAt"], "terminal endedAt")
    if started_at > ended_at:
        raise ReceiptValidationError("terminal startedAt is after endedAt")
    if int((ended_at - started_at).total_seconds()) != terminal["elapsedSeconds"]:
        raise ReceiptValidationError(
            "terminal elapsedSeconds does not match startedAt and endedAt"
        )

    for field, receipt_value in (
        ("StartTime", started_at),
        ("EndTime", ended_at),
    ):
        if field not in scontrol:
            raise ReceiptValidationError(f"scontrol evidence is missing {field}")
        if _parse_timestamp(scontrol[field], f"scontrol {field}") != receipt_value:
            raise ReceiptValidationError(f"scontrol {field} does not match the receipt")
    for field, receipt_value in (
        ("Start", started_at),
        ("End", ended_at),
    ):
        if row.get(field) is None:
            raise ReceiptValidationError(f"sacct evidence is missing {field}")
        if _parse_timestamp(row[field], f"sacct {field}") != receipt_value:
            raise ReceiptValidationError(f"sacct {field} does not match the receipt")

    for stream_name in ("stdout", "stderr"):
        if slurm["jobId"] not in Path(evidence[stream_name]["relativePath"]).name:
            raise ReceiptValidationError(
                f"Slurm {stream_name} path does not identify job {slurm['jobId']}"
            )


def _validate_inventory_relative_path(value):
    if not isinstance(value, str) or not value:
        raise ReceiptValidationError("RO-Crate inventory path must be a string")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ReceiptValidationError(
            f"RO-Crate inventory path is not confined: {value!r}"
        )


def _inventory_entry(path, crate_root):
    relative_path = path.relative_to(crate_root).as_posix()
    sha256, size = _sha256_and_size(path)
    return {
        "relativePath": relative_path,
        "sizeBytes": size,
        "sha256": sha256,
    }


def _walk_crate_files(crate_root):
    files = []
    for directory, directory_names, file_names in os.walk(
        crate_root, followlinks=False
    ):
        directory_path = Path(directory)
        for name in directory_names:
            path = directory_path / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ReceiptValidationError(
                    f"RO-Crate contains a symbolic-link directory: {path}"
                )
            if not stat.S_ISDIR(mode):
                raise ReceiptValidationError(
                    f"RO-Crate contains a special directory entry: {path}"
                )
        for name in file_names:
            path = directory_path / name
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise ReceiptValidationError(
                    f"RO-Crate contains a non-regular file: {path}"
                )
            files.append(path)
    files.sort(key=lambda path: path.relative_to(crate_root).as_posix().encode("utf-8"))
    return files


def _write_inventory(crate, workspace_root):
    raw_root = workspace_root / crate["rootRelativePath"]
    try:
        crate_root = raw_root.resolve(strict=True)
    except OSError as error:
        raise ReceiptValidationError(f"RO-Crate root is unavailable: {error}") from error
    if not crate_root.is_relative_to(workspace_root) or not crate_root.is_dir():
        raise ReceiptValidationError("RO-Crate root is not a confined directory")

    raw_inventory_path = workspace_root / crate["inventory"]["relativePath"]
    try:
        inventory_parent = raw_inventory_path.parent.resolve(strict=True)
    except OSError as error:
        raise ReceiptValidationError(
            f"RO-Crate inventory directory is unavailable: {error}"
        ) from error
    if not inventory_parent.is_relative_to(workspace_root):
        raise ReceiptValidationError("RO-Crate inventory escapes the workspace")
    inventory_path = inventory_parent / raw_inventory_path.name
    if inventory_path.is_relative_to(crate_root):
        raise ReceiptValidationError(
            "RO-Crate inventory must be outside the tree that it hashes"
        )

    inventory = [
        _inventory_entry(path, crate_root) for path in _walk_crate_files(crate_root)
    ]
    content = json.dumps(
        inventory,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=inventory_parent,
        prefix=f".{inventory_path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, inventory_path)
        _fsync_directory(inventory_parent)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    crate["inventory"].update(
        {
            "sha256": hashlib.sha256(content).hexdigest(),
            "sizeBytes": len(content),
        }
    )
    crate["treeSha256"] = hashlib.sha256(rfc8785.dumps(inventory)).hexdigest()


def _validate_ro_crate(receipt, workspace_root):
    crate = receipt["roCrate"]
    if crate is None:
        return

    raw_root = workspace_root / crate["rootRelativePath"]
    try:
        crate_root = raw_root.resolve(strict=True)
    except OSError as error:
        raise ReceiptValidationError(f"RO-Crate root is unavailable: {error}") from error
    if not crate_root.is_relative_to(workspace_root) or not crate_root.is_dir():
        raise ReceiptValidationError("RO-Crate root is not a confined directory")

    inventory_path = _workspace_artifact_path(
        crate["inventory"], workspace_root, "RO-Crate inventory"
    )
    if inventory_path.is_relative_to(crate_root):
        raise ReceiptValidationError(
            "RO-Crate inventory must be outside the tree that it hashes"
        )
    inventory = _load_json(inventory_path)
    if not isinstance(inventory, list):
        raise ReceiptValidationError("RO-Crate inventory must contain a JSON array")

    for entry in inventory:
        if not isinstance(entry, dict) or set(entry) != {
            "relativePath",
            "sizeBytes",
            "sha256",
        }:
            raise ReceiptValidationError("RO-Crate inventory entry has invalid fields")
        _validate_inventory_relative_path(entry["relativePath"])
        if (
            not isinstance(entry["sizeBytes"], int)
            or isinstance(entry["sizeBytes"], bool)
            or entry["sizeBytes"] < 0
            or not _is_sha256(entry["sha256"])
        ):
            raise ReceiptValidationError("RO-Crate inventory entry has invalid values")

    expected_order = sorted(
        inventory,
        key=lambda entry: entry["relativePath"].encode("utf-8"),
    )
    paths = [entry["relativePath"] for entry in inventory]
    if inventory != expected_order or len(paths) != len(set(paths)):
        raise ReceiptValidationError(
            "RO-Crate inventory must be uniquely sorted by UTF-8 path bytes"
        )

    actual_inventory = [
        _inventory_entry(path, crate_root) for path in _walk_crate_files(crate_root)
    ]
    if inventory != actual_inventory:
        raise ReceiptValidationError(
            "RO-Crate inventory does not match the exported files"
        )

    try:
        tree_sha256 = hashlib.sha256(rfc8785.dumps(inventory)).hexdigest()
    except rfc8785.CanonicalizationError as error:
        raise ReceiptValidationError(
            f"RO-Crate inventory cannot be canonicalized: {error}"
        ) from error
    if crate["treeSha256"] != tree_sha256:
        raise ReceiptValidationError(
            "RO-Crate tree SHA-256 does not match its canonical inventory"
        )

    metadata_path = _workspace_artifact_path(
        crate["metadata"], workspace_root, "RO-Crate metadata"
    )
    if not metadata_path.is_relative_to(crate_root):
        raise ReceiptValidationError("RO-Crate metadata is outside the crate root")

    required_artifacts = [receipt["analysis"]["spec"]]
    required_artifacts.extend(
        universe["definition"] for universe in receipt["analysis"]["universes"]
    )
    required_artifacts.extend(
        manifest["artifact"] for manifest in receipt["lightcone"]["manifests"]
    )
    required_artifacts.extend(output["artifact"] for output in receipt["outputs"])
    inventory_hashes = Counter(entry["sha256"] for entry in inventory)
    required_hashes = Counter(artifact["sha256"] for artifact in required_artifacts)
    if any(inventory_hashes[sha256] < count for sha256, count in required_hashes.items()):
        raise ReceiptValidationError(
            "RO-Crate does not contain every declared specification, universe, "
            "manifest, and output artifact"
        )


def _workspace_artifacts(receipt):
    yield "$.analysis.spec", receipt["analysis"]["spec"]
    for index, universe in enumerate(receipt["analysis"]["universes"]):
        yield f"$.analysis.universes[{index}].definition", universe["definition"]
    yield "$.execution.retainedScript", receipt["execution"]["retainedScript"]
    for index, input_record in enumerate(receipt["inputs"]):
        yield f"$.inputs[{index}].artifact", input_record["artifact"]
    for name, artifact in receipt["slurm"]["evidence"].items():
        yield f"$.slurm.evidence.{name}", artifact

    status_snapshot = receipt["lightcone"]["statusSnapshot"]
    if status_snapshot is not None:
        yield "$.lightcone.statusSnapshot", status_snapshot
    for index, manifest in enumerate(receipt["lightcone"]["manifests"]):
        yield f"$.lightcone.manifests[{index}].artifact", manifest["artifact"]
    verification_evidence = receipt["lightcone"]["verification"]["evidence"]
    if verification_evidence is not None:
        yield "$.lightcone.verification.evidence", verification_evidence
    for index, output in enumerate(receipt["outputs"]):
        yield f"$.outputs[{index}].artifact", output["artifact"]

    crate = receipt["roCrate"]
    if crate is not None:
        yield "$.roCrate.metadata", crate["metadata"]
        yield "$.roCrate.inventory", crate["inventory"]


def _resolved_module_files(receipt):
    module = receipt["execution"]["module"]
    yield "$.execution.module.modulefile", module["modulefile"]
    yield "$.execution.module.wrapper", module["wrapper"]
    for index, command in enumerate(module["commandSurface"]):
        yield (
            f"$.execution.module.commandSurface[{index}].resolvedFile",
            command["resolvedFile"],
        )


def _validate_referenced_files(receipt, workspace_root, module_roots):
    seen_workspace = {}
    for label, artifact in _workspace_artifacts(receipt):
        resolved = _validate_artifact(artifact, workspace_root, label)
        if resolved in seen_workspace:
            raise ReceiptValidationError(
                f"{label} duplicates workspace artifact target from "
                f"{seen_workspace[resolved]}: {resolved}"
            )
        seen_workspace[resolved] = label
    for label, resolved_file in _resolved_module_files(receipt):
        _validate_resolved_file(resolved_file, module_roots, label)


def _populate_referenced_files(receipt, workspace_root, module_roots):
    try:
        for label, artifact in _workspace_artifacts(receipt):
            artifact_path = _resolve_regular_file(
                workspace_root / artifact["relativePath"],
                [workspace_root],
                label,
            )
            sha256, size = _sha256_and_size(artifact_path)
            artifact.update({"sha256": sha256, "sizeBytes": size})
        for label, resolved_file in _resolved_module_files(receipt):
            resolved_path = _resolve_regular_file(
                Path(resolved_file["path"]),
                module_roots,
                label,
            )
            sha256, size = _sha256_and_size(resolved_path)
            resolved_file.update({"sha256": sha256, "sizeBytes": size})
    except (KeyError, TypeError) as error:
        raise ReceiptValidationError(
            f"receipt candidate lacks required evidence structure: {error}"
        ) from error


def _resolved_directory(path, allowed_root, label):
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReceiptValidationError(f"{label} is unavailable: {error}") from error
    if not resolved.is_dir() or not resolved.is_relative_to(allowed_root):
        raise ReceiptValidationError(f"{label} is not a confined directory")
    return resolved


def _resolve_generation_roots(workspace_root, allowed_module_roots):
    try:
        resolved_workspace = workspace_root.resolve(strict=True)
    except OSError as error:
        raise ReceiptValidationError(f"workspace root is unavailable: {error}") from error
    if not resolved_workspace.is_dir():
        raise ReceiptValidationError("workspace root is not a directory")

    resolved_module_roots = []
    for root in allowed_module_roots:
        try:
            resolved = root.resolve(strict=True)
        except OSError as error:
            raise ReceiptValidationError(
                f"allowed module root is unavailable: {root}: {error}"
            ) from error
        if not resolved.is_dir():
            raise ReceiptValidationError(f"allowed module root is not a directory: {root}")
        resolved_module_roots.append(resolved)
    return resolved_workspace, resolved_module_roots


def _derive_receipt(candidate, workspace_root, module_roots):
    if not isinstance(candidate, dict):
        raise ReceiptValidationError("receipt candidate must be a JSON object")
    receipt = json.loads(json.dumps(candidate))
    if receipt.get("workspaceRoot") != str(workspace_root):
        raise ReceiptValidationError(
            "candidate workspaceRoot does not match the configured workspace root"
        )

    receipt.pop("trust", None)
    receipt.pop("generation", None)
    _populate_referenced_files(receipt, workspace_root, module_roots)

    for universe in receipt.get("analysis", {}).get("universes", []):
        try:
            canonical = rfc8785.dumps(
                {
                    "universeId": universe["universeId"],
                    "decisions": universe["decisions"],
                }
            )
        except (KeyError, rfc8785.CanonicalizationError) as error:
            raise ReceiptValidationError(
                f"cannot derive universe identity: {error}"
            ) from error
        universe["canonicalSha256"] = hashlib.sha256(canonical).hexdigest()

    manifests = {
        manifest["outputKey"]: manifest["artifact"]["sha256"]
        for manifest in receipt.get("lightcone", {}).get("manifests", [])
    }
    for result in (
        receipt.get("lightcone", {}).get("verification", {}).get("results", [])
    ):
        try:
            result["manifestSha256"] = manifests[result["outputKey"]]
        except KeyError as error:
            raise ReceiptValidationError(
                f"verification result has no matching manifest: {error}"
            ) from error

    crate = receipt.get("roCrate")
    if crate is not None:
        _write_inventory(crate, workspace_root)

    trust_level, output_integrity = _derived_trust_values(receipt)
    receipt["trust"] = {
        "level": trust_level,
        "outputIntegrity": output_integrity,
        "actualContainerIdentity": None,
        "actualContainerIdentityAttested": False,
        "statement": "The actual tool-container identity is not attested by this receipt.",
    }
    receipt["generation"] = {
        "method": "atomic-write-fsync-rename",
        "finalizedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "complete": True,
    }
    return receipt


def _rename_noreplace(source, destination):
    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform.startswith("linux"):
        try:
            rename = library.renameat2
        except AttributeError as error:
            raise ReceiptValidationError(
                "this platform lacks atomic no-replace rename support"
            ) from error
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, source_bytes, -100, destination_bytes, 1)
    elif sys.platform == "darwin":
        rename = library.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(source_bytes, destination_bytes, 0x00000004)
    else:
        raise ReceiptValidationError(
            "this platform lacks atomic no-replace rename support"
        )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number == errno.EEXIST:
            raise ReceiptValidationError(f"final receipt already exists: {destination}")
        raise ReceiptValidationError(
            f"cannot atomically publish receipt: {os.strerror(error_number)}"
        )


def generate_receipt(
    candidate_path,
    receipt_directory,
    workspace_root,
    allowed_module_roots,
):
    configured_workspace, module_roots = _resolve_generation_roots(
        workspace_root, allowed_module_roots
    )
    destination_directory = _resolved_directory(
        receipt_directory,
        configured_workspace,
        "receipt directory",
    )
    final_path = destination_directory / "receipt.json"
    receipt = _derive_receipt(
        _load_json(candidate_path),
        configured_workspace,
        module_roots,
    )

    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination_directory,
        prefix=".receipt.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        validate_receipt(
            temporary_path,
            configured_workspace,
            module_roots,
        )
        temporary_path.chmod(0o444)
        _rename_noreplace(temporary_path, final_path)
        _fsync_directory(destination_directory)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return final_path


def validate_receipt(receipt_path, workspace_root, allowed_module_roots):
    receipt = _load_json(receipt_path)
    _validate_schema(receipt)
    _validate_universe_hashes(receipt)
    _validate_identities(receipt)
    _validate_success_coverage(receipt)
    _validate_module_execution(receipt)
    _validate_derived_trust(receipt)
    _validate_receipt_timeline(receipt)

    try:
        configured_workspace = workspace_root.resolve(strict=True)
    except OSError as error:
        raise ReceiptValidationError(
            f"workspace root is unavailable: {workspace_root}: {error}"
        ) from error
    if not configured_workspace.is_dir():
        raise ReceiptValidationError(
            f"workspace root is not a directory: {configured_workspace}"
        )

    try:
        recorded_workspace = Path(receipt["workspaceRoot"]).resolve(strict=True)
    except OSError as error:
        raise ReceiptValidationError(
            f"recorded workspace root is unavailable: {error}"
        ) from error
    if recorded_workspace != configured_workspace:
        raise ReceiptValidationError(
            "recorded workspace root does not match the configured workspace root"
        )

    module_roots = []
    for root in allowed_module_roots:
        try:
            resolved = root.resolve(strict=True)
        except OSError as error:
            raise ReceiptValidationError(
                f"allowed module root is unavailable: {root}: {error}"
            ) from error
        if not resolved.is_dir():
            raise ReceiptValidationError(f"allowed module root is not a directory: {root}")
        module_roots.append(resolved)

    _validate_referenced_files(receipt, configured_workspace, module_roots)
    _validate_lightcone_manifests(receipt, configured_workspace)
    _validate_lightcone_status(receipt, configured_workspace)
    _validate_verification_evidence(receipt, configured_workspace)
    _validate_slurm_evidence(receipt, configured_workspace)
    _validate_ro_crate(receipt, configured_workspace)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="neurodesktop-pilot-receipt",
        description="Generate and validate Neurodesktop pilot execution receipts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="validate a finalized pilot execution receipt",
    )
    validate.add_argument("receipt", type=Path)
    validate.add_argument("--workspace-root", type=Path, required=True)
    validate.add_argument(
        "--allowed-module-root",
        type=Path,
        action="append",
        required=True,
        dest="allowed_module_roots",
    )

    generate = subparsers.add_parser(
        "generate",
        help="derive, validate, and atomically publish a pilot execution receipt",
    )
    generate.add_argument("candidate", type=Path)
    generate.add_argument("--receipt-directory", type=Path, required=True)
    generate.add_argument("--workspace-root", type=Path, required=True)
    generate.add_argument(
        "--allowed-module-root",
        type=Path,
        action="append",
        required=True,
        dest="allowed_module_roots",
    )
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            validate_receipt(
                args.receipt,
                args.workspace_root,
                args.allowed_module_roots,
            )
            print(f"valid: {args.receipt}")
            return 0
        if args.command == "generate":
            final_path = generate_receipt(
                args.candidate,
                args.receipt_directory,
                args.workspace_root,
                args.allowed_module_roots,
            )
            print(f"generated: {final_path}")
            return 0
    except ReceiptValidationError as error:
        print(f"invalid: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"invalid: filesystem operation failed: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
