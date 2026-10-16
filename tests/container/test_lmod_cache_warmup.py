import json
import os
import subprocess
from pathlib import Path

import pytest

from testlib import resolve_source


WARM_SCRIPT = resolve_source(
    "/opt/neurodesktop/warm_module_cache.sh", "config/jupyter/warm_module_cache.sh",
)


def _module(root, name):
    path = root / name / "1.0.lua"
    path.parent.mkdir(parents=True)
    path.write_text(
        'local f = assert(io.open(pathJoin(os.getenv("HOME"), "module-reads"), "a"))\n'
        f'f:write("{name}\\n")\n'
        'f:close()\n'
        f'whatis("{name}")\n', encoding="utf-8",
    )


def _warm(env):
    result = subprocess.run(
        ["bash", str(WARM_SCRIPT)], env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cache built" in result.stdout, result.stdout + result.stderr


def _lmod(env, *args):
    result = subprocess.run(
        ["bash", "-c", 'source /usr/share/lmod/lmod/init/bash; module "$@"',
         "lmod-test", *args],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def _user_cache(cache_dir):
    return "".join(path.read_text(encoding="utf-8", errors="replace")
                   for path in cache_dir.glob("spiderT*.lua"))


@pytest.mark.parametrize("custom_xdg", [False, True], ids=["home", "custom-xdg"])
def test_warmup_writes_and_refreshes_the_real_lmod_user_cache(tmp_path, custom_xdg):
    modules = tmp_path / "modules"
    home = tmp_path / "custom home"
    home.mkdir()
    _module(modules, "warmup-first")
    env_script = tmp_path / "environment_variables.sh"
    env_script.write_text(f'export MODULEPATH="{modules}"\n', encoding="utf-8")
    env = {"PATH": os.environ["PATH"], "HOME": str(home),
           "USER": os.environ.get("USER", "jovyan"), "MODULEPATH": str(modules),
           "NEURODESKTOP_WARM_MODULES_DIR": str(modules),
           "NEURODESKTOP_WARM_ENV_SCRIPT": str(env_script)}
    if custom_xdg:
        env["XDG_CACHE_HOME"] = str(tmp_path / "custom cache")
    cache_dir = Path(json.loads(_lmod(env, "--config-json"))["configT"]["usrCacheDir"])
    reads = home / "module-reads"

    _warm(env)
    assert "warmup-first" in _user_cache(cache_dir)
    cold_reads = reads.read_text()
    assert cold_reads.splitlines() == ["warmup-first"]
    assert "warmup-first/1.0" in _lmod(env, "--quiet", "avail")
    assert reads.read_text() == cold_reads, "warm avail must not evaluate modulefiles"

    # Lmod would keep serving this cache for a day; a restart must not.
    _module(modules, "warmup-published-later")
    assert "warmup-published-later" not in _user_cache(cache_dir)
    _warm(env)
    assert "warmup-published-later" in _user_cache(cache_dir)
    assert not (cache_dir / "invalidated").exists()
    refreshed_reads = reads.read_text()
    assert refreshed_reads.splitlines().count("warmup-first") == 2
    assert refreshed_reads.splitlines().count("warmup-published-later") == 1
    assert "warmup-published-later/1.0" in _lmod(env, "--quiet", "avail")
    assert reads.read_text() == refreshed_reads
    assert cache_dir.stat().st_uid == os.getuid()
    assert cache_dir.stat().st_mode & 0o077 == 0
    for path in cache_dir.glob("spiderT*"):
        assert path.stat().st_uid == os.getuid()
        assert path.stat().st_mode & 0o077 == 0
