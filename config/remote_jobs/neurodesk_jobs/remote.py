"""Cluster-side helper: object store, submission journal and allocation worker.

Runs with the cluster's own Python (3.9 or newer) and the standard library only.
The cluster needs Slurm and, on compute nodes, Apptainer or Singularity; it does
not need DataLad or git-annex.
"""

import getpass
import hashlib
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

from .core import (JobError, atomic, canonical, digest, identifier, inventory,
                   read, relative, resources, run, site as checked_site, timestamp, verify)

RESERVED = {"execution.json", "outcome.json", "worker.log", ".gitattributes", ".gitmodules"}
GIT_METADATA = {".gitattributes", ".gitignore", ".gitmodules"}


def run_directory(site, run_id):
    return Path(site["state_root"]) / "runs" / identifier(run_id)


def object_path(data_root, sha):
    if not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise JobError("Invalid object checksum")
    return Path(data_root) / "objects" / sha[:2] / sha


def staged_objects(manifest):
    """Files the worker needs: the image and selected inputs, not source bundles."""
    return {name: entry for name, entry in manifest["files"].items()
            if name == "runtime.sif" or name.startswith("data/")}


def present(path, size):
    return path.is_file() and path.stat().st_size == size


def missing(value):
    """Objects to upload: absent, or truncated by an interrupted copy."""
    site = checked_site(value["site"])
    return {"missing": sorted(sha for sha, size in value["objects"].items()
                              if not present(object_path(site["data_root"], sha), size))}


