"""Execute agent launchers and tool shells with isolated image paths."""

import os
import shlex
import subprocess

import pytest

from testlib import repo_path


@pytest.fixture
def image(tmp_path):
    paths = {
        path: tmp_path / str(index)
        for index, path in enumerate([
            "/opt/neurodesktop/agent_shell_setup.sh",
            "/opt/neurodesktop/agent_bash_env.sh",
            "/opt/neurodesktop/environment_variables.sh",
            "/etc/profile.d/lmod.sh",
            "/usr/share/module.sh",
            "/usr/share/lmod/lmod/init/bash",
            "/opt/AGENTS.md",
            "/usr/bin/codex",
            "/usr/bin/opencode",
            "/opt/jovyan_defaults/.local/bin/claude",
        ])
    }

    def install(source, target):
        text = repo_path(source).read_text()
        for original, replacement in paths.items():
            text = text.replace(original, str(replacement))
        target.write_text(text)
        target.chmod(0o755)
        return target

    for name in ("agent_shell_setup.sh", "agent_bash_env.sh"):
        install(f"config/agents/{name}", paths[f"/opt/neurodesktop/{name}"])
    paths["/opt/neurodesktop/environment_variables.sh"].write_text(
        'echo environment-banner\n: "$UNSET_ENVIRONMENT_VARIABLE"\n'
        'export MODULEPATH=/test/catalogue\n'
    )
    for path in ("/etc/profile.d/lmod.sh", "/usr/share/module.sh", "/usr/share/lmod/lmod/init/bash"):
        paths[path].write_text(
            f"export INIT_PATH={shlex.quote(path)}\n"
            'export BASH_ENV=/missing/raw-lmod-initializer\n'
            'module() { case "$*" in\n'
            '  "load test/1") export MODULE_LOADED=yes ;;\n'
            '  "--version") echo Lmod-test ;;\n'
            '  *) return 1 ;;\n'
            'esac; }\n'
        )
    paths["/opt/AGENTS.md"].write_text("Environment guidance revision: test-revision\n")
    return paths, install


def run(script, tmp_path, **extra_env):
    env = {k: v for k, v in os.environ.items()
           if k not in ("BASH_ENV", "NEURODESKTOP_PREVIOUS_BASH_ENV")
           and not k.startswith("BASH_FUNC_")}
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        env={**env, "HOME": str(tmp_path), **extra_env},
        cwd=tmp_path, text=True, capture_output=True, timeout=10,
    )


@pytest.mark.parametrize("layout", ["profile", "module", "legacy"])
@pytest.mark.parametrize("strict", [False, True])
def test_initializer_loads_modules_and_preserves_shell_options(image, tmp_path, layout, strict):
    paths, _ = image
    if layout != "profile":
        paths["/etc/profile.d/lmod.sh"].unlink()
    if layout == "legacy":
        paths["/usr/share/module.sh"].unlink()
    initializer = paths["/opt/neurodesktop/agent_bash_env.sh"]
    result = run(
        ("set -euo pipefail\n" if strict else "set -eo pipefail\n")
        + f'source {initializer}\n'
        + 'module load test/1\nprintf "%s %s %s\\n" "$MODULEPATH" "$MODULE_LOADED" "$INIT_PATH"\n'
        + ('[[ "$-" == *u* ]]' if strict else '[[ "$-" != *u* ]]'), tmp_path,
    )
    assert result.returncode == 0, result.stderr
    expected = {"profile": "/etc/profile.d/lmod.sh", "module": "/usr/share/module.sh",
                "legacy": "/usr/share/lmod/lmod/init/bash"}[layout]
    assert result.stdout == f"/test/catalogue yes {expected}\n"
    assert not result.stderr


