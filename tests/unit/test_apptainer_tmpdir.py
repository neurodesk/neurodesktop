"""Tests for the Apptainer session/tmp directory fix in environment_variables.sh.

Podman (unlike Docker) passes the host's TMPDIR through to the container.
When that host TMPDIR is empty or relative, Apptainer defaults its session
directory to "." and fails every container/module load with::

    FATAL:   container creation failed: failed to add  as session directory:
    path . is not an absolute path

environment_variables.sh must guarantee an absolute TMPDIR inside the
container and pin the Apptainer cache/tmp/work directories under it so module
loads never depend on a host-provided value that can be relative or missing.
"""

import os
import subprocess

import pytest

from testlib import resolve_source


ENV_SCRIPT = resolve_source(
    "/opt/neurodesktop/environment_variables.sh",
    "config/jupyter/environment_variables.sh",
)


def _source_with_tmpdir(tmpdir_value, extra_env=None):
    """Source environment_variables.sh with a controlled TMPDIR.

    Returns a dict of the resulting environment variables.
    """
    env = {
        "HOME": "/tmp/fake-home",
        "PATH": "/usr/bin:/bin",
        "NEURODESKTOP_ENV_SOURCED": "",
    }
    if tmpdir_value is not None:
        env["TMPDIR"] = tmpdir_value
    else:
        env.pop("TMPDIR", None)
    if extra_env:
        env.update(extra_env)

    # Print the four variables we care about after sourcing.
    snippet = (
        f"source {ENV_SCRIPT} >/dev/null 2>&1; "
        'printf "TMPDIR=%s\\n" "$TMPDIR"; '
        'printf "APPTAINER_CACHEDIR=%s\\n" "${APPTAINER_CACHEDIR:-}"; '
        'printf "APPTAINER_TMPDIR=%s\\n" "${APPTAINER_TMPDIR:-}"; '
        'printf "APPTAINER_WORKDIR=%s\\n" "${APPTAINER_WORKDIR:-}"'
    )
    proc = subprocess.run(
        ["bash", "-c", snippet],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, f"sourcing failed: {proc.stdout}\n{proc.stderr}"

    result = {}
    for line in proc.stdout.splitlines():
        key, _, val = line.partition("=")
        result[key] = val
    return result


def _is_absolute(path):
    return path.startswith("/")


@pytest.mark.parametrize(
    "host_tmpdir,desc",
    [
        (None, "TMPDIR unset (host does not export it)"),
        ("", "TMPDIR empty"),
        ("relative_tmp", "TMPDIR relative"),
        (".", "TMPDIR is a dot"),
        ("some/relative/path", "TMPDIR relative nested"),
    ],
)
def test_tmpdir_guaranteed_absolute_for_bad_host_values(host_tmpdir, desc):
    """A host-provided TMPDIR that is empty or relative must be replaced."""
    result = _source_with_tmpdir(host_tmpdir)
    assert result.get("TMPDIR", "").startswith("/"), (
        f"{desc}: TMPDIR={result.get('TMPDIR')!r} is not absolute"
    )
    for var in ("APPTAINER_CACHEDIR", "APPTAINER_TMPDIR", "APPTAINER_WORKDIR"):
        val = result.get(var, "")
        assert _is_absolute(val), (
            f"{desc}: {var}={val!r} is not absolute"
        )


def test_absolute_host_tmpdir_is_preserved():
    """A legitimate absolute host TMPDIR must not be overridden."""
    result = _source_with_tmpdir("/var/tmp")
    assert result["TMPDIR"] == "/var/tmp", result


def test_explicit_apptainer_dirs_not_overwritten():
    """User-provided APPTAINER_*DIR values must be respected."""
    result = _source_with_tmpdir(
        "/tmp",
        extra_env={
            "APPTAINER_CACHEDIR": "/custom/cache",
            "APPTAINER_TMPDIR": "/custom/tmp",
            "APPTAINER_WORKDIR": "/custom/work",
        },
    )
    assert result["APPTAINER_CACHEDIR"] == "/custom/cache", result
    assert result["APPTAINER_TMPDIR"] == "/custom/tmp", result
    assert result["APPTAINER_WORKDIR"] == "/custom/work", result


def test_defaults_under_tmpdir_when_unset():
    """When no overrides are given, the Apptainer dirs live under TMPDIR."""
    result = _source_with_tmpdir(None)
    tmpdir = result["TMPDIR"]
    assert result["APPTAINER_CACHEDIR"] == f"{tmpdir}/apptainer-cache", result
    assert result["APPTAINER_TMPDIR"] == f"{tmpdir}/apptainer-tmp", result
    assert result["APPTAINER_WORKDIR"] == f"{tmpdir}/apptainer-work", result
