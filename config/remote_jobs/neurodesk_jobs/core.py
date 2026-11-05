"""Persistent records and the validated pilot request format."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import time


class JobError(Exception):
    """An operation failed without granting permission to repeat submission."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def checksum(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def atomic(path, value):
    """Publish a complete record; flush data and directory before returning."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def run(argv, cwd=None, env=None, timeout=300, input=None):
    env = dict(os.environ if env is None else env)
    # Load the optional next extension before core RIA imports. Late discovery
    # mixes old and patched IO classes in DataLad 1.8 when next is discovered late.
    env["DATALAD_EXTENSIONS_LOAD"] = "next"
    for role in ("AUTHOR", "COMMITTER"):
        env.setdefault(f"GIT_{role}_NAME", "Neurodesk jobs")
        env.setdefault(f"GIT_{role}_EMAIL", "neurodesk-jobs@localhost")
    try:
        result = subprocess.run(
            [str(arg) for arg in argv], cwd=cwd, env=env, input=input,
            capture_output=True, timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JobError(f"{argv[0]} unavailable or timed out") from exc
    if result.returncode:
        raise JobError(f"{argv[0]} failed ({result.returncode}): "
                       + (result.stderr + result.stdout).decode(errors="replace")[-3000:])
    return result.stdout.decode().strip()


def git(path, *args, **kwargs):
    return run(["git", "-C", path, *args], **kwargs)


def relative(value):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise JobError("Expected a relative data file path")
    path = PurePosixPath(value)
    if (not isinstance(value, str) or not value or path.is_absolute()
            or str(path) != value or any(part in {"..", ".git", ".datalad"}
                                       for part in path.parts)):
        raise JobError(f"Expected a relative data file path: {value!r}")
    return value


def identifier(value):
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise JobError("Invalid run ID")
    return value


def profile(value):
    required = {"name", "host", "transfer_host", "state_root", "store_root",
                "work_root", "archive_root", "path", "python", "apptainer",
                "resources", "scratch_retention_days"}
    if not isinstance(value, dict) or set(value) != required:
        raise JobError(f"Profile fields must be: {', '.join(sorted(required))}")
    for key in ("name", "host", "transfer_host"):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value[key]):
            raise JobError(f"{key} must be a name/SSH alias, with no user or options")
    for key in ("state_root", "store_root", "work_root", "archive_root",
                "python", "apptainer"):
        if (not isinstance(value[key], str) or not value[key].startswith("/")
                or ".." in Path(value[key]).parts or "REPLACE" in value[key]
                or any(c in value[key] for c in "\n\r\0:,")):
            raise JobError(f"Configure an absolute {key}; templates are not site support")
    roots = [Path(value[k]) for k in ("state_root", "store_root", "work_root")]
    if any(a == b or a in b.parents or b in a.parents
           for i, a in enumerate(roots) for b in roots[i + 1:]):
        raise JobError("Site state, store, and work roots must be separate directories")
    if not isinstance(value["path"], str) or not all(
            part.startswith("/") and "REPLACE" not in part
            for part in value["path"].split(":")):
        raise JobError("path must explicitly locate the cluster tools")
    allowed = {"account", "partition", "time", "mem", "cpus-per-task", "gres", "qos"}
    if (not value["resources"] or set(value["resources"]) - allowed
            or not {"time", "mem", "cpus-per-task"} <= set(value["resources"])):
        raise JobError("Specify time, mem, cpus-per-task and only supported resource flags")
    for option in value["resources"].values():
        if not re.fullmatch(r"[a-zA-Z0-9_:.,+-]+", str(option)):
            raise JobError("Invalid Slurm resource value")
    if type(value["scratch_retention_days"]) is not int or value["scratch_retention_days"] < 1:
        raise JobError("Set the site's actual scratch retention window")
    return value


def request(value):
    if not isinstance(value, dict) or set(value) != {"inputs", "command", "validate", "outputs"}:
        raise JobError("Request needs inputs, command, validate, and outputs")
    for key in ("inputs", "outputs"):
        if not isinstance(value[key], list) or not value[key]:
            raise JobError(f"{key} must list exact required files")
        for item in value[key]:
            relative(item)
        if len(set(value[key])) != len(value[key]):
            raise JobError(f"Duplicate {key}")
    for key in ("command", "validate"):
        if (not isinstance(value[key], list) or not value[key]
                or any(not isinstance(x, str) or "\0" in x for x in value[key])):
            raise JobError(f"{key} must be a nonempty argument vector")
    if set(value["outputs"]) & {"execution.json", "outcome.json", "worker.log", ".gitattributes", ".gitmodules"}:
        raise JobError("Output name is reserved for job provenance")
    return value


def inventory(root, paths):
    return {str(path): {"sha256": checksum(Path(root) / path),
                        "size": (Path(root) / path).stat().st_size}
            for path in sorted(paths)}


def verify(root, files):
    for name, expected in files.items():
        relative(name)
        path = Path(root) / name
        if not path.is_file() or path.stat().st_size != expected["size"]:
            raise JobError(f"Missing or incomplete content: {name}")
        if checksum(path) != expected["sha256"]:
            raise JobError(f"Content checksum mismatch: {name}")


def timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