def put(data_root, sha, size):
    """Store stdin as one object after checking its size and SHA-256."""
    target = object_path(data_root, sha)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if present(target, int(size)):
        for _ in iter(lambda: sys.stdin.buffer.read(8 * 1024 * 1024), b""):
            pass
        return
    result = hashlib.sha256()
    received = 0
    fd, name = tempfile.mkstemp(prefix=".put-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            for chunk in iter(lambda: sys.stdin.buffer.read(8 * 1024 * 1024), b""):
                result.update(chunk)
                received += len(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if received != int(size) or result.hexdigest() != sha:
            raise JobError("Upload was incomplete or corrupted; run stage again")
        os.chmod(name, 0o400)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def get(value):
    """Write one published output file of an attempt to stdout."""
    site = checked_site(value["site"])
    attempt = value["attempt_id"]
    if not re.fullmatch(r"[0-9]+-[0-9]+-[a-f0-9]{32}", attempt):
        raise JobError("Invalid attempt ID")
    journal = run_directory(site, value["run_id"]) / "attempts" / attempt
    receipt = read(journal / "receipt.json")
    name = relative(value["path"])
    if name not in receipt["files"]:
        raise JobError(f"{name} is not a published result of this attempt")
    with (Path(site["data_root"]) / "work" / attempt / "output" / name).open("rb") as stream:
        shutil.copyfileobj(stream, sys.stdout.buffer, 8 * 1024 * 1024)


def prepare(value):
    site = checked_site(value["site"])
    if digest(value["manifest"]) != value["request_digest"]:
        raise JobError("Manifest does not match its digest")
    for name, entry in staged_objects(value["manifest"]).items():
        path = object_path(site["data_root"], entry["sha256"])
        if not path.is_file() or path.stat().st_size != entry["size"]:
            raise JobError(f"Staged content is missing: {name}; run stage again")
    directory = run_directory(site, value["run_id"])
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        if not (directory / "request.json").exists():
            raise JobError("Incomplete preparation needs reconciliation; use a new run") from None
        existing = read(directory / "request.json")
        # Each helper version has its own path; an upgrade may restage a run.
        if {k: v for k, v in existing.items() if k != "helper"} != {
                k: v for k, v in value.items() if k != "helper"}:
            raise JobError("Run ID already belongs to a different request") from None
    else:
        atomic(directory / "request.json", value)
    (directory / "attempts").mkdir(exist_ok=True, mode=0o700)
    return {"prepared": True}


def submit(value):
    site = checked_site(value["site"])
    directory = run_directory(site, value["run_id"])
    if not (directory / "request.json").is_file():
        raise JobError("Remote journal missing: submission-unknown; do not recreate or resubmit")
    request = read(directory / "request.json")
    if request["request_digest"] != value["request_digest"] or request["site"] != site:
        raise JobError("Submission does not match the prepared request")
    if (directory / "submit.claim").exists():
        return submission_record(directory)
    command = ["env", f"PATH={site['path']}", site["python"], request["helper"],
               "_worker", str(directory)]
    script = "#!/bin/sh\nset -eu\numask 077\nexec " + shlex.join(command) + "\n"
    options = resources(request["manifest"]["request"].get("slurm"))
    flags = [f"--{key}={setting}" for key, setting in sorted(options.items())]
    try:
        # Validates options without submitting, so a typo in the request's slurm
        # section fails here instead of leaving an unrecoverable claim.
        run(["sbatch", "--test-only", *flags], input=script.encode(), timeout=60)
    except JobError as exc:
        raise JobError(f"Slurm rejected this job's options, so nothing was submitted: {exc}\n"
                       "Fix the slurm section of the request, then run prepare and submit "
                       "again.") from None
    try:
        (directory / "submit.claim").mkdir()
    except FileExistsError:
        return submission_record(directory)
    record = {"state": "submission-unknown", "created": timestamp(),
              "request_digest": value["request_digest"], "owner": getpass.getuser(),
              "job_name": "nj-" + value["run_id"],
              "comment": "nj:" + value["run_id"] + ":" + value["request_digest"]}
    atomic(directory / "submission.json", record)
    argv = ["sbatch", "--parsable", "--no-requeue", "--export=NONE",
            f"--job-name={record['job_name']}", f"--comment={record['comment']}",
            f"--output={directory}/slurm-%j.log", f"--error={directory}/slurm-%j.log", *flags]
    try:
        response = run(argv, input=script.encode(), timeout=60)
        if not re.fullmatch(r"[0-9]+", response):
            raise JobError("Unrecognized or federated sbatch response")
        record.update(state="accepted", job_id=response)
        atomic(directory / "submission.json", record)
    except JobError as exc:
        # A failed response is never permission to submit again. Even a client
        # timeout may occur after the scheduler has accepted the job.
        record["error"] = str(exc)
        atomic(directory / "submission.json", record)
    return record


def submission_record(directory):
    file = directory / "submission.json"
    return read(file) if file.exists() else {"state": "submission-unknown"}


def scheduler(record):
    """Return an authoritative correlated row, or retain uncertainty."""
    if "created" not in record:
        return {"state": "UNKNOWN"}
    found = {}
    errors = []
    try:
        rows = run(["squeue", "--noheader", "--user", record["owner"],
                    "--name", record["job_name"], "--format=%i|%j|%k|%T"], timeout=30)
        for row in rows.splitlines():
            job, name, comment, state = row.strip().split("|", 3)
            if name == record["job_name"] and comment == record["comment"] and job.isdigit():
                found[job] = {"job_id": job, "state": state}
    except (JobError, ValueError) as exc:
        errors.append(str(exc))
    try:
        rows = run(["sacct", "--noheader", "--parsable2", "--allocations",
                    "--user", record["owner"], "--name", record["job_name"],
                    "--starttime", record["created"].split("T")[0],
                    "--format=JobIDRaw,JobName%64,Comment%128,State,ExitCode,User%64"],
                   env=dict(os.environ, TZ="UTC"), timeout=30)
        for row in rows.splitlines():
            job, name, comment, state, exit_code, owner = row.strip().split("|")[:6]
            correlated = comment == record["comment"] or (
                not comment and job == record.get("job_id"))
            if (job.isdigit() and name == record["job_name"]
                    and owner == record["owner"] and correlated):
                # An active queue row overrides lagging accounting from a prior start.
                found.setdefault(job, {"job_id": job, "state": state.split()[0],
                                       "exit_code": exit_code})
    except (JobError, ValueError) as exc:
        errors.append(str(exc))
    if len(found) != 1 or (record.get("job_id") and record["job_id"] not in found):
        return {"state": "UNKNOWN", "matches": list(found.values()), "errors": errors}
    return next(iter(found.values()))


def status(value):
    directory = run_directory(checked_site(value["site"]), value["run_id"])
    if not (directory / "request.json").is_file():
        return {"submission": {"state": "submission-unknown"},
                "scheduler": {"state": "UNKNOWN"}, "attempts": []}
    record = submission_record(directory)
    observed = scheduler(record)
    attempts = []
    for path in sorted((directory / "attempts").iterdir()):
        if (path / "state.json").is_file():
            attempt = read(path / "state.json")
            if (path / "receipt.json").is_file():
                attempt["receipt"] = read(path / "receipt.json")
            attempts.append(attempt)
    return {"submission": record, "scheduler": observed, "attempts": attempts}


def cancel(value):
    observed = status(value)
    job = observed["scheduler"].get("job_id")
    if not job:
        raise JobError("Cannot cancel an uncorrelated job; reconcile with the site")
    run(["scancel", job], timeout=30)
    return {"state": "cancel-requested", "job_id": job}


def container_runtime():
    """Apptainer is often installed on compute nodes only."""
    for name in ("apptainer", "singularity"):
        found = shutil.which(name)
        if found:
            return found
    return None


def doctor(value):
    """Check what login-node commands need; Apptainer is checked in the job."""
    site = checked_site(value["site"])
    checks = {"python": platform.python_version()}
    for argv in (["sbatch", "--version"], ["squeue", "--version"], ["sacct", "--version"]):
        checks[argv[0]] = run(argv)
    for key in ("state_root", "data_root"):
        Path(site[key]).mkdir(parents=True, exist_ok=True, mode=0o700)
        if not os.access(site[key], os.W_OK):
            raise JobError(f"{key} {site[key]} is not writable")
        checks[key] = "writable"
    runtime = container_runtime()
    checks["apptainer"] = (run([runtime, "--version"]) if runtime
                           else "not on login nodes; jobs look for it on the compute node")
    return {"checks": checks}


def logs(value):
    directory = run_directory(checked_site(value["site"]), value["run_id"])
    attempt = value.get("attempt")
    if attempt:
        if not re.fullmatch(r"[0-9]+-[0-9]+-[a-f0-9]{32}", attempt):
            raise JobError("Invalid attempt ID")
        paths = [directory / "attempts" / attempt / "worker.log"]
    else:
        paths = sorted(directory.glob("slurm-*.log"))
    result = {}
    for path in paths:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 65536))
            result[str(path.relative_to(directory))] = stream.read().decode(errors="replace")
    return result