@pytest.mark.parametrize("launcher,args", [
    ("codex_exec", []), ("claude_exec", []), ("opencode", ["acp"]),
    ("t3-provider-bin/codex", ["app-server"]),
    ("t3-provider-bin/claude", []), ("t3-provider-bin/opencode", []),
])
def test_launchers_initialize_fresh_tool_shells_without_protocol_banners(image, tmp_path, launcher, args):
    paths, install = image
    for binary in ("/usr/bin/codex", "/usr/bin/opencode", "/opt/jovyan_defaults/.local/bin/claude"):
        paths[binary].write_text(
            '#!/bin/sh\nexec bash --noprofile --norc -c '
            "'module load test/1; test \"$MODULE_LOADED\" = yes && echo protocol-ok'\n"
        )
        paths[binary].chmod(0o755)
    wrapper = install(f"config/agents/{launcher}", tmp_path / "launcher")
    result = run(shlex.join([str(wrapper), *args]), tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "protocol-ok\n"
    assert not result.stderr


def test_setup_preserves_existing_initializer_and_survives_repeated_setup(image, tmp_path):
    paths, _ = image
    previous = tmp_path / "previous.sh"
    previous.write_text('export USER_INITIALIZED=yes\n')
    setup = paths["/opt/neurodesktop/agent_shell_setup.sh"]
    result = run(
        f'. {setup}\n. {setup}\n'
        "bash -c 'module load test/1; printf \"%s %s\\n\" \"$USER_INITIALIZED\" \"$MODULE_LOADED\"'",
        tmp_path, BASH_ENV=str(previous),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "yes yes\n"


def test_strict_script_stops_on_initialization_failure(image, tmp_path):
    paths, _ = image
    paths["/etc/profile.d/lmod.sh"].write_text('return 7\n')
    result = run(f'set -euo pipefail\nsource {paths["/opt/neurodesktop/agent_bash_env.sh"]}\necho unsafe', tmp_path)
    assert result.returncode == 7
    assert not result.stdout


def test_explicit_batch_initialization_does_not_repeat_user_setup(image, tmp_path):
    paths, _ = image
    previous = tmp_path / "previous.sh"
    previous.write_text('USER_SETUP_COUNT=$(( ${USER_SETUP_COUNT:-0} + 1 ))\n')
    setup = paths["/opt/neurodesktop/agent_shell_setup.sh"]
    initializer = paths["/opt/neurodesktop/agent_bash_env.sh"]
    result = run(
        f'BASH_ENV={previous}\n. {setup}\n'
        + shlex.join(["bash", "-c", f'source {initializer}\nprintf "%s\\n" "$USER_SETUP_COUNT"']),
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1\n"


def test_failed_module_load_stops_a_strict_batch_script(image, tmp_path):
    paths, _ = image
    result = run(
        f'set -euo pipefail\nsource {paths["/opt/neurodesktop/agent_bash_env.sh"]}\n'
        'module load missing/1\necho unsafe', tmp_path,
    )
    assert result.returncode != 0
    assert not result.stdout


@pytest.mark.parametrize("agent_shell", [False, True])
def test_login_profile_restores_only_agent_shell_initialization(image, tmp_path, agent_shell):
    paths, install = image
    profile = install("config/agents/agent_profile.sh", tmp_path / "profile.sh")
    setup = paths["/opt/neurodesktop/agent_shell_setup.sh"]
    result = run(
        (f'. {setup}\n' if agent_shell else '')
        + f'BASH_ENV=/distribution/lmod\n. {profile}\nprintf "%s\\n" "$BASH_ENV"', tmp_path,
    )
    assert result.returncode == 0, result.stderr
    expected = str(paths["/opt/neurodesktop/agent_bash_env.sh"]) if agent_shell else "/distribution/lmod"
    assert result.stdout == expected + "\n"


@pytest.mark.parametrize("scheduler_status", [0, 1, 124])
def test_preflight_reports_live_capacity_and_preserves_project_guidance(image, tmp_path, scheduler_status):
    paths, install = image
    preflight = install("config/agents/neurodesk-agent-preflight", tmp_path / "preflight")
    # Exercise timeout invocation without making the regression wait 15 seconds.
    timeout = tmp_path / "timeout"
    timeout.write_text('#!/bin/sh\n[ "$1" = 15s ] || exit 9\nshift\nexec "$@"\n')
    timeout.chmod(0o755)
    sinfo = tmp_path / "sinfo"
    sinfo.write_text(
        '#!/bin/sh\n[ "$1" = -o ] && [ "$2" = "%P %c %m %l %t" ] || exit 8\n'
        'echo "local* 4 15002 1:00:00 idle"\n'
        f'exit {scheduler_status}\n'
    )
    sinfo.chmod(0o755)
    project = tmp_path / "AGENTS.md"
    project.write_text("Keep my analysis instructions.\n")
    result = run(str(preflight), tmp_path, PATH=f'{tmp_path}:{os.environ["PATH"]}')
    assert result.returncode == scheduler_status, result.stderr
    assert "test-revision" in result.stdout
    assert "AGENTS.md differs" in result.stdout
    assert "local* 4 15002" in result.stdout
    assert project.read_text() == "Keep my analysis instructions.\n"


def test_shared_shell_assets_ship_in_image():
    dockerfile = repo_path("Dockerfile").read_text()
    assets = {
        "agent_shell_setup.sh": "/opt/neurodesktop/agent_shell_setup.sh",
        "agent_bash_env.sh": "/opt/neurodesktop/agent_bash_env.sh",
        "agent_profile.sh": "/etc/profile.d/zz-neurodesk-agent.sh",
        "neurodesk-agent-preflight": "/usr/local/bin/neurodesk-agent-preflight",
    }
    for name, target in assets.items():
        assert f"source=config/agents/{name}," in dockerfile
        assert f"/tmp/agents/{name} {target}" in dockerfile
