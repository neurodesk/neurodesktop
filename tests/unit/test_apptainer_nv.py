"""Regression coverage for automatic Apptainer NVIDIA library binding."""

import os
import shlex
import subprocess

import pytest

from testlib import resolve_source


ENV_SCRIPT = resolve_source(
    "/opt/neurodesktop/environment_variables.sh",
    "config/jupyter/environment_variables.sh",
)
UNSET = "__UNSET__"


def _configure_apptainer_nv(driver_version_file, apptainer_nv=UNSET):
    env = {
        "CVMFS_DISABLE": "true",
        "HOME": "/tmp",
        "NEURODESKTOP_ENV_SOURCED": "1",
        "PATH": os.environ["PATH"],
        "USER": "test-user",
    }
    if apptainer_nv != UNSET:
        env["APPTAINER_NV"] = apptainer_nv

    command = (
        f"source {shlex.quote(str(ENV_SCRIPT))} >/dev/null 2>&1; "
        "declare -F configure_apptainer_nv >/dev/null || exit 91; "
        f"configure_apptainer_nv {shlex.quote(str(driver_version_file))}; "
        f'printf "%s" "${{APPTAINER_NV-{UNSET}}}"'
    )
    return subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_loaded_nvidia_driver_enables_apptainer_nv(tmp_path):
    driver_version_file = tmp_path / "version"
    driver_version_file.touch()

    result = _configure_apptainer_nv(driver_version_file)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "1"


def test_missing_nvidia_driver_leaves_apptainer_nv_unset(tmp_path):
    result = _configure_apptainer_nv(tmp_path / "missing")

    assert result.returncode == 0, result.stderr
    assert result.stdout == UNSET


@pytest.mark.parametrize("explicit_value", ["0", "1", ""])
def test_explicit_apptainer_nv_value_is_preserved(tmp_path, explicit_value):
    driver_version_file = tmp_path / "version"
    driver_version_file.touch()

    result = _configure_apptainer_nv(driver_version_file, explicit_value)

    assert result.returncode == 0, result.stderr
    assert result.stdout == explicit_value
