"""Execute the interactive shell banner that advertises the module commands.

The banner is the only place a terminal user is told how to reach the
Neurodesk catalogue without the Applications menu, so it is driven here as a
shell rather than asserted as source text: a quoting mistake in the message
would leave a source assertion green while every new terminal printed a
mangled line or failed outright.
"""

import os
import shlex
import subprocess

import pytest

from testlib import repo_path


ENV_SCRIPT = "config/jupyter/environment_variables.sh"
MODULE_INIT = "/usr/share/module.sh"


@pytest.fixture
def banner(tmp_path):
    """Run the script with the image's Lmod initializer replaced by a stub."""
    stub = tmp_path / "module.sh"
    stub.write_text("")
    script = tmp_path / "environment_variables.sh"
    script.write_text(
        repo_path(ENV_SCRIPT).read_text().replace(MODULE_INIT, str(stub))
    )

    def run(interactive=True, **extra_env):
        arguments = ["bash", "--noprofile", "--norc"]
        arguments.append("-ic" if interactive else "-c")
        arguments.append(f"source {shlex.quote(str(script))}")
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(tmp_path),
                "USER": "test-user",
                "NEURODESKTOP_LOCAL_CONTAINERS": str(tmp_path / "containers"),
                **extra_env,
            },
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    return run


@pytest.mark.parametrize(
    "command",
    [
        '"ml av"',
        '"ml <tool>/<version>"',
        '"ml help <tool>"',
        '"ml keyword <binary>"',
    ],
)
def test_banner_advertises_module_command(banner, command):
    assert command in banner()


def test_banner_survives_its_own_quoting(banner):
    assert "If you don't know where a certain binary is available" in banner()


def test_banner_is_printed_once_per_shell(banner):
    assert "Neuroimaging tools" not in banner(NEURODESKTOP_MSG_SHOWN="1")


def test_non_interactive_shells_stay_silent(banner):
    assert banner(interactive=False) == ""