def container_command(runtime, image, inputs, outputs, argv, readonly=False):
    return [runtime, "exec", "--containall", "--cleanenv", "--no-home",
            "--bind", f"{inputs}:/inputs:ro", "--bind", f"{outputs}:/outputs:{'ro' if readonly else 'rw'}",
            "--pwd", "/outputs", str(image), *argv]


def container_environment():
    """Drop site-wide bind defaults so only /inputs and /outputs are mounted."""
    return {key: value for key, value in os.environ.items()
            if not re.match(r"(APPTAINER|SINGULARITY)_BIND", key)}


def published_files(output):
    files = []
    for path in sorted(output.rglob("*")):
        name = path.relative_to(output).as_posix()
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise JobError(f"Outputs may contain only regular files: {name}")
        if path.is_file():
            relative(name)
            if Path(name).name in GIT_METADATA:
                raise JobError(f"Outputs may not contain Git metadata: {name}")
            files.append(name)
    return files


def worker(directory):
    directory = Path(directory)
    request = read(directory / "request.json")
    site = checked_site(request["site"])
    manifest = request["manifest"]
    job = os.environ.get("SLURM_JOB_ID", "")
    restart = os.environ.get("SLURM_RESTART_COUNT", "0")
    if not job.isdigit() or not restart.isdigit():
        raise JobError("Worker must run inside a Slurm allocation")
    attempt_id = f"{job}-{restart}-{uuid.uuid4().hex}"
    journal = directory / "attempts" / attempt_id
    journal.mkdir(mode=0o700)
    workspace = Path(site["data_root"]) / "work" / attempt_id
    workspace.mkdir(parents=True, mode=0o700)
    state = {"attempt_id": attempt_id, "job_id": job, "restart_count": int(restart),
             "run_id": request["run_id"], "started": timestamp(), "state": "staging",
             "compute_host": platform.node()}
    atomic(journal / "state.json", state)
    os.environ.pop("SSH_AUTH_SOCK", None)
    log_path = journal / "worker.log"
    copying = False
    try:
        if digest(manifest) != request["request_digest"]:
            raise JobError("Input manifest does not match submission")
        runtime = container_runtime()
        if not runtime:
            raise JobError(f"Neither apptainer nor singularity is on the PATH of compute "
                           f"node {platform.node()}: {os.environ.get('PATH', '')}. If the "
                           "site provides it as a module, add its bin directory to \"path\" "
                           "in the desktop's site.json, then prepare a new run")
        tools = {"python": platform.python_version(), "container": run([runtime, "--version"])}
        # Copy inputs out of the shared store so the analysis cannot alter it.
        copying = True
        inputs = workspace / "input"
        wanted = staged_objects(manifest)
        for name, entry in wanted.items():
            if name.startswith("data/"):
                target = inputs / name[len("data/"):]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(object_path(site["data_root"], entry["sha256"]), target)
                target.chmod(0o700 if name in manifest["executables"] else 0o600)
        image = object_path(site["data_root"], wanted["runtime.sif"]["sha256"])
        try:
            verify(inputs, {name[len("data/"):]: entry for name, entry in wanted.items()
                            if name.startswith("data/")})
            verify(image.parent, {image.name: wanted["runtime.sif"]})
        except JobError as exc:
            raise JobError(f"{exc}. The copy in {site['data_root']}/objects is damaged: "
                           "delete it there, then prepare and submit a new run") from None
        copying = False
        output = workspace / "output"
        output.mkdir(mode=0o700)
        state["state"] = "running"
        atomic(journal / "state.json", state)
        environment = container_environment()
        with log_path.open("ab", buffering=0) as log:
            command = manifest["request"]["command"]
            result = subprocess.run(container_command(runtime, image, inputs, output, command),
                                    stdout=log, stderr=log, env=environment, check=False)
            state["analysis_exit_code"] = result.returncode
            atomic(journal / "state.json", state)
            if result.returncode:
                raise JobError("Analysis failed; see worker.log")
            for name in manifest["request"]["outputs"]:
                path = output / name
                if not path.is_file() or path.stat().st_size == 0:
                    raise JobError(f"Expected nonempty output missing: {name}")
            validation = subprocess.run(container_command(
                runtime, image, inputs, output, manifest["request"]["validate"], readonly=True),
                stdout=log, stderr=log, env=environment, check=False)
            state["validation_exit_code"] = validation.returncode
            if validation.returncode:
                raise JobError("Scientific validation failed; see worker.log")
        produced = published_files(output)
        if set(produced) & RESERVED:
            raise JobError("The analysis wrote a file name reserved for job provenance")
        state["state"] = "publishing"
        atomic(journal / "state.json", state)
        atomic(output / "execution.json", {
            "run_id": request["run_id"], "attempt_id": attempt_id, "manifest": manifest,
            "request_digest": request["request_digest"], "site": site, "tools": tools,
            "compute_host": platform.node(), "command": container_command(
                runtime, "runtime.sif", "INPUTS", "OUTPUTS", manifest["request"]["command"])})
        atomic(output / "outcome.json", state)
        shutil.copyfile(log_path, output / "worker.log")
        files = [*produced, "execution.json", "outcome.json", "worker.log"]
        for name in files:
            (output / name).chmod(0o400)
        receipt = {"run_id": request["run_id"], "attempt_id": attempt_id,
                   "request_digest": request["request_digest"], "published": timestamp(),
                   "files": inventory(output, files)}
        atomic(journal / "receipt.json", receipt)
        state["state"] = "site-results-available"
        atomic(journal / "state.json", state)
        return 0
    except Exception as exc:
        state.update(state="inputs-missing" if copying else "failed", error=str(exc))
        atomic(journal / "state.json", state)
        with log_path.open("ab") as log:
            log.write(canonical({"error": str(exc)}) + b"\n")
        return 1


OPERATIONS = {"missing": missing, "prepare": prepare, "submit": submit, "status": status,
              "cancel": cancel, "doctor": doctor, "logs": logs}
