"""Short-lived submission helper and allocation-local execution."""

import getpass
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import uuid

from . import data
from .core import (JobError, atomic, canonical, digest, git, identifier, profile,
                   read, run, timestamp, verify)


def run_directory(site, run_id):
    return Path(site["state_root"]) / "runs" / identifier(run_id)


def prepare(value):
    site = profile(value["site"])
    directory = run_directory(site, value["run_id"])
    for root in (site["state_root"], site["store_root"], site["work_root"]):
        Path(root).mkdir(parents=True, exist_ok=True, mode=0o700)
    store_version = Path(site["store_root"]) / "ria-layout-version"
    try:
        with store_version.open("x") as stream:
            stream.write("1\n")
    except FileExistsError:
        if store_version.read_text().strip() != "1":
            raise JobError("Unsupported RIA store layout") from None
    try:
        directory.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        if not (directory / "request.json").exists():
            raise JobError("Incomplete preparation needs reconciliation; use a new run") from None
        if read(directory / "request.json") != value:
            raise JobError("Run ID already belongs to a different request") from None
        return {"prepared": True}
    atomic(directory / "request.json", value)
    (directory / "attempts").mkdir()
    return {"prepared": True}


def submit(value):
    site = profile(value["site"])
    directory = run_directory(site, value["run_id"])
    if not (directory / "request.json").is_file():
        raise JobError("Remote journal missing: submission-unknown; do not recreate or resubmit")
    request = read(directory / "request.json")
    if request["request_digest"] != value["request_digest"] or request["site"] != site:
        raise JobError("Submission does not match the prepared request")
    try:
        (directory / "submit.claim").mkdir()
    except FileExistsError:
        return submission_record(directory)
    record = {"state": "submission-unknown", "created": timestamp(),
              "request_digest": value["request_digest"], "owner": getpass.getuser(),
              "job_name": "nj-" + value["run_id"],
              "comment": "nj:" + value["run_id"] + ":" + value["request_digest"]}
    atomic(directory / "submission.json", record)
    command = ["env", f"PATH={site['path']}", site["python"], request["helper"],
               "_worker", str(directory)]
    script = "#!/bin/sh\nset -eu\numask 077\nexec " + shlex.join(command) + "\n"
    argv = ["sbatch", "--parsable", "--no-requeue", "--export=NONE",
            f"--job-name={record['job_name']}", f"--comment={record['comment']}",
            f"--output={directory}/slurm-%j.log", f"--error={directory}/slurm-%j.log",
            *[f"--{key}={value}" for key, value in sorted(site["resources"].items())]]
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
                    "--format=JobIDRaw,JobName%64,Comment%128,State,ExitCode,User%64"], timeout=30)
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
    directory = run_directory(profile(value["site"]), value["run_id"])
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


def doctor(value):
    site = profile(value["site"])
    tools = {}
    for argv in ([site["python"], "--version"], ["git", "--version"],
                 ["git-annex", "version", "--raw"], ["datalad", "--version"],
                 [site["apptainer"], "--version"], ["sbatch", "--version"]):
        tools[argv[0]] = run(argv)
    return {"tools": tools, "site_validated": False,
            "note": "Login checks only; allocation, MFA, storage and restore gates remain"}


def logs(value):
    directory = run_directory(profile(value["site"]), value["run_id"])
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


def container_command(site, inputs, outputs, argv, readonly=False):
    return [site["apptainer"], "exec", "--containall", "--cleanenv", "--no-home",
            "--bind", f"{inputs}/data:/inputs:ro", "--bind", f"{outputs}:/outputs:{'ro' if readonly else 'rw'}",
            "--pwd", "/outputs", str(inputs / "runtime.sif"), *argv]


