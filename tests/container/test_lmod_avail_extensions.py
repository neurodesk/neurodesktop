"""Prove that a shell in the image hides Lmod extensions from ``ml av``.

``tests/unit/test_lmod_extensions.py`` asserts that
``environment_variables.sh`` exports ``LMOD_AVAIL_EXTENSIONS=no``. That says
nothing about whether Lmod still honors the variable, so an Lmod upgrade that
renamed or dropped it would leave the unit test green and the listing cluttered.
This test runs the real ``ml av`` against a synthetic module that provides an
extension.
"""

import os
import subprocess

from testlib import resolve_source


ENV_SCRIPT = resolve_source(
    "/opt/neurodesktop/environment_variables.sh",
    "config/jupyter/environment_variables.sh",
)
LMOD_INIT = "/usr/share/lmod/lmod/init/bash"
MODULE_NAME = "lmod-avail-test/1.0"
EXTENSION_NAME = "lmod-avail-extension-test"


def _ml_avail(module_path, home, avail_extensions=None):
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "USER": os.environ.get("USER", "jovyan"),
        "LMOD_COLORIZE": "no",
        "TERM": "dumb",
    }
    if avail_extensions is not None:
        env["LMOD_AVAIL_EXTENSIONS"] = avail_extensions

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            # MODULEPATH is set after sourcing because the script derives it
            # from CVMFS, and this test supplies its own catalogue instead.
            f'source "{ENV_SCRIPT}" >/dev/null 2>&1; '
            f'source "{LMOD_INIT}"; '
            f'export MODULEPATH="{module_path}"; '
            'mkdir -p "$HOME/.cache/lmod"; '
            '"$LMOD_DIR/spider" -o spiderT "$MODULEPATH" '
            '> "$HOME/.cache/lmod/spiderT.lua"; '
            "ml av",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def test_ml_avail_hides_extensions_by_default(tmp_path):
    module_file = tmp_path / "modules" / "lmod-avail-test" / "1.0.lua"
    module_file.parent.mkdir(parents=True)
    module_file.write_text(f'extensions("{EXTENSION_NAME}/9.9")\n', encoding="utf-8")
    module_path = tmp_path / "modules"
    home = tmp_path / "home"

    enabled = _ml_avail(module_path, home / "enabled", "yes")
    assert MODULE_NAME in enabled
    assert EXTENSION_NAME in enabled, "the probe module provides no extension"

    default = _ml_avail(module_path, home / "default")
    assert MODULE_NAME in default
    assert EXTENSION_NAME not in default
