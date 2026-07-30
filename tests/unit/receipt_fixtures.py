"""Shared pilot-execution-receipt fixture builders for the unit tier.

Both the receipt CLI tests and the ASTRA viewer tests need a fully materialized,
hash-consistent receipt on disk. That construction lives here so neither test
module has to import the other's private helpers.
"""

import hashlib
import json
import subprocess
import sys

import rfc8785

from testlib import repo_path, resolve_source


FIXTURE_DIR = repo_path("tests/fixtures/pilot-execution-receipts")


def receipt_cli_path():
    return resolve_source(
        "/usr/local/bin/neurodesktop-pilot-receipt",
        "scripts/neurodesktop_pilot_receipt.py",
    )


def write_hashed_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "sizeBytes": len(content),
    }


def materialize_receipt(tmp_path, fixture_name):
    receipt = json.loads((FIXTURE_DIR / fixture_name).read_text())
    workspace = tmp_path / "workspace"
    module_root = tmp_path / "modules"
    receipt["workspaceRoot"] = str(workspace)

    def materialize(value):
        if isinstance(value, dict):
            if {"relativePath", "sha256", "sizeBytes"} <= value.keys():
                relative_path = value["relativePath"]
                value.update(
                    write_hashed_file(
                        workspace / relative_path,
                        f"workspace artifact: {relative_path}\n".encode(),
                    )
                )
                return
            if {"path", "sha256", "sizeBytes"} <= value.keys():
                original_path = value["path"]
                resolved_path = module_root / original_path.lstrip("/")
                value["path"] = str(resolved_path)
                value.update(
                    write_hashed_file(
                        resolved_path,
                        f"resolved module file: {original_path}\n".encode(),
                    )
                )
                return
            for child in value.values():
                materialize(child)
        elif isinstance(value, list):
            for child in value:
                materialize(child)

    materialize(receipt)

    for universe in receipt["analysis"]["universes"]:
        canonical = rfc8785.dumps(
            {
                "universeId": universe["universeId"],
                "decisions": universe["decisions"],
            }
        )
        universe["canonicalSha256"] = hashlib.sha256(canonical).hexdigest()

    decisions_by_universe = {
        universe["universeId"]: universe["decisions"]
        for universe in receipt["analysis"]["universes"]
    }
    for manifest in receipt["lightcone"]["manifests"]:
        universe_id, output_id = manifest["outputKey"].split(":", 1)
        manifest_payload = {
            "schema_version": 1,
            "output_id": output_id,
            "universe_id": universe_id,
            "recipe": "run-bet-pilot.sh {input} {output}",
            "decisions": decisions_by_universe[universe_id],
            "code_version": "sha256:" + "a" * 64,
            "container_image": None,
            "worker_image": None,
            "data_version": "sha256:" + "b" * 64,
            "input_versions": {"t1w": "c" * 64},
            "git_sha": "d" * 40,
            "git_remote": "https://github.com/neurodesk/neurodesktop.git",
            "lc_version": "0.4.0",
            "finished_at": 1785351870.0,
            "host": "neurodesktop",
            "slurm_job_id": receipt["slurm"]["jobId"],
        }
        manifest["artifact"].update(
            write_hashed_file(
                workspace / manifest["artifact"]["relativePath"],
                json.dumps(manifest_payload, sort_keys=True).encode(),
            )
        )

    outputs_by_universe = {}
    for output in receipt["outputs"]:
        outputs_by_universe.setdefault(output["universeId"], []).append(output)
    status_payload = {
        "universes": [
            {
                "universe_id": universe_id,
                "outputs": [
                    {
                        "output_id": output["outputId"],
                        "analysis_id": None,
                        "status": "ok",
                        "recipe_command": "run-bet-pilot.sh {input} {output}",
                    }
                    for output in outputs
                ],
            }
            for universe_id, outputs in outputs_by_universe.items()
        ]
    }
    if receipt["lightcone"]["statusSnapshot"] is not None:
        receipt["lightcone"]["statusSnapshot"].update(
            write_hashed_file(
                workspace
                / receipt["lightcone"]["statusSnapshot"]["relativePath"],
                json.dumps(status_payload, sort_keys=True).encode(),
            )
        )

    terminal = receipt["slurm"]["terminal"]
    job_id = receipt["slurm"]["jobId"]
    resources = receipt["slurm"]["resources"]
    slurm_evidence = receipt["slurm"]["evidence"]
    slurm_evidence["scontrol"].update(
        write_hashed_file(
            workspace / slurm_evidence["scontrol"]["relativePath"],
            (
                f"JobId={job_id} JobState={terminal['state']} "
                f"ExitCode={terminal['exitCode']} StartTime={terminal['startedAt']} "
                f"EndTime={terminal['endedAt']} NodeList={terminal['node']} "
                f"Partition={receipt['slurm']['partition']} "
                f"NumNodes={resources['nodes']} NumTasks={resources['tasks']} "
                f"CPUs/Task={resources['cpusPerTask']} "
                f"MinMemoryNode={resources['memoryMiB']}M "
                f"TimeLimit=00:15:00\n"
            ).encode(),
        )
    )
    slurm_evidence["sacct"].update(
        write_hashed_file(
            workspace / slurm_evidence["sacct"]["relativePath"],
            (
                "JobID|State|ExitCode|Start|End|ElapsedRaw|NodeList\n"
                f"{job_id}|{terminal['state']}|{terminal['exitCode']}|"
                f"{terminal['startedAt']}|{terminal['endedAt']}|"
                f"{terminal['elapsedSeconds']}|{terminal['node']}\n"
            ).encode(),
        )
    )
    slurm_evidence["stdout"].update(
        write_hashed_file(
            workspace / slurm_evidence["stdout"]["relativePath"],
            f"SLURM_JOB_ID={job_id}\nSLURMD_NODENAME={terminal['node']}\n".encode(),
        )
    )

    crate = receipt["roCrate"]
    if crate is not None:
        crate_root = workspace / crate["rootRelativePath"]
        crate_metadata_path = workspace / crate["metadata"]["relativePath"]
        crate["metadata"].update(
            write_hashed_file(
                crate_metadata_path,
                json.dumps(
                    {
                        "@context": "https://w3id.org/ro/crate/1.1/context",
                        "@graph": [],
                    },
                    sort_keys=True,
                ).encode(),
            )
        )

        crate_members = [receipt["analysis"]["spec"]]
        crate_members.extend(
            universe["definition"] for universe in receipt["analysis"]["universes"]
        )
        crate_members.extend(
            manifest["artifact"] for manifest in receipt["lightcone"]["manifests"]
        )
        crate_members.extend(output["artifact"] for output in receipt["outputs"])
        for index, artifact in enumerate(crate_members):
            source = workspace / artifact["relativePath"]
            destination = crate_root / "payload" / f"{index:03d}-{source.name}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())

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
            write_hashed_file(
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

    manifest_hashes = {
        manifest["outputKey"]: manifest["artifact"]["sha256"]
        for manifest in receipt["lightcone"]["manifests"]
    }
    for result in receipt["lightcone"]["verification"]["results"]:
        result["manifestSha256"] = manifest_hashes[result["outputKey"]]

    verification = receipt["lightcone"]["verification"]
    verification_payload = {
        key: value for key, value in verification.items() if key != "evidence"
    }
    if verification["evidence"] is not None:
        verification["evidence"].update(
            write_hashed_file(
                workspace / verification["evidence"]["relativePath"],
                json.dumps(verification_payload, sort_keys=True).encode(),
            )
        )

    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path, workspace, module_root


def materialize_success_receipt(tmp_path):
    return materialize_receipt(tmp_path, "valid-success.json")


def materialize_timeout_receipt(tmp_path):
    return materialize_receipt(tmp_path, "valid-timeout.json")


def validate(receipt_path, workspace, module_root):
    return subprocess.run(
        [
            sys.executable,
            str(receipt_cli_path()),
            "validate",
            str(receipt_path),
            "--workspace-root",
            str(workspace),
            "--allowed-module-root",
            str(module_root),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )


def generate(candidate_path, receipt_directory, workspace, module_root):
    return subprocess.run(
        [
            sys.executable,
            str(receipt_cli_path()),
            "generate",
            str(candidate_path),
            "--receipt-directory",
            str(receipt_directory),
            "--workspace-root",
            str(workspace),
            "--allowed-module-root",
            str(module_root),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )


def write_source_candidate(receipt_path, candidate_path):
    candidate = json.loads(receipt_path.read_text())
    candidate.pop("trust")
    candidate.pop("generation")

    def remove_derived(value):
        if isinstance(value, dict):
            if {"relativePath", "sha256", "sizeBytes"} <= value.keys() or {
                "path",
                "sha256",
                "sizeBytes",
            } <= value.keys():
                value.pop("sha256")
                value.pop("sizeBytes")
                return
            for child in value.values():
                remove_derived(child)
        elif isinstance(value, list):
            for child in value:
                remove_derived(child)

    remove_derived(candidate)
    for universe in candidate["analysis"]["universes"]:
        universe.pop("canonicalSha256")
    for result in candidate["lightcone"]["verification"]["results"]:
        result.pop("manifestSha256")
    if candidate["roCrate"] is not None:
        candidate["roCrate"].pop("treeSha256")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
