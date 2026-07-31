#!/usr/bin/env python3
"""Prepare and execute the bounded Neurodesktop ASTRA/Lightcone BET pilot."""

import argparse
import csv
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PILOT_DIRECTORY = "astra-lightcone-bet"
SLURM_TERMINAL_STATES = frozenset(
    {
        "BOOT_FAIL",
        "CANCELLED",
        "COMPLETED",
        "DEADLINE",
        "FAILED",
        "NODE_FAIL",
        "OUT_OF_MEMORY",
        "PREEMPTED",
        "REVOKED",
        "TIMEOUT",
    }
)


class PilotError(RuntimeError):
    """Raised when the bounded pilot cannot proceed safely."""


def _asset_root():
    installed = Path("/opt/neurodesktop/pilots") / PILOT_DIRECTORY
    if installed.is_dir():
        return installed
    return Path(__file__).resolve().parents[1] / "pilots" / PILOT_DIRECTORY


def _load_contract(asset_root):
    try:
        return json.loads((asset_root / "pilot-contract.json").read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PilotError(f"cannot read the installed pilot contract: {error}") from error


def _publish_exact_file(source, destination, mode=0o644):
    try:
        content = source.read_bytes()
    except OSError as error:
        raise PilotError(f"cannot read pilot asset {source}: {error}") from error

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or destination.read_bytes() != content:
            raise PilotError(f"prepared pilot file already differs: {destination}")
        destination.chmod(mode)
        return

    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(mode)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _prepare_project(workspace_root):
    asset_root = _asset_root()
    contract = _load_contract(asset_root)
    _publish_exact_file(
        asset_root / "pilot-contract.json",
        workspace_root / "pilot-contract.json",
    )

    project_root = asset_root / "project"
    for source in sorted(project_root.rglob("*")):
        if not source.is_file():
            continue
        relative_path = source.relative_to(project_root)
        mode = 0o755 if source.suffix in {".sh", ".sbatch"} else 0o644
        _publish_exact_file(source, workspace_root / "project" / relative_path, mode)

    source_manifest = {
        "snapshot": contract["sourceSnapshot"],
        "files": [
            {
                key: source[key]
                for key in ("relativePath", "sha256", "sizeBytes", "url")
            }
            for source in contract["inputs"]
        ],
    }
    manifest_path = workspace_root / "inputs" / "source-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(source_manifest, indent=2, sort_keys=True).encode() + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=manifest_path.parent,
        prefix=".source-manifest.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, manifest_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _sha256_and_size(path):
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise PilotError(f"cannot hash input {path}: {error}") from error
    return digest.hexdigest(), size


def _input_destination(workspace_root, source):
    relative_path = Path(source["relativePath"])
    if relative_path.is_absolute() or any(
        part in {"", ".", ".."} for part in relative_path.parts
    ):
        raise PilotError(f"input path is not confined: {source['relativePath']!r}")
    destination = workspace_root / "inputs" / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = destination.parent.resolve(strict=True)
    if not resolved_parent.is_relative_to(workspace_root):
        raise PilotError(f"input path escapes the workspace: {source['relativePath']!r}")
    return resolved_parent / destination.name


def _validate_staged_input(destination, source):
    try:
        mode = destination.lstat().st_mode
    except OSError as error:
        raise PilotError(f"cannot inspect input {source['id']}: {error}") from error
    if not stat.S_ISREG(mode):
        raise PilotError(f"input {source['id']} is not a regular file")
    sha256, size = _sha256_and_size(destination)
    if sha256 != source["sha256"]:
        raise PilotError(
            f"input {source['id']} SHA-256 mismatch: expected "
            f"{source['sha256']}, found {sha256}"
        )
    if size != source["sizeBytes"]:
        raise PilotError(
            f"input {source['id']} size mismatch: expected "
            f"{source['sizeBytes']}, found {size}"
        )


def _download_input(destination, source):
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    request = urllib.request.Request(
        source["url"],
        headers={"User-Agent": "neurodesktop-astra-lightcone-pilot/1.0"},
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            try:
                response = urllib.request.urlopen(request, timeout=120)
            except (OSError, urllib.error.URLError) as error:
                raise PilotError(
                    f"cannot download input {source['id']}: {error}"
                ) from error
            with response:
                final_url = response.geturl()
                if not final_url.startswith("https://"):
                    raise PilotError(
                        f"input {source['id']} redirected outside HTTPS: {final_url}"
                    )
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        _validate_staged_input(temporary_path, source)
        temporary_path.chmod(0o444)
        try:
            os.link(temporary_path, destination, follow_symlinks=False)
        except FileExistsError as error:
            raise PilotError(f"input appeared during staging: {destination}") from error
        temporary_path.unlink()
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _stage_inputs(workspace_root, contract):
    destinations = [
        (_input_destination(workspace_root, source), source)
        for source in contract["inputs"]
    ]
    for destination, source in destinations:
        if destination.exists() or destination.is_symlink():
            _validate_staged_input(destination, source)
    for destination, source in destinations:
        if not destination.exists():
            _download_input(destination, source)


def prepare(workspace_root, project_only):
    if workspace_root.exists() and not workspace_root.is_dir():
        raise PilotError(f"workspace root is not a directory: {workspace_root}")
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace_root = workspace_root.resolve(strict=True)
    _prepare_project(workspace_root)
    if not project_only:
        _stage_inputs(workspace_root, _load_contract(_asset_root()))
    return workspace_root


def _run_external(argv, *, cwd=None, env=None, timeout=15):
    try:
        return subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PilotError(f"cannot run {argv[0]}: {error}") from error


def _wait_for_local_slurm(contract, timeout_seconds):
    mode = os.environ.get("NEURODESKTOP_SLURM_MODE", "local").lower()
    if mode != "local":
        raise PilotError(
            "the pilot requires NEURODESKTOP_SLURM_MODE=local inside Neurodesktop"
        )
    if timeout_seconds < 0:
        raise PilotError("scheduler timeout cannot be negative")

    deadline = time.monotonic() + timeout_seconds
    last_output = ""
    while True:
        ping = _run_external(["scontrol", "ping"])
        last_output = ping.stdout.strip()
        if ping.returncode == 0:
            partition = contract["slurm"]["partition"]
            info = _run_external(
                [
                    "sinfo",
                    "--noheader",
                    "--partition",
                    partition,
                    "--format",
                    "%a|%T",
                ]
            )
            last_output = info.stdout.strip()
            healthy = any(
                line.split("|", 1)[0].strip().lower() == "up"
                and not any(
                    bad_state in line.lower()
                    for bad_state in ("down", "drain", "fail", "unknown")
                )
                for line in info.stdout.splitlines()
                if "|" in line
            )
            if info.returncode == 0 and healthy:
                return
        if time.monotonic() >= deadline:
            detail = f": {last_output}" if last_output else ""
            raise PilotError(
                "local Slurm scheduler was not ready within "
                f"{timeout_seconds} seconds{detail}"
            )
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def _utc_now(timespec="microseconds"):
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec=timespec)
        .replace("+00:00", "Z")
    )


