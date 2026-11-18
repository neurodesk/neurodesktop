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


SITE_FIELDS = ("host", "python", "path", "state_root", "data_root")
DEFAULT_RESOURCES = {"time": "01:00:00", "mem": "4G", "cpus-per-task": 1}
SLURM_OPTIONS = {"account", "partition", "time", "mem", "cpus-per-task", "gres", "qos"}
CONNECT_HINT = "run: neurodesk jobs connect HOST (an alias from ~/.ssh/config)"


def host(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value):
        raise JobError(f"SSH host {value!r} must be an alias from ~/.ssh/config "
                       "(letters, digits, '.', '_' or '-'; no user@ or options), "
                       "for example: neurodesk jobs connect bunya")
    return value


def site(value, source="site configuration"):
    """Validate the site record that `connect` discovers and saves."""
    if not isinstance(value, dict):
        raise JobError(f"{source} is not a JSON object; {CONNECT_HINT}")
    problems = [f"missing {key!r}" for key in SITE_FIELDS if key not in value]
    problems += [f"unknown field {key!r}" for key in sorted(set(value) - set(SITE_FIELDS))]
    if problems:
        raise JobError(f"{source}: {'; '.join(problems)}. Re-run connect to rediscover the "
                       f"site: neurodesk jobs connect {value.get('host', 'HOST')}")
    host(value["host"])
    for key in ("python", "state_root", "data_root"):
        path = value[key]
        if (not isinstance(path, str) or not path.startswith("/") or ".." in Path(path).parts
                or any(c in path for c in "\n\r\0:")):
            raise JobError(f"{source}: {key}={path!r} must be an absolute cluster path")
    if Path(value["state_root"]) == Path(value["data_root"]):
        raise JobError(f"{source}: state_root and data_root must be different directories")
    if not isinstance(value["path"], str) or "\0" in value["path"]:
        raise JobError(f"{source}: path must be the cluster's PATH string")
    return value


def resources(value):
    """Merge a request's optional Slurm options with the defaults."""
    merged = dict(DEFAULT_RESOURCES)
    merged.update(value or {})
    return merged


def request(value):
    required = {"inputs", "command", "validate", "outputs"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"slurm"}:
        raise JobError("Request needs inputs, command, validate and outputs, "
                       "plus an optional slurm section")
    slurm = value.get("slurm", {})
    if not isinstance(slurm, dict) or set(slurm) - SLURM_OPTIONS:
        raise JobError("slurm may only set: " + ", ".join(sorted(SLURM_OPTIONS)))
    for option, setting in slurm.items():
        if not re.fullmatch(r"[a-zA-Z0-9_:.,+-]+", str(setting)):
            raise JobError(f"Invalid slurm {option} value: {setting!r}")
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
