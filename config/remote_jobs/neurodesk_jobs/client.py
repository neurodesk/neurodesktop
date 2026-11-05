"""Client-owned lifecycle, verified collection and self-contained archives."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tarfile
import tempfile
import uuid

from . import data
from .core import (JobError, atomic, checksum, digest, git, identifier, profile,
                   read, relative, request, timestamp, verify)
from .transport import SSH


@contextmanager
def client_lock(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "client.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


class Client:
    def __init__(self, site, state):
        self.site = profile(site)
        self.state = Path(state).expanduser().resolve()
        self.transport = SSH(self.site, self.state)

    def directory(self, run_id):
        return self.state / "runs" / identifier(run_id)

    def record(self, run_id):
        record = read(self.directory(run_id) / "run.json")
        if record["site"] != self.site:
            raise JobError("Use the original site profile for this run")
        return record

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
            raise JobError("A submission was attempted; staging cannot recreate its journal")
        record["helper"] = self.transport.install()
        record["tools"] = self.transport.rpc(record["helper"], "doctor", {"site": self.site})["tools"]
        record["state"] = "staging"
        atomic(self.directory(run_id) / "run.json", record)
        remote = {k: record[k] for k in ("run_id", "site", "request_digest", "input_id",
                                        "input_commit", "helper", "tools")}
        self.transport.rpc(record["helper"], "prepare", remote)
        # The transfer alias must expose the same filesystem as the command host.
        probe = str(Path(self.site["store_root"]) / (".probe-" + run_id))
        self.transport.remote([self.site["python"], "-c",
                               "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])",
                               probe, record["request_digest"]])
        self.transport.remote([self.site["python"], "-c",
                               "import pathlib,sys; assert pathlib.Path(sys.argv[1]).read_text()==sys.argv[2]",
                               probe, record["request_digest"]], host=self.site["transfer_host"])
        data.publish(self.directory(run_id) / "input",
                     data.store_url(self.site["store_root"], self.site["transfer_host"]),
                     self.transport.env)
        record["state"] = "staged"
        atomic(self.directory(run_id) / "run.json", record)
        return {"run_id": run_id, "state": "staged", "worker_verification": "pending"}

    def submit(self, run_id):
        record = self.record(run_id)
        if record["state"] not in {"staged", "submission-unknown", "accepted"}:
            raise JobError("Stage the run before submitting")
        # Persist client intent before sending anything. A lost RPC response
        # cannot return this run to the staged state.
        record["state"] = "submission-unknown"
        atomic(self.directory(run_id) / "run.json", record)
        reply = self.transport.rpc(record["helper"], "submit", {
            "site": self.site, "run_id": run_id, "request_digest": record["request_digest"]})
        record.update(state=reply["state"], submission=reply)
        atomic(self.directory(run_id) / "run.json", record)
        return reply

    def status(self, run_id):
        record = self.record(run_id)
        if "helper" not in record:
            return {"run_id": run_id, "state": record["state"], "attempts": []}
        observed = self.transport.rpc(record["helper"], "status", {
            "site": self.site, "run_id": run_id})
        observed.update(run_id=run_id, archive_state="archive-pending",
                        retention_days=self.site["scratch_retention_days"],
                        note="Jobs publish while the desktop is off; collection and archiving require reconnecting")
        for key in ("collection", "archive"):
            file = self.directory(run_id) / f"{key}.json"
            if file.exists():
                observed[key] = read(file)
        if "archive" in observed:
            observed["archive_state"] = "archive-verified"
        atomic(self.directory(run_id) / "observed.json", observed)
        return observed

    def cancel(self, run_id):
        record = self.record(run_id)
        return self.transport.rpc(record["helper"], "cancel", {"site": self.site, "run_id": run_id})

    def logs(self, run_id, attempt=None):
        record = self.record(run_id)
        return self.transport.rpc(record["helper"], "logs", {
            "site": self.site, "run_id": run_id, "attempt": attempt})

    def collect(self, run_id):
        record = self.record(run_id)
        directory = self.directory(run_id)
        observed = self.status(run_id)
        scheduler = observed.get("scheduler", {})
        if scheduler.get("state") != "COMPLETED" or scheduler.get("exit_code") != "0:0":
            raise JobError("Collection requires confirmed Slurm COMPLETED with exit 0:0")
        eligible = [attempt for attempt in observed["attempts"]
                    if attempt["state"] == "site-results-available"
                    and attempt["job_id"] == scheduler["job_id"]
                    and attempt.get("analysis_exit_code") == 0
                    and attempt.get("validation_exit_code") == 0
                    and "receipt" in attempt]
        if not eligible:
            raise JobError("No successfully validated and published attempt")
        eligible.sort(key=lambda item: (item["receipt"]["published"], item["attempt_id"]))
        existing = read(directory / "collection.json") if (directory / "collection.json").exists() else None
        selected = next((item for item in eligible if existing and
                         item["attempt_id"] == existing["attempt_id"]), eligible[0])
        receipt = selected["receipt"]
        if (receipt["request_digest"] != record["request_digest"]
                or receipt["run_id"] != run_id or receipt["attempt_id"] != selected["attempt_id"]):
            raise JobError("Publication receipt does not match the requested run")
        url = data.store_url(self.site["store_root"], self.site["transfer_host"])
        if existing:
            if existing["attempt_id"] != selected["attempt_id"]:
                raise JobError("Canonical result is no longer eligible; reconcile explicitly")
            verify(directory / "collected" / "output", receipt["files"])
            verify(directory / "collected" / "input", record["manifest"]["files"])
            return existing
        with tempfile.TemporaryDirectory(prefix="collect-", dir=directory) as temp:
            root = Path(temp)
            output = data.checkout(url, receipt["dataset_id"], receipt["commit"],
                                   root / "output", self.transport.env)
            verify(output, receipt["files"])
            inputs = data.checkout(url, record["input_id"], record["input_commit"],
                                   root / "input", self.transport.env)
            if digest(read(inputs / "manifest.json")) != record["request_digest"]:
                raise JobError("Retrieved input manifest changed")
            verify(inputs, record["manifest"]["files"])
            # The target is never overwritten. Interrupted collection is kept
            # for inspection; a user can move it away before repeating collection.
            if (directory / "collected").exists():
                raise JobError("Unjournaled collected directory exists; inspect and move it before retrying")
            os.rename(root, directory / "collected")
        result = {**receipt, "retrieved": timestamp(), "path": str(directory / "collected")}
        atomic(directory / "collection.json", result)
        return result

    def archive(self, run_id):
        directory = self.directory(run_id)
        # Verified collection already includes the scheduler's success evidence.
        # Archiving it must remain possible after scratch/accounting expires.
        collection = (read(directory / "collection.json") if (directory / "collection.json").exists()
                      else self.collect(run_id))
        verify(directory / "collected" / "output", collection["files"])
        verify(directory / "collected" / "input", self.record(run_id)["manifest"]["files"])
        destination = Path(self.site["archive_root"])
        if not destination.is_dir():
            raise JobError("Configured persistent archive destination is not mounted/available")
        target = destination / f"neurodesk-{run_id}-{collection['attempt_id']}.tar.gz"
        if (directory / "archive.json").exists():
            receipt = read(directory / "archive.json")
            if not target.is_file() or checksum(target) != receipt["sha256"]:
                raise JobError("Previously verified archive is missing or changed")
            return receipt
        if target.exists():
            raise JobError("Unjournaled archive exists; restore and reconcile it before retrying")
        with tempfile.TemporaryDirectory(prefix="archive-", dir=directory) as temp:
            root = Path(temp)
            metadata = {"schema": 1, "run": self.record(run_id), "collection": collection,
                        "observed": read(directory / "observed.json"), "datasets": {}}
            for name in ("input", "output"):
                metadata["datasets"][name] = data.archive_payload(directory / "collected" / name,
                                                                 root / name)
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
                        import shutil
                        shutil.copyfileobj(source, stream)
        metadata = read(exported / "archive.json")
        restored = root / "datasets"
        restored.mkdir()
        for name in ("input", "output"):
            data.restore_payload(exported / name, restored / name, metadata["datasets"][name])
        atomic(restored / "archive.json", metadata)
        os.rename(restored, destination)
    return {"path": str(destination), "run_id": metadata["run"]["run_id"]}
