"""Run the Lmod cache warm-up against stub Lmod and CVMFS trees.

``tests/container/test_lmod_cache_warmup.py`` checks the same contract
against the image's real Lmod.
"""

import os
import subprocess
import time

from testlib import resolve_source


def _source(name):
    return resolve_source(f"/opt/neurodesktop/{name}", f"config/jupyter/{name}")


def _write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def _fixture(tmp_path, module_status=0):
    """Stub Lmod records each call; the stub environment script sets MODULEPATH."""
    modules = tmp_path / "cvmfs/neurodesk-modules"
    modules.mkdir(parents=True)
    calls = tmp_path / "calls"
    env_script = _write(tmp_path / "environment_variables.sh", (
        '[ -z "${NEURODESKTOP_ENV_SOURCED:-}" ] || return 0\n'
        "export NEURODESKTOP_ENV_SOURCED=1\n"
        f'export MODULEPATH="{modules}"\n'
    ))
    lmod_init = _write(tmp_path / "lmod/init/bash", (
        "module() {\n"
        '    local cache_dir="${LMOD_TEST_CACHE_DIR:-$HOME/.cache/lmod}"\n'
        '    if [ "$1" = "--config-json" ]; then\n'
        '        printf \'{"configT":{"usrCacheDir":"%s"}}\\n\' "$cache_dir" >&2\n'
        '        return 0\n'
        '    fi\n'
        '    marker=no; [ -f "$cache_dir/invalidated" ] && marker=yes\n'
        f'    printf "%s|%s|%s|%s|%s\\n" "$(id -un)" "$LMOD_SHORT_TIME" "$MODULEPATH" '
        f'"$marker" "$*" >> "{calls}"\n'
        f"    return {module_status}\n"
        "}\n"
    ))
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "NEURODESKTOP_WARM_MODULES_DIR": str(modules),
        "NEURODESKTOP_WARM_LMOD_INIT": str(lmod_init),
        "NEURODESKTOP_WARM_ENV_SCRIPT": str(env_script),
    }
    env.pop("NEURODESKTOP_LMOD_CACHE_WARMUP", None)
    return env, calls, modules, home


def _run(env):
    return subprocess.run(
        ["bash", str(_source("warm_module_cache.sh"))],
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_builds_a_fresh_user_cache_for_the_cvmfs_modulepath(tmp_path):
    env, calls, modules, home = _fixture(tmp_path)
    # A stale guard from the caller must not stop MODULEPATH being derived.
    env["NEURODESKTOP_ENV_SOURCED"] = "1"
    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert "cache built" in result.stdout
    user, short_time, modulepath, invalidated, args = calls.read_text().strip().split("|")
    assert user == subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    # Always written, and never reusing yesterday's list of published tools.
    assert short_time == "0"
    assert invalidated == "yes"
    assert modulepath == str(modules)
    assert args == "--quiet avail"
    assert (home / ".cache/lmod").is_dir()
    assert (home / ".cache/lmod").stat().st_mode & 0o777 == 0o700


def test_uses_the_cache_directory_reported_by_initialized_lmod(tmp_path):
    env, calls, _, home = _fixture(tmp_path)
    cache_dir = tmp_path / "custom cache/lmod"
    with open(env["NEURODESKTOP_WARM_ENV_SCRIPT"], "a") as script:
        script.write(f'export LMOD_TEST_CACHE_DIR="{cache_dir}"\n')
    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert "cache built" in result.stdout
    assert calls.read_text().strip().split("|")[3] == "yes"
    assert (cache_dir / "invalidated").is_file()
    assert (cache_dir / "invalidated").stat().st_mode & 0o777 == 0o600
    assert not (home / ".cache/lmod").exists()
    assert not (home / ".lmod.d").exists()


def test_skips_without_mounted_cvmfs_modules(tmp_path):
    env, calls, modules, _ = _fixture(tmp_path)
    modules.rmdir()
    result = _run(env)
    assert result.returncode == 0
    assert "unavailable" in result.stdout
    assert not calls.exists()


def test_can_be_disabled(tmp_path):
    env, calls, _, _ = _fixture(tmp_path)
    env["NEURODESKTOP_LMOD_CACHE_WARMUP"] = "0"
    result = _run(env)
    assert result.returncode == 0
    assert "Disabled" in result.stdout
    assert not calls.exists()


def test_lmod_failure_never_fails_startup(tmp_path):
    env, calls, _, _ = _fixture(tmp_path, module_status=3)
    result = _run(env)
    assert result.returncode == 0
    assert calls.exists()
    assert "[WARN]" in result.stdout


def test_root_builds_as_the_notebook_user_with_a_clean_environment(tmp_path):
    env, calls, _, home = _fixture(tmp_path)
    shims = tmp_path / "shims"
    log = tmp_path / "runuser.log"
    me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    # Pretend to be root for the launcher only; the stub runuser then runs the
    # build as this test's user, as the real one would as the notebook user.
    _write(shims / "id", (
        "#!/bin/bash\n"
        f'if [ "$1" = "-u" ] && [ -z "${{AS_USER:-}}" ]; then echo 0; else echo "{me}"; fi\n'
    ), 0o755)
    _write(shims / "getent", f'#!/bin/bash\n[ "$2" = "alice" ] && echo "alice:x:1000:100::{home}:/bin/bash"\n', 0o755)
    _write(shims / "runuser", (
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" > "{log}"\n'
        '[ "$1" = "-u" ] && [ "$3" = "--" ] || exit 99\n'
        "shift 3\n"
        'exec env AS_USER=1 "$@"\n'
    ), 0o755)
    env.update(PATH=f"{shims}:{env['PATH']}", NB_USER="alice", HOME="/root-home",
               STARTUP_SECRET="root-only")
    result = _run(env)
    assert result.returncode == 0, result.stdout + result.stderr
    invocation = log.read_text()
    assert invocation.startswith("-u alice -- env -i ")
    assert f"HOME={home}" in invocation
    assert "STARTUP_SECRET" not in invocation
    assert calls.read_text().count("--quiet avail") == 1
    assert (home / ".cache/lmod").is_dir()


def test_deferred_startup_launches_warmup_without_delaying_done(tmp_path):
    script = _source("deferred_startup.sh").read_text()
    run = script[script.index('echo "[deferred] Starting deferred initialization..."'):]
    warm = _write(tmp_path / "warm.sh", f'#!/bin/bash\nsleep 2\ntouch "{tmp_path}/warmed"\n', 0o755)
    assert run.count("/opt/neurodesktop/warm_module_cache.sh") == 2
    run = run.replace("/opt/neurodesktop/warm_module_cache.sh", str(warm))
    stubs = (
        f'start_cvmfs() {{ touch "{tmp_path}/cvmfs"; }}\n'
        "start_slurm() { :; }\n"
        f'DEFERRED_DONE="{tmp_path}/done"\n'
    )
    started = time.monotonic()
    subprocess.run(["bash", "-c", stubs + run], check=True, timeout=10,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert time.monotonic() - started < 1.5
    assert (tmp_path / "done").exists()
    assert not (tmp_path / "warmed").exists()
    deadline = time.monotonic() + 10
    while not (tmp_path / "warmed").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert (tmp_path / "warmed").exists(), "the warm-up must still run after startup is done"