def _run_id():
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{PILOT_DIRECTORY}-{timestamp}-{secrets.token_hex(4)}"


def _write_text_atomic(destination, content):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _artifact(relative_path):
    return {
        "relativePath": Path(relative_path).as_posix(),
        "sha256": "0" * 64,
        "sizeBytes": 0,
    }


def _resolved_file(path):
    return {"path": str(path), "sha256": "0" * 64, "sizeBytes": 0}


def _slurm_terminal_row(job_id):
    result = _run_external(
        [
            "sacct",
            "--jobs",
            job_id,
            "--allocations",
            "--noheader",
            "--parsable2",
            "--format=State,ExitCode,Start,End,ElapsedRaw,NodeList",
        ]
    )
    if result.returncode != 0:
        raise PilotError(f"cannot query Slurm accounting for job {job_id}: {result.stdout.strip()}")
    rows = [line.split("|") for line in result.stdout.splitlines() if line.strip()]
    if not rows or len(rows[0]) < 6:
        return None
    state, exit_code, started_at, ended_at, elapsed, node = rows[0][:6]
    return {
        "state": state.strip(),
        "exitCode": exit_code.strip(),
        "startedAt": started_at.strip(),
        "endedAt": ended_at.strip(),
        "elapsedSeconds": elapsed.strip(),
        "node": node.strip(),
    }


