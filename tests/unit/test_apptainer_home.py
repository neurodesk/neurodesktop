"""Regression coverage for Apptainer home initialization."""

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


def _source_environment(home=None, apptainer_home=None):
    env = {
        "NEURODESKTOP_ENV_SOURCED": "1",
        "PATH": os.environ["PATH"],
        "USER": "test-user",
    }
    if home is not None:
        env["HOME"] = str(home)
    if apptainer_home is not None:
        env["APPTAINER_HOME"] = str(apptainer_home)

    command = (
        f"source {shlex.quote(str(ENV_SCRIPT))} >/dev/null 2>&1; "
        f'printf "%s" "${{APPTAINER_HOME-{UNSET}}}"'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    return result.stdout


def test_existing_home_defaults_apptainer_home(tmp_path):
    assert _source_environment(home=tmp_path) == str(tmp_path)


def test_explicit_apptainer_home_is_preserved(tmp_path):
    explicit_home = "/custom/apptainer-home"

    assert (
        _source_environment(home=tmp_path, apptainer_home=explicit_home)
        == explicit_home
    )


def test_empty_apptainer_home_defaults_to_home(tmp_path):
    assert _source_environment(home=tmp_path, apptainer_home="") == str(tmp_path)


@pytest.mark.parametrize("home", [None, ""])
def test_missing_home_does_not_set_apptainer_home(home):
    assert _source_environment(home=home) == UNSET


def test_non_directory_home_does_not_set_apptainer_home(tmp_path):
    assert _source_environment(home=tmp_path / "missing") == UNSET
