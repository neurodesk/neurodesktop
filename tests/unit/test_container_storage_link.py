"""Keep Neurocommand's bundled modules and existing container stores intact."""

import os
import shlex
import subprocess

import pytest

from testlib import repo_path


@pytest.mark.parametrize("layout", ["missing", "directory", "bundles", "legacy", "link", "dangling", "file"])
def test_startup_preserves_existing_neurocommand_container_paths(tmp_path, layout):
    store = tmp_path / "shared store/containers"
    store.mkdir(parents=True)
    destination = tmp_path / "neurocommand/local/containers"
    destination.parent.mkdir(parents=True)
    if layout in {"directory", "bundles", "legacy"}:
        destination.mkdir()
    if layout in {"bundles", "legacy"}:
        modules = destination / "modules"
        modules.mkdir()
        (modules / "bundle.lua").write_text("user or bundled module\n")
    if layout == "legacy":
        (destination / "containers").symlink_to(store)
    if layout in {"link", "dangling"}:
        destination.symlink_to(store if layout == "link" else tmp_path / "absent")
    if layout == "file":
        destination.write_text("user file\n")

    source = repo_path("config/jupyter/jupyterlab_startup.sh").read_text()
    setup = source[source.index("# Create a symlink to the neurodesktop-storage directory"):
                   source.index("# ensure overlay directory exists")]
    setup = setup.replace('"/neurocommand/local/containers"', shlex.quote(str(destination)))
    before = destination.lstat() if layout != "missing" else None
    for _ in range(2):
        result = subprocess.run(
            ["bash", "-c", setup], capture_output=True, text=True, timeout=10,
            env={**os.environ, "NEURODESKTOP_LOCAL_CONTAINERS": str(store)},
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
    if layout == "missing":
        assert destination.is_symlink()
        assert destination.readlink() == store
    else:
        after = destination.lstat()
        assert (after.st_ino, after.st_mode) == (before.st_ino, before.st_mode)
        if layout in {"link", "dangling"}:
            assert destination.readlink() == (store if layout == "link" else tmp_path / "absent")
    if layout == "directory":
        assert list(destination.iterdir()) == []
    if layout in {"bundles", "legacy"}:
        assert (destination / "modules/bundle.lua").read_text() == "user or bundled module\n"
        assert sorted(path.name for path in destination.iterdir()) == (
            ["containers", "modules"] if layout == "legacy" else ["modules"]
        )
    if layout == "file":
        assert destination.read_text() == "user file\n"


@pytest.mark.parametrize("bundled_modules", [False, True])
def test_image_link_setup_preserves_neurocommand_bundles(tmp_path, bundled_modules):
    store = tmp_path / "store/containers"
    destination = tmp_path / "neurocommand/local/containers"
    destination.parent.mkdir(parents=True)
    if bundled_modules:
        (destination / "modules").mkdir(parents=True)
        (destination / "modules/bundle.lua").write_text("image module\n")
    dockerfile = repo_path("Dockerfile").read_text()
    install = dockerfile.split("# Install neurocommand\n", 1)[1].split("# Install AI coding assistants", 1)[0]
    setup = install.split("&& bash install.sh \\\n", 1)[1].strip().removeprefix("&& ")
    setup = setup.replace("/neurodesktop-storage/containers", shlex.quote(str(store)))
    setup = setup.replace("/neurocommand/local/containers", shlex.quote(str(destination)))

    result = subprocess.run(["bash", "-c", setup], capture_output=True, text=True, timeout=10)

    assert result.returncode == 0, result.stderr
    if bundled_modules:
        assert not destination.is_symlink()
        assert sorted(path.name for path in destination.iterdir()) == ["modules"]
        assert (destination / "modules/bundle.lua").read_text() == "image module\n"
    else:
        assert destination.is_symlink()
        assert destination.readlink() == store