def worker(directory):
    directory = Path(directory)
    request = read(directory / "request.json")
    site = profile(request["site"])
    job = os.environ.get("SLURM_JOB_ID", "")
    restart = os.environ.get("SLURM_RESTART_COUNT", "0")
    if not job.isdigit() or not restart.isdigit():
        raise JobError("Worker must run inside a Slurm allocation")
    attempt_id = f"{job}-{restart}-{uuid.uuid4().hex}"
    journal = directory / "attempts" / attempt_id
    journal.mkdir(mode=0o700)
    workspace = Path(site["work_root"]) / attempt_id
    workspace.mkdir(mode=0o700)
    state = {"attempt_id": attempt_id, "job_id": job, "restart_count": int(restart),
             "run_id": request["run_id"], "started": timestamp(), "state": "staging"}
    atomic(journal / "state.json", state)
    # --export=NONE does not remove Slurm's own variables. No client SSH socket
    # or credential variables are needed or forwarded by this worker.
    os.environ.pop("SSH_AUTH_SOCK", None)
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    os.environ["GIT_ALLOW_PROTOCOL"] = "file"
    os.environ["GIT_SSH_COMMAND"] = "/bin/false"
    os.environ["DATALAD_EXTENSIONS_LOAD"] = "next"
    log_path = journal / "worker.log"
    try:
        tools = doctor({"site": site})["tools"]
        if tools != request["tools"]:
            raise JobError("Worker tool versions differ from the staged toolchain")
        inputs = data.checkout(data.store_url(site["store_root"]), request["input_id"],
                               request["input_commit"], workspace / "input")
        manifest = read(inputs / "manifest.json")
        if digest(manifest) != request["request_digest"]:
            raise JobError("Input manifest does not match submission")
        verify(inputs, manifest["files"])
        # Annex's locked links point outside data/ into .git/annex. Materialize
        # these files before mounting data/ alone at /inputs in the container.
        git(inputs, "annex", "unlock", "--", "data", timeout=86400)
        for name in manifest["executables"]:
            (inputs / name).chmod(0o700)
        output = workspace / "output"
        data.create(output)
        atomic(output / "execution.json", {"run_id": request["run_id"],
               "attempt_id": attempt_id, "manifest": manifest, "site": site,
               "request_digest": request["request_digest"],
               "tools": tools})
        data.save(output, "Record pinned execution request")
        state["state"] = "running"
        atomic(journal / "state.json", state)
        with log_path.open("ab", buffering=0) as log:
            command = container_command(site, inputs, output, manifest["request"]["command"])
            result = subprocess.run(["datalad", "run", "-d", str(output), "-m",
                                     "Run pinned analysis", "--",
                                     *[arg.replace("{", "{{").replace("}", "}}") for arg in command]],
                                    stdout=log, stderr=log, check=False)
            state["analysis_exit_code"] = result.returncode
            atomic(journal / "state.json", state)
            if result.returncode:
                raise JobError("Analysis failed; see worker.log")
            for name in manifest["request"]["outputs"]:
                path = output / name
                if not path.is_file() or path.stat().st_size == 0:
                    raise JobError(f"Expected nonempty output missing: {name}")
            validation = subprocess.run(container_command(
                site, inputs, output, manifest["request"]["validate"], readonly=True),
                stdout=log, stderr=log, check=False)
            state["validation_exit_code"] = validation.returncode
            if validation.returncode:
                raise JobError("Scientific validation failed; see worker.log")
        state["state"] = "publishing"
        atomic(journal / "state.json", state)
        shutil.copyfile(log_path, output / "worker.log")
        atomic(output / "outcome.json", state)
        data.save(output, "Save validated outputs and execution evidence")
        receipt = data.verified_publication(output, site["store_root"], {
            "run_id": request["run_id"], "attempt_id": attempt_id,
            "request_digest": request["request_digest"], "published": timestamp()})
        atomic(journal / "receipt.json", receipt)
        state["state"] = "site-results-available"
        atomic(journal / "state.json", state)
        return 0
    except Exception as exc:
        state.update(state="inputs-missing" if state["state"] == "staging" else "failed",
                     error=str(exc))
        atomic(journal / "state.json", state)
        with log_path.open("ab") as log:
            log.write(canonical({"error": str(exc)}) + b"\n")
        return 1


OPERATIONS = {"prepare": prepare, "submit": submit, "status": status,
              "cancel": cancel, "doctor": doctor, "logs": logs}
