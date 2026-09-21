#!/usr/bin/env python3
"""Assemble review materials from the current checkout without publishing them."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def release_image(value):
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9-]*/neurodesktop-kasm:1\.19\.0-[0-9]{8}\.[1-9][0-9]*", value):
        raise argparse.ArgumentTypeError("Use ghcr.io/OWNER/neurodesktop-kasm:1.19.0-YYYYMMDD.N")
    return value


def pinned_base(value):
    if not re.fullmatch(r"ghcr\.io/neurodesk/neurodesktop@sha256:[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError("Pin the public Neurodesktop base as ghcr.io/neurodesk/neurodesktop@sha256:DIGEST")
    return value


def positive_size(value):
    size = int(value)
    if size <= 0:
        raise argparse.ArgumentTypeError("Uncompressed image size must be positive, in decimal MB")
    return size


def assemble(output, image, base, size):
    # Refuse an existing output directory rather than mixing releases or deleting it.
    output.mkdir(parents=True, exist_ok=False)
    publishing = ROOT / "config/kasm/publishing"
    workspace = output / "workspaces/Neurodesktop"
    workspace.mkdir(parents=True)
    metadata = json.loads((publishing / "workspace.json").read_text())
    metadata["compatibility"] = [{
        "version": "1.19.x", "image": image,
        "uncompressed_size_mb": size,
        "available_tags": [image.rsplit(":", 1)[1]],
    }]
    (workspace / "workspace.json").write_text(json.dumps(metadata, indent=2) + "\n")
    shutil.copy2(publishing / "neurodesktop.svg", workspace)
    shutil.copytree(publishing / "screenshots", output / "screenshots")
    shutil.copy2(publishing / "submission.md", output)
    source = output / "source"
    paths = [
        "LICENSE", "scripts/verify_kasm_image.sh", "tests/testlib.py",
        "tests/conftest.py", "docs/architecture/kasm.md",
        "docs/architecture/kasm-publishing.md",
        "scripts/prepare_kasm_submission.py", ".github/workflows/release-kasm.yml",
        ".trivyignore.yaml", "tests/unit/test_kasm_submission.py",
        "tests/unit/test_kasm_workspace.py", "tests/unit/test_kasm_release_workflow.py",
    ]
    paths += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "config/kasm").glob("*")) if p.is_file()]
    paths += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "tests/container").glob("test_kasm_*.py"))]
    for name in paths:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    shutil.copytree(publishing, source / "config/kasm/publishing")
    for action in ("docker-login-retry", "check-registry-manifest"):
        shutil.copytree(ROOT / ".github/actions" / action, source / ".github/actions" / action)
    build = f"docker build --platform linux/amd64 --build-arg NEURODESKTOP_IMAGE='{base}' -f config/kasm/Dockerfile -t '{image}' .\n"
    (source / "build.sh").write_text("#!/bin/sh\nset -eu\ncd \"$(dirname \"$0\")\"\n" + build)
    (source / "build.sh").chmod(0o755)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output([
        "git", "status", "--porcelain", "--", "config/kasm", "scripts/prepare_kasm_submission.py",
        "scripts/verify_kasm_image.sh", "tests", "docs/architecture/kasm.md",
        "docs/architecture/kasm-publishing.md", ".github/workflows/release-kasm.yml",
    ], cwd=ROOT, text=True).strip())
    provenance = {
        "image": image, "base_image": base, "source_revision": revision,
        "source_has_uncommitted_changes": dirty,
        "architecture": "amd64", "status": "unpublished-review-candidate",
        "validation": "See attached build/test/scan logs. Generating this bundle does not validate an image.",
        "kasm_platform_validation": "pending", "official_store_acceptance": "pending",
    }
    (output / "release.json").write_text(json.dumps(provenance, indent=2) + "\n")
    files = sorted(p for p in output.rglob("*") if p.is_file())
    (output / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(output)}\n" for p in files
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=release_image)
    parser.add_argument("--base-image", required=True, type=pinned_base)
    parser.add_argument("--uncompressed-size-mb", required=True, type=positive_size)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    assemble(args.output, args.image, args.base_image, args.uncompressed_size_mb)


if __name__ == "__main__":
    main()
