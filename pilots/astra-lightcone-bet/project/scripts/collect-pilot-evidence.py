#!/usr/bin/env python3
"""Capture allocation-local module identity and Lightcone result evidence."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _atomic_json(destination, payload):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with source.open("rb") as source_stream, os.fdopen(
            descriptor, "wb"
        ) as destination_stream:
            shutil.copyfileobj(source_stream, destination_stream)
            destination_stream.flush()
            os.fsync(destination_stream.fileno())
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_contract(workspace):
    return json.loads((workspace / "pilot-contract.json").read_text())


def _capture_module(workspace, run_directory):
    contract = _load_contract(workspace)
    modulefiles = [
        Path(value).resolve(strict=True)
        for value in os.environ.get("_LMFILES_", "").split(":")
        if value
    ]
    matching_modulefiles = [
        path
        for path in modulefiles
        if contract["module"]["specifier"].replace("/", os.sep) in str(path)
        or (
            path.stem == contract["module"]["specifier"].split("/", 1)[1]
            and "fsl" in path.parts
        )
    ]
    if len(matching_modulefiles) != 1:
        raise RuntimeError(
            "could not identify exactly one loaded FSL modulefile from _LMFILES_: "
            + ":".join(map(str, modulefiles))
        )

    commands = []
    for name in contract["module"]["commands"]:
        resolved = shutil.which(name)
        if resolved is None:
            raise RuntimeError(f"loaded module did not expose {name}")
        commands.append({"name": name, "path": str(Path(resolved).resolve(strict=True))})

    wrapper = Path(commands[0]["path"])
    wrapper_text = wrapper.read_text(errors="replace")
    image_matches = re.findall(r"(/[A-Za-z0-9_./+-]+\.simg)\b", wrapper_text)
    payload = {
        "exactSpecifier": contract["module"]["specifier"],
        "modulefile": str(matching_modulefiles[0]),
        "wrapper": str(wrapper),
        "commandSurface": commands,
        "wrapperContainerReference": image_matches[0] if image_matches else None,
    }
    _atomic_json(run_directory / "module" / "identity.json", payload)


def _filename(output_id, token):
    names = {
        "bet_brain": f"sub-01_ses-test_desc-{token}_T1w.nii.gz",
        "bet_mask": f"sub-01_ses-test_desc-{token}_mask.nii.gz",
        "mask_volume": f"sub-01_ses-test_desc-{token}_mask-metrics.json",
        "boundary_qc": f"sub-01_ses-test_desc-{token}_boundary-qc.png",
    }
    return names[output_id]


def _collect_outputs(workspace, run_directory):
    from lightcone.engine.verify import verify_outputs

    contract = _load_contract(workspace)
    project = run_directory / "project"
    expected_keys = [
        f"{universe['id']}:{output['id']}"
        for universe in contract["universes"]
        for output in contract["outputs"]
    ]
    results_by_key = {}
    for universe in contract["universes"]:
        for result in verify_outputs(project, universe_id=universe["id"]):
            results_by_key[f"{universe['id']}:{result.output_id}"] = result

    verification_results = []
    for output_key in expected_keys:
        universe_id, output_id = output_key.split(":", 1)
        result = results_by_key.get(output_key)
        manifest = (
            project
            / "results"
            / universe_id
            / output_id
            / ".lightcone-manifest.json"
        )
        manifest_sha256 = _sha256(manifest) if manifest.is_file() else "0" * 64
        passed = result is not None and result.passed and manifest.is_file()
        failure = None
        if not passed:
            failure = result.failure if result is not None else "missing_output"
        verification_results.append(
            {
                "outputKey": output_key,
                "status": "passed" if passed else "failed",
                "failure": failure,
                "manifestSha256": manifest_sha256,
            }
        )

    coverage_complete = set(results_by_key) == set(expected_keys)
    passed = coverage_complete and all(
        result["status"] == "passed" for result in verification_results
    )
    verification = {
        "status": "passed" if passed else "failed",
        "performedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "coverageComplete": coverage_complete,
        "expectedOutputKeys": expected_keys,
        "results": verification_results,
    }
    _atomic_json(run_directory / "lightcone" / "verification.json", verification)
    if not passed:
        failures = [
            result["outputKey"]
            for result in verification_results
            if result["status"] == "failed"
        ]
        raise RuntimeError(f"Lightcone verification did not cover clean outputs: {failures}")

    for universe in contract["universes"]:
        for output in contract["outputs"]:
            source_directory = (
                project / "results" / universe["id"] / output["id"]
            )
            _atomic_copy(
                source_directory / ".lightcone-manifest.json",
                run_directory
                / "lightcone"
                / universe["id"]
                / output["id"]
                / ".lightcone-manifest.json",
            )
            filename = _filename(output["id"], universe["filenameToken"])
            _atomic_copy(
                source_directory / filename,
                run_directory / "outputs" / universe["id"] / filename,
            )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("capture-module", "collect-outputs"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--run-directory", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == "capture-module":
            _capture_module(args.workspace, args.run_directory)
        else:
            _collect_outputs(args.workspace, args.run_directory)
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"pilot evidence collection failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
