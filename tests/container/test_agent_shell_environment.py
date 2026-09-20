"""Exercise installed Lmod from a fresh agent tool shell, without CVMFS data."""

import os
import subprocess


def test_agent_tool_shell_discovers_and_loads_a_module(tmp_path):
    module = tmp_path / "modules" / "neurodesk-shell-probe" / "1.0.lua"
    module.parent.mkdir(parents=True)
    module.write_text('setenv("NEURODESK_SHELL_PROBE", "loaded")\n')
    result = subprocess.run(
        ["/bin/sh", "-c", '''
            . /opt/neurodesktop/agent_shell_setup.sh
            exec bash --noprofile --norc -c '
                set -euo pipefail
                module use "$1"
                module spider neurodesk-shell-probe/1.0
                module load neurodesk-shell-probe/1.0
                test "$NEURODESK_SHELL_PROBE" = loaded
                test "$BASH_ENV" = /opt/neurodesktop/agent_bash_env.sh
                bash --noprofile --norc -c "type module >/dev/null"
            ' _ "$1"
        ''', "_", str(tmp_path / "modules")],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path),
             "USER": os.environ.get("USER", "jovyan"), "TERM": "dumb"},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "neurodesk-shell-probe" in result.stdout + result.stderr
