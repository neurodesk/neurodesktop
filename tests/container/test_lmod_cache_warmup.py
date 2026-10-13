"""Prove the installed Lmod honors the module-cache warm-up contract.

The unit tests drive ``warm_module_cache.sh`` against a stub ``module``. They
cannot show that this Lmod still keeps its user cache in ``~/.lmod.d/.cache``,
rebuilds it when an ``invalidated`` marker exists, and writes it for a quick
build when ``LMOD_SHORT_TIME=0``. An Lmod upgrade could change any of these.
"""

import os
import subprocess

from testlib import resolve_source


WARM_SCRIPT = resolve_source(
    "/opt/neurodesktop/warm_module_cache.sh", "config/jupyter/warm_module_cache.sh",
)


def _module(root, name):
    path = root / name / "1.0.lua"
    path.parent.mkdir(parents=True)
    path.write_text(f'whatis("{name}")\n', encoding="utf-8")


def _warm(tmp_path, modules, home):
    env_script = tmp_path / "environment_variables.sh"
    env_script.write_text(f'export MODULEPATH="{modules}"\n', encoding="utf-8")
    result = subprocess.run(
        ["bash", str(WARM_SCRIPT)],
        env={"PATH": os.environ["PATH"], "HOME": str(home),
             "USER": os.environ.get("USER", "jovyan"),
             "NEURODESKTOP_WARM_MODULES_DIR": str(modules),
             "NEURODESKTOP_WARM_ENV_SCRIPT": str(env_script)},
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cache built" in result.stdout, result.stdout + result.stderr


def _user_cache(home):
    return "".join(path.read_text(encoding="utf-8", errors="replace")
                   for path in (home / ".lmod.d/.cache").glob("spiderT*.lua"))


def test_warmup_writes_and_refreshes_the_real_lmod_user_cache(tmp_path):
    modules = tmp_path / "modules"
    home = tmp_path / "home"
    home.mkdir()
    _module(modules, "warmup-first")

    _warm(tmp_path, modules, home)
    assert "warmup-first" in _user_cache(home)

    # Lmod would keep serving this cache for a day; a restart must not.
    _module(modules, "warmup-published-later")
    _warm(tmp_path, modules, home)
    assert "warmup-published-later" in _user_cache(home)
    assert not (home / ".lmod.d/.cache/invalidated").exists()
