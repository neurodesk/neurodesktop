"""Shared shell defaults for Lmod's availability listing."""

import os
import subprocess

import pytest

from testlib import resolve_source


ENV_SCRIPT = resolve_source(
    "/opt/neurodesktop/environment_variables.sh",
    "config/jupyter/environment_variables.sh",
)


@pytest.mark.parametrize("override, expected", [(None, "no"), ("yes", "yes"), ("no", "no")])
def test_avail_extensions_default_and_override(tmp_path, override, expected):
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "USER": "test-user",
        "NEURODESKTOP_ENV_SOURCED": "1",
    }
    if override is not None:
        env["LMOD_AVAIL_EXTENSIONS"] = override
    result = subprocess.run(
        [
            "bash", "-c",
            'source "$1" >/dev/null 2>&1; '
            'bash -c \'printf "%s" "$LMOD_AVAIL_EXTENSIONS"\'',
            "test", str(ENV_SCRIPT),
        ],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
