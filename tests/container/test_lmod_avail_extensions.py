"""Exercise extension filtering through the terminal's real ``ml av`` command."""

import os
import subprocess


LMOD_INIT = "/usr/share/lmod/lmod/init/bash"
MODULE_NAME = "lmod-avail-test/1.0"
EXTENSION_NAME = "lmod-avail-extension-test"
EXTENSION_SPEC = f"{EXTENSION_NAME}/9.9"


def _run_ml_avail(module_path, home, avail_extensions=None):
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "LMOD_COLORIZE": "no",
            "MODULEPATH": str(module_path),
            "TERM": "dumb",
        }
    )
    if avail_extensions is not None:
        env["LMOD_AVAIL_EXTENSIONS"] = avail_extensions
    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f"set -e; source {LMOD_INIT}; "
                'mkdir -p "$HOME/.cache/lmod"; '
                '"$LMOD_DIR/spider" -o spiderT "$MODULEPATH" '
                '> "$HOME/.cache/lmod/spiderT.lua"; '
                "ml av"
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def test_ml_avail_hides_extensions_by_default(tmp_path):
    module_path = tmp_path / "modules"
    module_file = module_path / "lmod-avail-test" / "1.0.lua"
    module_file.parent.mkdir(parents=True)
    module_file.write_text(
        f'extensions("{EXTENSION_SPEC}")\n',
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()

    assert os.environ.get("LMOD_AVAIL_EXTENSIONS") == "no"

    enabled_output = _run_ml_avail(module_path, home, "yes")
    assert MODULE_NAME in enabled_output
    assert EXTENSION_NAME in enabled_output

    default_output = _run_ml_avail(module_path, home)
    assert MODULE_NAME in default_output
    assert EXTENSION_NAME not in default_output