def _wait_for_terminal_job(job_id, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while True:
        row = _slurm_terminal_row(job_id)
        if (
            row is not None
            and row["state"].split()[0].rstrip("+") in SLURM_TERMINAL_STATES
        ):
            return row
        if time.monotonic() >= deadline:
            raise PilotError(
                f"job {job_id} did not reach a terminal Slurm state within "
                f"{timeout_seconds} seconds"
            )
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def _wait_for_terminal_scontrol(job_id, timeout_seconds=60):
    deadline = time.monotonic() + timeout_seconds
    last_output = ""
    while True:
        result = _run_external(["scontrol", "show", "job", "-o", job_id])
        last_output = result.stdout.strip()
        if result.returncode == 0:
            fields = _read_scontrol_evidence(result.stdout, job_id)
            if fields.get("JobState", "").rstrip("+") in SLURM_TERMINAL_STATES:
                return result
        if time.monotonic() >= deadline:
            detail = f": {last_output}" if last_output else ""
            raise PilotError(
                f"job {job_id} did not expose terminal scontrol evidence within "
                f"{timeout_seconds} seconds{detail}"
            )
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def _timestamp_utc(value, label):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PilotError(f"Slurm returned an invalid {label}: {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_sacct_evidence(content, job_id):
    rows = list(csv.DictReader(content.splitlines(), delimiter="|"))
    row = next((record for record in rows if record.get("JobID") == job_id), None)
    if row is None:
        raise PilotError(f"Slurm accounting evidence omitted job {job_id}")
    return row


def _read_scontrol_evidence(content, job_id):
    fields = {}
    for field in content.split():
        if "=" in field:
            key, value = field.split("=", 1)
            fields[key] = value
    if fields.get("JobId") != job_id:
        raise PilotError(f"Slurm control evidence omitted job {job_id}")
    return fields


def _output_filename(output_id, token):
    return {
        "bet_brain": f"sub-01_ses-test_desc-{token}_T1w.nii.gz",
        "bet_mask": f"sub-01_ses-test_desc-{token}_mask.nii.gz",
        "mask_volume": f"sub-01_ses-test_desc-{token}_mask-metrics.json",
        "boundary_qc": f"sub-01_ses-test_desc-{token}_boundary-qc.png",
    }[output_id]


def _module_identity(run_directory):
    path = run_directory / "module" / "identity.json"
    try:
        identity = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PilotError(f"allocation did not record module identity: {error}") from error
    required = {
        "exactSpecifier",
        "modulefile",
        "wrapper",
        "commandSurface",
        "wrapperContainerReference",
    }
    if not isinstance(identity, dict) or set(identity) != required:
        raise PilotError("allocation recorded malformed module identity")
    return identity


def _terminal_outcome(state, exit_code):
    if state == "COMPLETED" and exit_code == "0:0":
        return "succeeded"
    if state == "TIMEOUT":
        return "timed-out"
    if state == "CANCELLED":
        return "cancelled"
    return "failed"


def _prepare_run_snapshot(workspace_root, run_directory, contract):
    asset_project = _asset_root() / "project"
    run_project = run_directory / "project"
    for source in sorted(asset_project.rglob("*")):
        if not source.is_file():
            continue
        relative_path = source.relative_to(asset_project)
        mode = 0o755 if source.suffix in {".py", ".sh", ".sbatch"} else 0o644
        _publish_exact_file(source, run_project / relative_path, mode)

    for source in contract["inputs"]:
        staged = _input_destination(workspace_root, source)
        destination = run_directory / "inputs" / source["relativePath"]
        _publish_exact_file(staged, destination, 0o444)
        _validate_staged_input(destination, source)


def _candidate_for_run(
    workspace_root,
    contract,
    run_id,
    job_id,
    submission_argv,
    submitted_at,
    scontrol_content,
    sacct_content,
):
    run_directory = workspace_root / "runs" / run_id
    module = _module_identity(run_directory)
    scontrol = _read_scontrol_evidence(scontrol_content, job_id)
    sacct = _read_sacct_evidence(sacct_content, job_id)
    state = scontrol.get("JobState", "")
    exit_code = scontrol.get("ExitCode", "")
    outcome = _terminal_outcome(state, exit_code)

    terminal = {
        "state": state,
        "exitCode": exit_code,
        "startedAt": _timestamp_utc(scontrol.get("StartTime", ""), "job start time"),
        "endedAt": _timestamp_utc(scontrol.get("EndTime", ""), "job end time"),
        "elapsedSeconds": int(sacct.get("ElapsedRaw", "")),
        "node": scontrol.get("NodeList", ""),
    }
    if not terminal["node"]:
        raise PilotError("Slurm control evidence omitted the execution node")

    placeholder = lambda path: _artifact(path)
    universes = [
        {
            "universeId": universe["id"],
            "definition": placeholder(
                f"runs/{run_id}/project/universes/{universe['id']}.yaml"
            ),
            "decisions": {
                "bet_fractional_threshold": universe["decisionOption"]
            },
            "canonicalSha256": "0" * 64,
        }
        for universe in contract["universes"]
    ]
    base = f"runs/{run_id}"
    verification_path = run_directory / "lightcone" / "verification.json"
    success_evidence = (
        outcome == "succeeded"
        and (run_directory / "lightcone" / "status.json").is_file()
        and verification_path.is_file()
    )
    if outcome == "succeeded" and not success_evidence:
        raise PilotError("completed allocation omitted required Lightcone evidence")

    manifests = []
    outputs = []
    if success_evidence:
        try:
            verification = json.loads(verification_path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise PilotError(f"cannot read allocation verification evidence: {error}") from error
        for universe in contract["universes"]:
            for output in contract["outputs"]:
                output_key = f"{universe['id']}:{output['id']}"
                manifests.append(
                    {
                        "outputKey": output_key,
                        "artifact": placeholder(
                            f"{base}/lightcone/{universe['id']}/{output['id']}/"
                            ".lightcone-manifest.json"
                        ),
                    }
                )
                filename = _output_filename(output["id"], universe["filenameToken"])
                outputs.append(
                    {
                        "outputKey": output_key,
                        "outputId": output["id"],
                        "universeId": universe["id"],
                        "kind": output["kind"],
                        "mediaType": output["mediaType"],
                        "artifact": placeholder(
                            f"{base}/outputs/{universe['id']}/{filename}"
                        ),
                    }
                )
        verification = {
            **verification,
            "evidence": placeholder(f"{base}/lightcone/verification.json"),
        }
        ro_crate = {
            "rootRelativePath": f"exports/{run_id}/ro-crate",
            "metadata": placeholder(
                f"exports/{run_id}/ro-crate/ro-crate-metadata.json"
            ),
            "inventory": placeholder(f"exports/{run_id}/ro-crate-inventory.json"),
            "treeSha256": "0" * 64,
        }
        status_snapshot = placeholder(f"{base}/lightcone/status.json")
    else:
        verification = {
            "status": "not-run",
            "performedAt": None,
            "coverageComplete": False,
            "expectedOutputKeys": [],
            "results": [],
            "evidence": None,
        }
        ro_crate = None
        status_snapshot = None

    return {
        "schemaVersion": "1.0.0",
        "receiptId": run_id,
        "createdAt": _utc_now(),
        "workspaceRoot": str(workspace_root),
        "outcome": outcome,
        "analysis": {
            "spec": placeholder(f"runs/{run_id}/project/astra.yaml"),
            "universes": universes,
        },
        "execution": {
            "retainedScript": placeholder("project/scripts/run-bet-pilot.sbatch"),
            "module": {
                "name": "fsl",
                "version": contract["module"]["specifier"].split("/", 1)[1],
                "exactSpecifier": module["exactSpecifier"],
                "modulefile": _resolved_file(module["modulefile"]),
                "wrapper": _resolved_file(module["wrapper"]),
                "commandSurface": [
                    {
                        "name": command["name"],
                        "resolvedFile": _resolved_file(command["path"]),
                    }
                    for command in module["commandSurface"]
                ],
                "wrapperContainerReference": module["wrapperContainerReference"],
            },
        },
        "inputs": [
            {
                "inputId": source["id"],
                "sourceUri": source["url"],
                "sourceSnapshot": contract["sourceSnapshot"],
                "artifact": placeholder(
                    f"runs/{run_id}/inputs/{source['relativePath']}"
                ),
            }
            for source in contract["inputs"]
        ],
        "slurm": {
            "clusterName": "neurodesktop",
            "partition": contract["slurm"]["partition"],
            "jobId": job_id,
            "submissionArgv": submission_argv,
            "submittedAt": submitted_at,
            "resources": {
                "nodes": contract["slurm"]["nodes"],
                "tasks": contract["slurm"]["tasks"],
                "cpusPerTask": contract["slurm"]["cpusPerTask"],
                "memoryMiB": contract["slurm"]["memoryMiB"],
                "wallTimeSeconds": contract["slurm"]["wallTimeSeconds"],
            },
            "terminal": terminal,
            "evidence": {
                name: placeholder(f"{base}/slurm/{filename}")
                for name, filename in {
                    "scontrol": f"scontrol-{job_id}.txt",
                    "sacct": f"sacct-{job_id}.txt",
                    "stdout": f"astra-lightcone-bet-{job_id}.out",
                    "stderr": f"astra-lightcone-bet-{job_id}.err",
                }.items()
            },
        },
        "lightcone": {
            "version": contract["packagePins"]["lightcone-cli"],
            "runtime": "none",
            "statusSnapshot": status_snapshot,
            "manifests": manifests,
            "verification": verification,
        },
        "outputs": outputs,
        "roCrate": ro_crate,
    }


def _receipt_cli():
    installed = shutil.which("neurodesktop-pilot-receipt")
    if installed:
        return installed
    checkout = Path(__file__).with_name("neurodesktop_pilot_receipt.py")
    if checkout.is_file():
        return str(checkout)
    raise PilotError("neurodesktop-pilot-receipt is unavailable")


def _generate_receipt(workspace_root, run_directory, candidate):
    receipt_directory = run_directory / "receipt"
    receipt_directory.mkdir(parents=True, exist_ok=False)
    candidate_path = receipt_directory / "candidate.json"
    _write_text_atomic(candidate_path, json.dumps(candidate, indent=2, sort_keys=True) + "\n")
    result = _run_external(
        [
            _receipt_cli(),
            "generate",
            str(candidate_path),
            "--receipt-directory",
            str(receipt_directory),
            "--workspace-root",
            str(workspace_root),
            "--allowed-module-root",
            "/cvmfs/neurodesk.ardc.edu.au",
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise PilotError(f"receipt generation failed: {result.stdout.strip()}")
    return receipt_directory / "receipt.json"


def run_pilot(workspace_root, scheduler_timeout_seconds):
    if workspace_root.exists() and not workspace_root.is_dir():
        raise PilotError(f"workspace root is not a directory: {workspace_root}")
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace_root = workspace_root.resolve(strict=True)
    _prepare_project(workspace_root)
    contract = _load_contract(_asset_root())
    _wait_for_local_slurm(contract, scheduler_timeout_seconds)
    _stage_inputs(workspace_root, contract)

    run_id = _run_id()
    run_directory = workspace_root / "runs" / run_id
    slurm_directory = run_directory / "slurm"
    slurm_directory.mkdir(parents=True, exist_ok=False)
    _prepare_run_snapshot(workspace_root, run_directory, contract)
    relative_script = "project/scripts/run-bet-pilot.sbatch"
    output_pattern = f"runs/{run_id}/slurm/astra-lightcone-bet-%j.out"
    error_pattern = f"runs/{run_id}/slurm/astra-lightcone-bet-%j.err"
    submission_argv = [
        "sbatch",
        "--parsable",
        f"--output={output_pattern}",
        f"--error={error_pattern}",
        relative_script,
    ]
    # Slurm reports start times at whole-second precision. Record submission at
    # the same precision so an immediate start cannot appear to precede it.
    submitted_at = _utc_now(timespec="seconds")
    environment = {
        **os.environ,
        "NEURODESKTOP_PILOT_WORKSPACE": str(workspace_root),
        "NEURODESKTOP_PILOT_RUN_ID": run_id,
    }
    submission = _run_external(
        submission_argv,
        cwd=workspace_root,
        env=environment,
    )
    if submission.returncode != 0:
        raise PilotError(f"Slurm submission failed: {submission.stdout.strip()}")
    raw_job_id = submission.stdout.strip().splitlines()[-1].split(";", 1)[0]
    if not re.fullmatch(r"[0-9]+", raw_job_id):
        raise PilotError(f"sbatch returned an invalid job ID: {submission.stdout.strip()!r}")
    job_id = raw_job_id

    _wait_for_terminal_job(job_id, contract["slurm"]["wallTimeSeconds"] + 120)
    scontrol_result = _wait_for_terminal_scontrol(job_id)
    sacct_result = _run_external(
        [
            "sacct",
            "--jobs",
            job_id,
            "--allocations",
            "--parsable2",
            "--format=JobID,State,ExitCode,Start,End,ElapsedRaw,NodeList",
        ]
    )
    if sacct_result.returncode != 0:
        raise PilotError(
            f"cannot capture Slurm accounting evidence for job {job_id}: "
            f"{sacct_result.stdout.strip()}"
        )
    _write_text_atomic(slurm_directory / f"scontrol-{job_id}.txt", scontrol_result.stdout)
    _write_text_atomic(slurm_directory / f"sacct-{job_id}.txt", sacct_result.stdout)

    candidate = _candidate_for_run(
        workspace_root,
        contract,
        run_id,
        job_id,
        submission_argv,
        submitted_at,
        scontrol_result.stdout,
        sacct_result.stdout,
    )
    receipt_path = _generate_receipt(workspace_root, run_directory, candidate)
    if candidate["outcome"] != "succeeded":
        raise PilotError(
            f"Slurm job {job_id} ended as {candidate['outcome']}; "
            f"receipt: {receipt_path}"
        )
    return receipt_path


def verify_run(run_directory):
    run_directory = run_directory.resolve(strict=True)
    receipt_path = run_directory / "receipt" / "receipt.json"
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PilotError(f"cannot read finalized receipt: {error}") from error
    workspace_root = Path(receipt.get("workspaceRoot", "")).resolve(strict=True)
    expected_run_directory = workspace_root / "runs" / receipt.get("receiptId", "")
    if expected_run_directory.resolve(strict=True) != run_directory:
        raise PilotError("run directory does not match the finalized receipt identity")

    receipt_result = _run_external(
        [
            _receipt_cli(),
            "validate",
            str(receipt_path),
            "--workspace-root",
            str(workspace_root),
            "--allowed-module-root",
            "/cvmfs/neurodesk.ardc.edu.au",
        ],
        timeout=120,
    )
    if receipt_result.returncode != 0:
        raise PilotError(f"receipt verification failed: {receipt_result.stdout.strip()}")

    environment = {
        **os.environ,
        "HOME": str(run_directory / "lightcone-home"),
        "PATH": f"/opt/uv/tools/lightcone-cli/bin{os.pathsep}{os.environ['PATH']}",
    }
    lightcone_result = _run_external(
        ["lc", "verify"],
        cwd=run_directory / "project",
        env=environment,
        timeout=120,
    )
    if lightcone_result.returncode != 0:
        raise PilotError(f"fresh Lightcone verification failed: {lightcone_result.stdout.strip()}")
    return receipt_path


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="neurodesktop-astra-lightcone-pilot",
        description="Run the bounded Neurodesktop ASTRA/Lightcone BET pilot.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser(
        "prepare",
        help="stage the pinned project and inputs without submitting Slurm",
    )
    prepare_parser.add_argument("--workspace-root", required=True, type=Path)
    prepare_parser.add_argument(
        "--project-only",
        action="store_true",
        help="stage auditable project assets without downloading inputs",
    )
    run_parser = subparsers.add_parser(
        "run",
        help="prepare, submit, verify, and record the bounded pilot",
    )
    run_parser.add_argument("--workspace-root", required=True, type=Path)
    run_parser.add_argument(
        "--scheduler-timeout-seconds",
        type=int,
        default=120,
        help="maximum wait for the local neurodesktop partition",
    )
    verify_parser = subparsers.add_parser(
        "verify",
        help="read-only fresh verification of a completed run",
    )
    verify_parser.add_argument("--run-directory", required=True, type=Path)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            workspace_root = prepare(args.workspace_root, args.project_only)
            print(f"prepared: {workspace_root}")
            return 0
        if args.command == "run":
            receipt_path = run_pilot(
                args.workspace_root,
                args.scheduler_timeout_seconds,
            )
            print(f"completed: {receipt_path}")
            return 0
        if args.command == "verify":
            receipt_path = verify_run(args.run_directory)
            print(f"verified: {receipt_path}")
            return 0
    except (OSError, PilotError) as error:
        print(f"pilot failed: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
