"""Check the actual submission bundle and its release input boundary."""

import argparse
import hashlib
import json
import subprocess

import pytest

from testlib import load_source_module, repo_path


release = load_source_module("kasm_submission", "/unused", "scripts/prepare_kasm_submission.py")
IMAGE = "ghcr.io/neurodesk/neurodesktop-kasm:1.19.0-20260919.1"
BASE = "ghcr.io/neurodesk/neurodesktop@sha256:" + "a" * 64


@pytest.mark.parametrize("image", ["neurodesktop:local", "ghcr.io/neurodesk/neurodesktop-kasm:latest", IMAGE + ";echo bad", IMAGE + "\n"])
def test_rejects_mutable_or_unsafe_release_names(image):
    with pytest.raises(argparse.ArgumentTypeError):
        release.release_image(image)


@pytest.mark.parametrize("base", ["neurodesktop:package-upgrade", "ghcr.io/neurodesk/neurodesktop:amd64", BASE + "\n"])
def test_requires_public_pinned_base(base):
    with pytest.raises(argparse.ArgumentTypeError):
        release.pinned_base(base)


def test_bundle_is_self_contained_and_does_not_claim_publication(tmp_path):
    output = tmp_path / "submission"
    result = subprocess.run([
        "python3", str(repo_path("scripts/prepare_kasm_submission.py")),
        "--image", IMAGE, "--base-image", BASE,
        "--uncompressed-size-mb", "11330", "--output", str(output),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    metadata = json.loads((output / "workspaces/Neurodesktop/workspace.json").read_text())
    assert metadata["compatibility"][0]["image"] == IMAGE
    assert metadata["run_config"]["privileged"] is True
    assert "VNC_PW" not in metadata["run_config"]["environment"]
    assert (output / "workspaces/Neurodesktop" / metadata["image_src"]).is_file()
    assert json.loads((output / "release.json").read_text())["status"] == "unpublished-review-candidate"
    for line in (output / "SHA256SUMS").read_text().splitlines():
        checksum, name = line.split("  ", 1)
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == checksum
    for name in ["config/kasm/Dockerfile", "tests/container/test_kasm_science.py", "tests/testlib.py", "scripts/verify_kasm_image.sh"]:
        assert (output / "source" / name).read_bytes() == repo_path(name).read_bytes()
    assert BASE in (output / "source/build.sh").read_text()
    with pytest.raises(FileExistsError):
        release.assemble(output, IMAGE, BASE, 11330)


@pytest.mark.parametrize("size", ["0", "-1"])
def test_rejects_missing_image_size(size):
    with pytest.raises(argparse.ArgumentTypeError):
        release.positive_size(size)


def test_dirty_metadata_covers_copied_license(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    release.assemble(seed, IMAGE, BASE, 11330)
    checkout = seed / "source"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "Fixture",
    ], cwd=checkout, check=True)
    monkeypatch.setattr(release, "ROOT", checkout)
    clean = tmp_path / "clean"
    release.assemble(clean, IMAGE, BASE, 11330)
    assert not json.loads((clean / "release.json").read_text())["source_has_uncommitted_changes"]
    with (checkout / "LICENSE").open("a") as license_file:
        license_file.write("\nFixture edit\n")
    dirty = tmp_path / "dirty"
    release.assemble(dirty, IMAGE, BASE, 11330)
    assert json.loads((dirty / "release.json").read_text())["source_has_uncommitted_changes"]
