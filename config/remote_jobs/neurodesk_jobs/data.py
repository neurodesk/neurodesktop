"""Immutable execution capsules and independent RIA result datasets."""

from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import quote

from .core import JobError, atomic, checksum, git, inventory, run, verify


def identity(path):
    return git(path, "config", "--file", ".datalad/config", "datalad.dataset.id")


def create(path):
    run(["datalad", "create", "-c", "text2git", path])
    git(path, "config", "user.name", "Neurodesk jobs")
    git(path, "config", "user.email", "neurodesk-jobs@localhost")
    git(path, "annex", "untrust", "here")


def save(path, message):
    run(["datalad", "save", "-d", path, "-m", message], timeout=3600)


def store_url(root, host=None):
    return "ria+" + (f"ssh://{host}" if host else "file://") + quote(str(root))


def publish(path, url, env=None):
    # Repair partial sibling configuration after an interrupted first stage.
    run(["datalad", "create-sibling-ria", "-d", path, "-s", "site",
         "--new-store-ok", "--existing", "reconfigure", "--trust-level", "untrust", url], env=env)
    run(["datalad", "push", "-d", path, "--to", "site", "--data", "anything"],
        env=env, timeout=86400)


def checkout(url, dataset_id, commit, destination, env=None):
    """Use exactly one content source. No subdataset or URL fallback."""
    if (not re.fullmatch(r"[a-f0-9-]{36}", dataset_id)
            or not re.fullmatch(r"[a-f0-9]{40,64}", commit)):
        raise JobError("Invalid dataset identity or Git commit in receipt")
    destination = Path(destination)
    # Git cloning avoids DataLad's auto-enable of the original SSH special remote.
    # RIA Git repositories occupy <id[:3]>/<id[3:]>.
    base = url.removeprefix("ria+").rstrip("/")
    source = base + "/" + dataset_id[:3] + "/" + dataset_id[3:]
    run(["git", "clone", "--no-local", source, destination], env=env, timeout=86400)
    git(destination, "checkout", "--detach", commit, env=env)
    if identity(destination) != dataset_id:
        raise JobError("RIA dataset identity mismatch")
    git(destination, "annex", "init", "--no-autoenable", env=env)
    git(destination, "annex", "untrust", "here", env=env)
    # Define the reader here, rather than enabling inherited remote.log
    # configuration. Only this explicit ORA store may supply content.
    git(destination, "annex", "initremote", "read-store", "--sameas", "site-storage", "type=external",
        "externaltype=ora", "encryption=none", "autoenable=false",
        f"archive-id={dataset_id}", f"url={url}", f"push-url={url}", env=env)
    git(destination, "annex", "untrust", "read-store", env=env)
    git(destination, "annex", "copy", "--from", "read-store", "--", ".",
        env=env, timeout=86400)
    # Prevent accidental writes through either Git or annex. The original URL
    # stored in git-annex was never enabled; the reader has explicit URLs.
    git(destination, "config", "remote.read-store.annex-readonly", "true", env=env)
    git(destination, "config", "remote.read-store.annex-ignore", "true", env=env)
    for remote in git(destination, "remote", env=env).splitlines():
        git(destination, "config", f"remote.{remote}.pushurl", "/dev/null", env=env)
    return destination


def tracked_repositories(root):
    """Record every subdataset commit; require installed, clean source trees."""
    found = []

    def visit(path, prefix):
        if git(path, "status", "--porcelain", "--untracked-files=normal"):
            raise JobError(f"Save the input dataset before preparing a job: {path}")
        commit = git(path, "rev-parse", "HEAD")
        entry = {"path": prefix, "id": identity(path), "commit": commit}
        found.append(entry)
        tree = git(path, "ls-tree", "-rz", "HEAD")
        for item in tree.split("\0"):
            if not item:
                continue
            meta, name = item.split("\t", 1)
            mode, _, sha = meta.split()
            if mode == "160000":
                child = path / name
                if not (child / ".git").exists() or git(child, "rev-parse", "HEAD") != sha:
                    raise JobError(f"Install the pinned subdataset: {child}")
                visit(child, f"{prefix}/{name}".removeprefix("./"))

    visit(Path(root), ".")
    return found


