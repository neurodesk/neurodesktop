import copy
import hashlib
import json
import os
import stat
import subprocess
import sys

import rfc8785

from testlib import repo_path, resolve_source


FIXTURE_DIR = repo_path("tests/fixtures/pilot-execution-receipts")


def _receipt_cli_path():
    return resolve_source(
        "/usr/local/bin/neurodesktop-pilot-receipt",
        "scripts/neurodesktop_pilot_receipt.py",
    )


def _write_hashed_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "sizeBytes": len(content),
    }


def _materialize_receipt(tmp_path, fixture_name):
    receipt = json.loads((FIXTURE_DIR / fixture_name).read_text())
    workspace = tmp_path / "workspace"
    module_root = tmp_path / "modules"
    receipt["workspaceRoot"] = str(workspace)

    def materialize(value):
        if isinstance(value, dict):
            if {"relativePath", "sha256", "sizeBytes"} <= value.keys():
                relative_path = value["relativePath"]
                value.update(
                    _write_hashed_file(
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
                    _write_hashed_file(
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
            _write_hashed_file(
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
            _write_hashed_file(
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
        _write_hashed_file(
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
        _write_hashed_file(
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
        _write_hashed_file(
            workspace / slurm_evidence["stdout"]["relativePath"],
            f"SLURM_JOB_ID={job_id}\nSLURMD_NODENAME={terminal['node']}\n".encode(),
        )
    )

    crate = receipt["roCrate"]
    if crate is not None:
        crate_root = workspace / crate["rootRelativePath"]
        crate_metadata_path = workspace / crate["metadata"]["relativePath"]
        crate["metadata"].update(
            _write_hashed_file(
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
            _write_hashed_file(
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
            _write_hashed_file(
                workspace / verification["evidence"]["relativePath"],
                json.dumps(verification_payload, sort_keys=True).encode(),
            )
        )

    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path, workspace, module_root


def _materialize_success_receipt(tmp_path):
    return _materialize_receipt(tmp_path, "valid-success.json")


def _materialize_timeout_receipt(tmp_path):
    return _materialize_receipt(tmp_path, "valid-timeout.json")


def _validate(receipt_path, workspace, module_root):
    return subprocess.run(
        [
            sys.executable,
            str(_receipt_cli_path()),
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


def _generate(candidate_path, receipt_directory, workspace, module_root):
    return subprocess.run(
        [
            sys.executable,
            str(_receipt_cli_path()),
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


def _write_source_candidate(receipt_path, candidate_path):
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


def test_validate_accepts_a_successful_receipt_with_matching_files(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == f"valid: {receipt_path}"


def test_validate_accepts_a_timed_out_receipt_without_outputs(tmp_path):
    receipt_path, workspace, module_root = _materialize_timeout_receipt(tmp_path)

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 0, result.stdout


def test_validate_rejects_a_mismatched_universe_hash(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    receipt["analysis"]["universes"][0]["canonicalSha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "universe bet-f-0.5 canonical SHA-256 mismatch" in result.stdout


def test_validate_rejects_incomplete_success_verification_coverage(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    receipt["lightcone"]["verification"]["results"].pop()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "successful receipt output coverage differs" in result.stdout


def test_validate_rejects_an_unsupported_lightcone_manifest_schema(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    manifest = receipt["lightcone"]["manifests"][0]
    manifest_path = workspace / manifest["artifact"]["relativePath"]
    manifest_payload = json.loads(manifest_path.read_text())
    manifest_payload["schema_version"] = 2
    manifest["artifact"].update(
        _write_hashed_file(
            manifest_path,
            json.dumps(manifest_payload, sort_keys=True).encode(),
        )
    )
    receipt["lightcone"]["verification"]["results"][0][
        "manifestSha256"
    ] = manifest["artifact"]["sha256"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "schema_version must be 1" in result.stdout


def test_validate_rejects_an_out_of_range_lightcone_timestamp_cleanly(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    manifest = receipt["lightcone"]["manifests"][0]
    manifest_path = workspace / manifest["artifact"]["relativePath"]
    manifest_payload = json.loads(manifest_path.read_text())
    manifest_payload["finished_at"] = 1e100
    manifest["artifact"].update(
        _write_hashed_file(
            manifest_path,
            json.dumps(manifest_payload, sort_keys=True).encode(),
        )
    )
    receipt["lightcone"]["verification"]["results"][0][
        "manifestSha256"
    ] = manifest["artifact"]["sha256"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "finished_at is outside the supported timestamp range" in result.stdout
    assert "Traceback" not in result.stdout


def test_validate_rejects_slurm_evidence_for_a_different_job(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    scontrol = receipt["slurm"]["evidence"]["scontrol"]
    scontrol_path = workspace / scontrol["relativePath"]
    content = scontrol_path.read_text().replace("JobId=42", "JobId=999")
    scontrol.update(_write_hashed_file(scontrol_path, content.encode()))
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "scontrol JobId does not match the receipt" in result.stdout


def test_validate_rejects_an_incomplete_ro_crate_inventory(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    inventory_artifact = receipt["roCrate"]["inventory"]
    inventory_path = workspace / inventory_artifact["relativePath"]
    inventory = json.loads(inventory_path.read_text())
    inventory.pop()
    inventory_artifact.update(
        _write_hashed_file(
            inventory_path,
            json.dumps(
                inventory,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode(),
        )
    )
    receipt["roCrate"]["treeSha256"] = hashlib.sha256(
        rfc8785.dumps(inventory)
    ).hexdigest()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "RO-Crate inventory does not match the exported files" in result.stdout


def test_generate_derives_and_atomically_publishes_a_success_receipt(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    _write_source_candidate(receipt_path, candidate_path)
    receipt_directory = workspace / "runs" / "generated" / "receipt"
    receipt_directory.mkdir(parents=True)

    result = _generate(candidate_path, receipt_directory, workspace, module_root)

    final_path = receipt_directory / "receipt.json"
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == f"generated: {final_path}"
    assert final_path.is_file()
    assert final_path.stat().st_mode & stat.S_IWUSR == 0
    assert list(receipt_directory.iterdir()) == [final_path]

    generated = json.loads(final_path.read_text())
    assert generated["trust"] == {
        "level": "executed-unverified",
        "outputIntegrity": "passed",
        "actualContainerIdentity": None,
        "actualContainerIdentityAttested": False,
        "statement": "The actual tool-container identity is not attested by this receipt.",
    }
    assert generated["generation"]["method"] == "atomic-write-fsync-rename"
    assert generated["generation"]["complete"] is True

    validation = _validate(final_path, workspace, module_root)
    assert validation.returncode == 0, validation.stdout


def test_generate_derives_a_spec_only_timed_out_receipt(tmp_path):
    receipt_path, workspace, module_root = _materialize_timeout_receipt(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    _write_source_candidate(receipt_path, candidate_path)
    receipt_directory = workspace / "runs" / "timeout" / "receipt"
    receipt_directory.mkdir(parents=True)

    result = _generate(candidate_path, receipt_directory, workspace, module_root)

    final_path = receipt_directory / "receipt.json"
    assert result.returncode == 0, result.stdout
    generated = json.loads(final_path.read_text())
    assert generated["outcome"] == "timed-out"
    assert generated["trust"]["level"] == "spec-only"
    assert generated["trust"]["outputIntegrity"] == "not-run"
    assert generated["outputs"] == []
    assert generated["roCrate"] is None

    validation = _validate(final_path, workspace, module_root)
    assert validation.returncode == 0, validation.stdout


def test_validate_accepts_slurm_allocation_cancellation_normalization(tmp_path):
    receipt_path, workspace, module_root = _materialize_timeout_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    receipt["outcome"] = "cancelled"
    receipt["slurm"]["terminal"]["state"] = "CANCELLED"
    receipt["slurm"]["terminal"]["exitCode"] = "0:15"
    scontrol = receipt["slurm"]["evidence"]["scontrol"]
    scontrol_path = workspace / scontrol["relativePath"]
    scontrol.update(
        _write_hashed_file(
            scontrol_path,
            scontrol_path.read_bytes()
            .replace(b"JobState=TIMEOUT", b"JobState=CANCELLED")
            .replace(b"ExitCode=0:0", b"ExitCode=0:15"),
        )
    )
    sacct = receipt["slurm"]["evidence"]["sacct"]
    sacct_path = workspace / sacct["relativePath"]
    sacct.update(
        _write_hashed_file(
            sacct_path,
            sacct_path.read_bytes().replace(
                b"|TIMEOUT|0:15|", b"|CANCELLED by 1000|0:0|"
            ),
        )
    )
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 0, result.stdout


def test_generate_preserves_a_scientific_decision_named_path(tmp_path):
    receipt_path, workspace, module_root = _materialize_timeout_receipt(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    _write_source_candidate(receipt_path, candidate_path)
    candidate = json.loads(candidate_path.read_text())
    candidate["analysis"]["universes"][0]["decisions"]["path"] = "smoothing"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    receipt_directory = workspace / "runs" / "path-decision" / "receipt"
    receipt_directory.mkdir(parents=True)

    result = _generate(candidate_path, receipt_directory, workspace, module_root)

    assert result.returncode == 0, result.stdout
    generated = json.loads((receipt_directory / "receipt.json").read_text())
    assert generated["analysis"]["universes"][0]["decisions"]["path"] == "smoothing"


def test_generate_never_replaces_an_existing_final_receipt(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    _write_source_candidate(receipt_path, candidate_path)
    receipt_directory = workspace / "runs" / "existing" / "receipt"
    receipt_directory.mkdir(parents=True)

    first = _generate(candidate_path, receipt_directory, workspace, module_root)
    final_path = receipt_directory / "receipt.json"
    original = final_path.read_bytes()
    second = _generate(candidate_path, receipt_directory, workspace, module_root)

    assert first.returncode == 0, first.stdout
    assert second.returncode == 2
    assert "final receipt already exists" in second.stdout
    assert final_path.read_bytes() == original
    assert list(receipt_directory.iterdir()) == [final_path]


def test_failed_generation_leaves_no_final_or_temporary_receipt(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    _write_source_candidate(receipt_path, candidate_path)
    candidate = json.loads(candidate_path.read_text())
    candidate["lightcone"]["verification"]["results"].pop()
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    receipt_directory = workspace / "runs" / "invalid" / "receipt"
    receipt_directory.mkdir(parents=True)

    result = _generate(candidate_path, receipt_directory, workspace, module_root)

    assert result.returncode == 2
    assert "successful receipt output coverage differs" in result.stdout
    assert list(receipt_directory.iterdir()) == []


def test_validate_rejects_duplicate_input_identities(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    receipt["inputs"][1]["inputId"] = receipt["inputs"][0]["inputId"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "inputs contains duplicate inputId values" in result.stdout


def test_validate_rejects_modified_artifact_bytes(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    artifact = receipt["outputs"][0]["artifact"]
    artifact_path = workspace / artifact["relativePath"]
    content = artifact_path.read_bytes()
    artifact_path.write_bytes(bytes([content[0] ^ 1]) + content[1:])

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "SHA-256 mismatch" in result.stdout


def test_validate_rejects_a_workspace_symlink_escape(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    artifact = receipt["inputs"][0]["artifact"]
    artifact_path = workspace / artifact["relativePath"]
    outside = tmp_path / "outside-input.json"
    outside.write_bytes(artifact_path.read_bytes())
    artifact_path.unlink()
    artifact_path.symlink_to(outside)

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "escapes its allowed roots" in result.stdout


def test_validate_rejects_a_special_evidence_file(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    artifact = receipt["slurm"]["evidence"]["stderr"]
    artifact_path = workspace / artifact["relativePath"]
    artifact_path.unlink()
    os.mkfifo(artifact_path)

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "is not a regular file" in result.stdout


def test_validate_rejects_an_unsupported_receipt_version(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    receipt["schemaVersion"] = "2.0.0"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "schema violation" in result.stdout


def test_validate_rejects_an_unknown_receipt_field(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    receipt["unrecognizedEvidence"] = {}
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "schema violation" in result.stdout


def test_validate_rejects_executed_trust_without_a_canonical_manifest(tmp_path):
    receipt_path, workspace, module_root = _materialize_timeout_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    receipt["trust"]["level"] = "executed-unverified"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "derived trust level should be 'spec-only'" in result.stdout


def test_validate_rejects_duplicate_workspace_artifact_targets(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    receipt["inputs"][1]["artifact"] = copy.deepcopy(
        receipt["inputs"][0]["artifact"]
    )
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "duplicates workspace artifact target" in result.stdout


def test_validate_rejects_receipt_finalization_before_job_completion(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    receipt["generation"]["finalizedAt"] = "2026-07-29T19:04:00Z"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "receipt was finalized before the Slurm job ended" in result.stdout


def test_validate_rejects_submission_of_a_different_script(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    receipt["slurm"]["submissionArgv"][-1] = "project/scripts/other.sbatch"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "Slurm submission does not invoke the retained script" in result.stdout


def test_validate_rejects_slurm_resources_that_differ_from_scontrol(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    scontrol = receipt["slurm"]["evidence"]["scontrol"]
    scontrol_path = workspace / scontrol["relativePath"]
    content = scontrol_path.read_text().replace("NumNodes=1", "NumNodes=2")
    scontrol.update(_write_hashed_file(scontrol_path, content.encode()))
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "scontrol NumNodes does not match the receipt" in result.stdout


def test_validate_rejects_a_module_specifier_that_disagrees_with_its_identity(
    tmp_path,
):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    receipt["execution"]["module"]["exactSpecifier"] = "fsl/6.0.7.21"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "exact module specifier does not match name and version" in result.stdout


def test_validate_rejects_an_incomplete_success_status_snapshot(tmp_path):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    status_artifact = receipt["lightcone"]["statusSnapshot"]
    status_path = workspace / status_artifact["relativePath"]
    status = json.loads(status_path.read_text())
    status["universes"][0]["outputs"].pop()
    status_artifact.update(
        _write_hashed_file(
            status_path,
            json.dumps(status, sort_keys=True).encode(),
        )
    )
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "Lightcone status coverage differs from receipt outputs" in result.stdout


def test_validate_rejects_verification_evidence_that_differs_from_the_receipt(
    tmp_path,
):
    receipt_path, workspace, module_root = _materialize_success_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text())
    evidence_artifact = receipt["lightcone"]["verification"]["evidence"]
    evidence_path = workspace / evidence_artifact["relativePath"]
    evidence = json.loads(evidence_path.read_text())
    evidence["coverageComplete"] = False
    evidence_artifact.update(
        _write_hashed_file(
            evidence_path,
            json.dumps(evidence, sort_keys=True).encode(),
        )
    )
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _validate(receipt_path, workspace, module_root)

    assert result.returncode == 2
    assert "verification evidence differs from the receipt" in result.stdout
