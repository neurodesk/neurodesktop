"""Desktop-side lifecycle: snapshot, stage, submit, collect and archive."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import uuid

from . import data
from .core import (CONNECT_HINT, JobError, atomic, checksum, digest, git, identifier,
                   read, relative, request, site as checked_site, timestamp, verify)
from .remote import staged_objects
from .transport import SSH


@contextmanager
def client_lock(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "client.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def connect(state, alias=None, data_root=None):
    """Log in once (with MFA if the site asks) and save what the cluster provides."""
    file = Path(state) / "site.json"
    saved = read(file) if file.exists() else None
    alias = alias or (saved or {}).get("host")
    if not alias:
        raise JobError("Name the cluster to connect to, for example: neurodesk jobs connect "
                       "bunya (an alias from ~/.ssh/config)")
    transport = SSH(alias)
    transport.connect()
    try:
        site = checked_site(saved, str(file)) if saved["host"] == alias and not data_root else None
    except (JobError, KeyError, TypeError):
        site = None
    if not site:
        found = transport.discover()
        if not data_root and not found["scratch"]:
            raise JobError(f"No writable scratch directory found on {alias}. Choose one: "
                           f"neurodesk jobs connect {alias} --data-root /ABSOLUTE/CLUSTER/PATH")
        site = checked_site({
            "host": alias, "python": found["python"], "path": found["path"],
            "state_root": found["home"] + "/.neurodesk-jobs",
            "data_root": data_root or found["scratch"] + "/neurodesk-jobs"})
        atomic(file, site)
    helper = transport.install(site)
    return {"state": "connected", "site": site, "site_file": str(file),
            **transport.rpc(site, helper, "doctor", {"site": site})}


def saved_site(state):
    file = Path(state) / "site.json"
    if not file.exists():
        raise JobError(f"No cluster connected yet; {CONNECT_HINT}")
    return checked_site(read(file), str(file))


def run_site(state, run_id):
    """A run keeps the site it was prepared for, even after connecting elsewhere."""
    file = Path(state) / "runs" / identifier(run_id) / "run.json"
    if not file.exists():
        raise JobError(f"Unknown run {run_id} in {state}")
    return checked_site(read(file)["site"], str(file))


class Client:
    def __init__(self, site, state):
        self.site = checked_site(site)
        self.state = Path(state).expanduser().resolve()
        self.transport = SSH(self.site["host"])

    def directory(self, run_id):
        return self.state / "runs" / identifier(run_id)

    def record(self, run_id):
        return read(self.directory(run_id) / "run.json")

    def rpc(self, record, operation, value, timeout=300):
        return self.transport.rpc(self.site, record["helper"], operation, value, timeout)

    def prepare(self, source, image, specification):
        spec = request(specification)
        run_id = uuid.uuid4().hex
        directory = self.directory(run_id)
        directory.mkdir(parents=True, mode=0o700)
        manifest = data.capsule(source, image, spec, directory / "input")
        record = {"run_id": run_id, "site": self.site, "state": "prepared",
                  "request_digest": digest(manifest),
                  "input_id": data.identity(directory / "input"),
                  "input_commit": git(directory / "input", "rev-parse", "HEAD"),
                  "manifest": manifest, "created": timestamp()}
        atomic(directory / "run.json", record)
        return record

    def stage(self, run_id):
        record = self.record(run_id)
        if record["state"] not in {"prepared", "staging", "staged"}:
            raise JobError(f"Run is already {record['state']}; it cannot be staged again")
        directory = self.directory(run_id)
        record["helper"] = self.transport.install(self.site)
        record["state"] = "staging"
        atomic(directory / "run.json", record)
        wanted = staged_objects(record["manifest"])
        absent = set(self.rpc(record, "missing", {
            "site": self.site,
            "objects": {entry["sha256"]: entry["size"] for entry in wanted.values()}})["missing"])
        for name, entry in sorted(wanted.items()):
            if entry["sha256"] in absent:
                self.transport.upload(self.site, record["helper"], directory / "input" / name,
                                      entry["sha256"], entry["size"])
                absent.discard(entry["sha256"])
        self.rpc(record, "prepare", {k: record[k] for k in (
            "run_id", "site", "request_digest", "manifest", "helper")})
        record["state"] = "staged"
        atomic(directory / "run.json", record)
        return {"run_id": run_id, "state": "staged"}

    def submit(self, run_id):
        record = self.record(run_id)
        if record["state"] in {"prepared", "staging"}:
            self.stage(run_id)
            record = self.record(run_id)
        if record["state"] not in {"staged", "submission-unknown", "accepted"}:
            raise JobError(f"Run is {record['state']}; it cannot be submitted")
        # Persist client intent before sending anything. A lost RPC response
        # cannot return this run to the staged state.
        record["state"] = "submission-unknown"
        atomic(self.directory(run_id) / "run.json", record)
        reply = self.rpc(record, "submit", {
            "site": self.site, "run_id": run_id, "request_digest": record["request_digest"]})
        record.update(state=reply["state"], submission=reply)
        atomic(self.directory(run_id) / "run.json", record)
        if reply["state"] != "accepted" and "error" in reply:
            raise JobError(f"Slurm did not confirm the submission: {reply['error']}\n"
                           "This run is never submitted twice. Check `neurodesk jobs status "
                           f"{run_id}`: if Slurm has no such job, prepare a new run.")
        return reply

    def status(self, run_id):
        record = self.record(run_id)
        if "helper" not in record:
            return {"run_id": run_id, "state": record["state"], "attempts": []}
        observed = self.rpc(record, "status", {"site": self.site, "run_id": run_id})
        observed.update(run_id=run_id, archive_state="archive-pending")
        for key in ("collection", "archive"):
            file = self.directory(run_id) / f"{key}.json"
            if file.exists():
                observed[key] = read(file)
        if "archive" in observed:
            observed["archive_state"] = "archive-verified"
        atomic(self.directory(run_id) / "observed.json", observed)
        return observed

    def cancel(self, run_id):
        return self.rpc(self.record(run_id), "cancel", {"site": self.site, "run_id": run_id})

    def logs(self, run_id, attempt=None):
        return self.rpc(self.record(run_id), "logs", {
            "site": self.site, "run_id": run_id, "attempt": attempt})

    def collect(self, run_id):
        record = self.record(run_id)
        directory = self.directory(run_id)
        observed = self.status(run_id)
        scheduler = observed.get("scheduler", {})
        if scheduler.get("state") != "COMPLETED" or scheduler.get("exit_code") != "0:0":
            raise JobError("Collection requires confirmed Slurm COMPLETED with exit 0:0; "
                           f"Slurm reports {scheduler.get('state', 'UNKNOWN')}")
        eligible = [attempt for attempt in observed["attempts"]
                    if attempt["state"] == "site-results-available"
                    and attempt["job_id"] == scheduler["job_id"]
                    and attempt.get("analysis_exit_code") == 0
                    and attempt.get("validation_exit_code") == 0
                    and "receipt" in attempt]
        if not eligible:
            raise JobError("No successfully validated and published attempt; see logs")
        eligible.sort(key=lambda item: (item["receipt"]["published"], item["attempt_id"]))
        existing = read(directory / "collection.json") if (directory / "collection.json").exists() else None
        selected = next((item for item in eligible if existing and
                         item["attempt_id"] == existing["attempt_id"]), eligible[0])
        receipt = selected["receipt"]
        if (receipt["request_digest"] != record["request_digest"]
                or receipt["run_id"] != run_id or receipt["attempt_id"] != selected["attempt_id"]):
            raise JobError("Publication receipt does not match the requested run")
        verify(directory / "input", record["manifest"]["files"])
        if existing:
            if existing["attempt_id"] != selected["attempt_id"]:
                raise JobError("Canonical result is no longer eligible; reconcile explicitly")
            verify(directory / "output", receipt["files"])
            return existing
        # The target is never overwritten. Only an interrupted final rename can
        # leave it behind; a user can move it away before repeating collection.
        if (directory / "output").exists():
            raise JobError("Unjournaled output directory exists; inspect and move it before retrying")
        with tempfile.TemporaryDirectory(prefix="collect-", dir=directory) as temp:
            files = Path(temp) / "files"
            for name in sorted(receipt["files"]):
                target = files / relative(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                self.transport.download(self.site, record["helper"], {
                    "site": self.site, "run_id": run_id, "attempt_id": receipt["attempt_id"],
                    "path": name}, target)
            verify(files, receipt["files"])
            output = Path(temp) / "output"
            data.create(output)
            for name in sorted(receipt["files"]):
                (output / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(files / name, output / name)
            data.save(output, f"Results of run {run_id}, attempt {receipt['attempt_id']}")
            uncommitted = set(receipt["files"]) - set(data.payload_files(output))
            if uncommitted:
                raise JobError("DataLad did not commit these results: "
                               + ", ".join(sorted(uncommitted)))
            verify(output, receipt["files"])
            os.rename(output, directory / "output")
        result = {**receipt, "retrieved": timestamp(), "path": str(directory / "output"),
                  "commit": git(directory / "output", "rev-parse", "HEAD")}
        atomic(directory / "collection.json", result)
        return result

    def archive(self, run_id, destination=None):
        directory = self.directory(run_id)
        # Verified collection already includes the scheduler's success evidence.
        # Archiving it must remain possible after scratch/accounting expires.
        collection = (read(directory / "collection.json") if (directory / "collection.json").exists()
                      else self.collect(run_id))
        record = self.record(run_id)
        verify(directory / "output", collection["files"])
        verify(directory / "input", record["manifest"]["files"])
        if (directory / "archive.json").exists():
            receipt = read(directory / "archive.json")
            if not Path(receipt["path"]).is_file() or checksum(receipt["path"]) != receipt["sha256"]:
                raise JobError("Previously verified archive is missing or changed")
            if destination and Path(destination).expanduser().resolve() != Path(receipt["path"]).parent.resolve():
                raise JobError(f"Run is already archived at {receipt['path']}")
            return receipt
        destination = Path(destination or self.state / "archives").expanduser()
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = destination / f"neurodesk-{run_id}-{collection['attempt_id']}.tar.gz"
        if target.exists():
            raise JobError("Unjournaled archive exists; restore and reconcile it before retrying")
        with tempfile.TemporaryDirectory(prefix="archive-", dir=directory) as temp:
            root = Path(temp)
            metadata = {"schema": 2, "run": record, "collection": collection,
                        "observed": read(directory / "observed.json"), "datasets": {}}
            for name in ("input", "output"):
                metadata["datasets"][name] = data.archive_payload(directory / name, root / name)
            if (set(collection["files"]) - set(metadata["datasets"]["output"]["files"])
                    or set(record["manifest"]["files"]) - set(metadata["datasets"]["input"]["files"])):
                raise JobError("Collected results or snapshot inputs are missing from the archive")
            atomic(root / "archive.json", metadata)
            fd, pending = tempfile.mkstemp(prefix=".neurodesk-", dir=destination)
            os.close(fd)
            try:
                with tarfile.open(pending, "w:gz") as archive:
                    for name in ("archive.json", "input", "output"):
                        archive.add(root / name, arcname=name)
                with open(pending, "rb") as stream:
                    os.fsync(stream.fileno())
                restore(pending, root / "restored")
                # Hard-link publication refuses to overwrite an existing archive.
                os.link(pending, target)
                fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                receipt = {"path": str(target), "sha256": checksum(target),
                           "verified": timestamp(), "attempt_id": collection["attempt_id"],
                           "verification": "restored Git repositories and all payload bytes"}
                atomic(directory / "archive.json", receipt)
            finally:
                Path(pending).unlink(missing_ok=True)
        return receipt


def restore(archive_path, destination):
    """Restore without a cluster or external repositories; reject tar links."""
    destination = Path(destination)
    if destination.exists():
        raise JobError("Restore destination must not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="restore-", dir=destination.parent) as temp:
        root = Path(temp)
        exported = root / "export"
        exported.mkdir()
        with tarfile.open(archive_path, "r:gz") as archive:
            names = set()
            for member in archive:
                relative(member.name)
                if member.name in names or not (member.isdir() or member.isfile()):
                    raise JobError("Archive contains duplicate paths or non-regular files")
                names.add(member.name)
                target = exported / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as stream, archive.extractfile(member) as source:
                        shutil.copyfileobj(source, stream)
        metadata = read(exported / "archive.json")
        restored = root / "datasets"
        restored.mkdir()
        for name in ("input", "output"):
            data.restore_payload(exported / name, restored / name, metadata["datasets"][name])
        atomic(restored / "archive.json", metadata)
        os.rename(restored, destination)
    return {"path": str(destination), "run_id": metadata["run"]["run_id"]}