def capsule(source, image, spec, destination):
    """Flatten selected bytes, retaining exact source repositories as bundles.

    Nested datasets are execution inputs, never runtime clone instructions.
    Consequently a worker cannot follow an upstream subdataset URL.
    """
    source, image, destination = Path(source).resolve(), Path(image).resolve(), Path(destination)
    repositories = tracked_repositories(source)
    run(["datalad", "get", "-d", source, "--", *spec["inputs"]], cwd=source, timeout=86400)
    create(destination)
    (destination / "data").mkdir()
    (destination / "sources").mkdir()
    executables = []
    for name in spec["inputs"]:
        path = source / name
        owner = max((r for r in repositories
                     if r["path"] == "." or name.startswith(r["path"] + "/")),
                    key=lambda r: len(r["path"]))
        repo = source / owner["path"]
        local = path.relative_to(repo).as_posix()
        mode = git(repo, "ls-tree", "HEAD", "--", local).split(" ", 1)[0]
        if mode not in {"100644", "100755", "120000"} or not path.is_file():
            raise JobError(f"Input must be a tracked regular or annexed file: {name}")
        if mode == "120000":
            key = git(repo, "annex", "lookupkey", "--", local)
            if not key or "/annex/objects/" not in str(path.resolve()):
                raise JobError(f"Ordinary symlinks are not execution inputs: {name}")
        target = destination / "data" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        executable = bool(path.stat().st_mode & 0o111)
        target.chmod(0o700 if executable else 0o600)
        if executable:
            executables.append("data/" + name)
    for index, entry in enumerate(repositories):
        entry["bundle"] = f"sources/{index}.bundle"
        git(source / entry["path"], "bundle", "create", destination / entry["bundle"], "HEAD")
    shutil.copyfile(image, destination / "runtime.sif")
    if tracked_repositories(source) != [
            {k: v for k, v in entry.items() if k != "bundle"} for entry in repositories]:
        raise JobError("Input revisions changed while preparing the snapshot")
    manifest = {"schema": 1, "request": spec, "sources": repositories,
                "executables": sorted(executables),
                "image_sha256": checksum(destination / "runtime.sif"),
                "files": inventory(destination, [
                    "runtime.sif", *[f"data/{p}" for p in spec["inputs"]],
                    *[r["bundle"] for r in repositories]])}
    atomic(destination / "manifest.json", manifest)
    save(destination, "Freeze execution inputs, software and source revisions")
    return manifest


def payload_files(dataset):
    return [p for p in git(dataset, "ls-files", "-z").split("\0")
            if p and not p.startswith(".datalad/") and p != ".gitattributes"]


def verified_publication(dataset, store, receipt):
    publish(dataset, store_url(store))
    receipt.update(dataset_id=identity(dataset), commit=git(dataset, "rev-parse", "HEAD"),
                   files=inventory(dataset, payload_files(dataset)))
    with tempfile.TemporaryDirectory(prefix="verify-", dir=Path(dataset).parent) as temp:
        restored = checkout(store_url(store), receipt["dataset_id"], receipt["commit"],
                            Path(temp) / "result")
        verify(restored, receipt["files"])
    return receipt


def archive_payload(dataset, target):
    """Export one Git bundle plus plain bytes, without annex symlinks."""
    target.mkdir()
    git(dataset, "bundle", "create", target / "history.bundle", "--all", "HEAD")
    files = payload_files(dataset)
    for name in files:
        path = target / "files" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(dataset) / name, path)
    return {"id": identity(dataset), "commit": git(dataset, "rev-parse", "HEAD"),
            "files": inventory(target / "files", files),
            "bundle_sha256": checksum(target / "history.bundle")}


def restore_payload(source, destination, record):
    if checksum(source / "history.bundle") != record["bundle_sha256"]:
        raise JobError("Archive Git bundle checksum mismatch")
    verify(source / "files", record["files"])
    run(["git", "clone", source / "history.bundle", destination])
    git(destination, "checkout", "--detach", record["commit"])
    git(destination, "annex", "init", "--no-autoenable")
    git(destination, "annex", "untrust", "here")
    if identity(destination) != record["id"]:
        raise JobError("Archive dataset identity mismatch")
    for name in record["files"]:
        target = destination / name
        if target.is_symlink():
            # reinject verifies the annex key before consuming the exported file.
            git(destination, "annex", "reinject", source / "files" / name, name)
    verify(destination, record["files"])
    git(destination, "remote", "remove", "origin")
    return destination
