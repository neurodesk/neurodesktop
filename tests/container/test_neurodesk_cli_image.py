"""The installed `neurodesk` command reaches every helper under its new name."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

DISPATCHED = {
    ("cite",): "neurodesk-cite",
    ("kernel",): "neurodesk-kernel",
    ("uv",): "neurodesk-uv",
    ("jobs",): "neurodesk-jobs",
    ("update",): "neurodesk-update",
    ("t3",): "t3_neurodesk_setup",
    ("agent", "preflight"): "neurodesk-agent-preflight",
    ("astra",): "neurodesk-astra-provenance",
    ("lightcone", "materialize"): "neurodesk-lc-materialize",
}


def neurodesk(*args, timeout=120):
    return subprocess.run(["neurodesk", *args], capture_output=True, text=True,
                          timeout=timeout, check=False)


def test_dispatcher_is_root_owned_and_on_path():
    path = Path(shutil.which("neurodesk"))
    assert path == Path("/usr/local/bin/neurodesk")
    assert path.stat().st_uid == 0
    assert path.stat().st_mode & 0o022 == 0
    result = neurodesk("--help")
    assert result.returncode == 0, result.stderr
    for words in DISPATCHED:
        assert f"  {' '.join(words)} " in result.stdout


@pytest.mark.parametrize("words", DISPATCHED, ids=" ".join)
def test_command_help_names_the_new_command(words):
    result = neurodesk(*words, "--help")
    assert result.returncode == 0, result.stderr
    assert result.stdout.lower().startswith(f"usage: neurodesk {' '.join(words)}"), result.stdout


@pytest.mark.parametrize("name", sorted(set(DISPATCHED.values())))
def test_previous_command_names_still_run(name):
    assert os.access(shutil.which(name) or "", os.X_OK), name


@pytest.mark.parametrize("name", ["check", "test", "info"])
def test_builtin_command_help(name):
    result = neurodesk(name, "--help")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: neurodesk {name}")


def test_cvmfs_check_matches_the_startup_mode():
    result = neurodesk("check", "cvmfs", timeout=300)
    if os.environ.get("CVMFS_DISABLE", "false").lower() in {"true", "1"}:
        assert result.stdout.startswith("[SKIP] cvmfs"), result.stdout
    else:
        assert result.returncode == 0, result.stdout
        assert result.stdout.startswith("[PASS] cvmfs"), result.stdout


def test_info_reports_the_image_version():
    result = neurodesk("info", timeout=120)
    assert result.returncode == 0, result.stderr
    assert f"Neurodesktop: {os.environ['NEURODESKTOP_VERSION']}" in result.stdout
