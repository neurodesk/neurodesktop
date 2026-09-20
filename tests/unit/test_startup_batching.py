"""Exercise startup work against temporary homes without the image."""

import os
import shlex
import subprocess
import sys

from testlib import resolve_source


def source(name):
    return resolve_source(f"/opt/neurodesktop/{name}", f"config/jupyter/{name}").read_text()


def shell(script, home, **env):
    return subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "HOME": str(home), **env},
        capture_output=True, text=True, timeout=10, check=True,
    )


def sanitizer():
    script = source("jupyterlab_startup.sh")
    return script[script.index("sanitize_jupyterlab_workspaces() {"):script.index("ensure_jupyterlab_page_config() {")]


def test_workspace_batch_preserves_valid_files_and_quarantines_invalid(tmp_path):
    workspaces = tmp_path / ".jupyter/lab/workspaces"
    workspaces.mkdir(parents=True)
    for n in range(100):
        (workspaces / f"{n}.jupyterlab-workspace").write_text('{}')
    invalid = workspaces / "bad name\nwith newline.jupyterlab-workspace"
    invalid.write_bytes(b'\xff')
    malformed = workspaces / "malformed.jupyterlab-workspace"
    malformed.write_text('{')
    deep = workspaces / "deep.jupyterlab-workspace"
    # Unclosed nesting is invalid even on Python versions that can parse
    # 2,000 balanced levels without reaching their recursion limit.
    deep.write_text("[" * 2000)
    nested = workspaces / "nested"
    nested.mkdir()
    (nested / "untouched.jupyterlab-workspace").write_text('{')
    link = workspaces / "link.jupyterlab-workspace"
    link.symlink_to(nested / "untouched.jupyterlab-workspace")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "calls"
    python = bin_dir / "python3"
    python.write_text(f'#!/bin/bash\necho called >> {shlex.quote(str(counter))}\nexec {shlex.quote(sys.executable)} "$@"\n')
    python.chmod(0o755)

    shell(sanitizer(), tmp_path, PATH=f"{bin_dir}:{os.environ['PATH']}")

    assert counter.read_text().splitlines() == ["called"]
    assert all((workspaces / f"{n}.jupyterlab-workspace").read_text() == '{}' for n in range(100))
    assert not invalid.exists()
    assert not malformed.exists()
    assert not deep.exists()
    assert next(workspaces.glob("deep*.invalid-*")).read_text() == "[" * 2000
    assert next(workspaces.glob("bad*.invalid-*")).read_bytes() == b'\xff'
    assert next(workspaces.glob("malformed*.invalid-*")).read_text() == '{'
    assert link.is_symlink()
    assert (nested / "untouched.jupyterlab-workspace").read_text() == '{'
    shell(sanitizer(), tmp_path)
    assert len(list(workspaces.glob("*.invalid-*"))) == 3


def test_defaults_restore_missing_migrate_newer_and_preserve_current(tmp_path):
    script = source("restore_home_defaults.sh").rsplit("\nrestore_defaults", 1)[0]
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    for name in ("missing", "newer", "custom"):
        (defaults / name).write_text(f"default {name}")
        os.utime(defaults / name, (200, 200))
    (home / "newer").write_text("old")
    os.utime(home / "newer", (100, 100))
    (home / "custom").write_text("user content")
    os.utime(home / "custom", (300, 300))
    shell(script + '\nfor name in missing newer custom; do sync_file_from_defaults "$DEFAULTS/$name" "$HOME/nested/$name"; done', home, DEFAULTS=str(defaults))
    assert (home / "nested/missing").read_text() == "default missing"
    shell(script + '\nfor name in newer custom; do sync_file_from_defaults "$DEFAULTS/$name" "$HOME/$name"; done', home, DEFAULTS=str(defaults))
    assert (home / "newer").read_text() == "default newer"
    assert (home / "custom").read_text() == "user content"


def test_deferred_components_run_independently_and_done_waits_for_both(tmp_path):
    script = source("deferred_startup.sh")
    run = script[script.index('echo "[deferred] Starting deferred initialization..."'):]
    # CVMFS cannot finish until Slurm starts. A serial runner times out.
    stubs = '''
start_cvmfs() {
    for attempt in {1..100}; do
        if [ -f "$HOME/slurm" ]; then
            sleep 0.05
            [ ! -e "$DEFERRED_DONE" ] || exit 1
            touch "$HOME/cvmfs"
            return
        fi
        sleep 0.01
    done
    return 1
}
start_slurm() { touch "$HOME/slurm"; }
DEFERRED_DONE="$HOME/done"
'''
    shell(stubs + run, tmp_path)
    assert (tmp_path / "slurm").exists()
    assert (tmp_path / "cvmfs").exists()
    assert (tmp_path / "done").exists()


def test_home_permissions_protect_cache_and_new_ssh_files(tmp_path):
    script = source("restore_home_defaults.sh").rsplit("\nrestore_defaults", 1)[0]
    cache = tmp_path / ".config/matplotlib-mpldir"
    cache.mkdir(parents=True)
    cache.chmod(0o777)
    shell(script + '\ncreate_directories\nsetup_ssh_directory\numask 000\nmkdir "$HOME/.ssh/new"\ntouch "$HOME/.ssh/new/key"', tmp_path)
    assert cache.stat().st_mode & 0o777 == 0o700
    assert (tmp_path / ".ssh").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / ".ssh/new").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / ".ssh/new/key").stat().st_mode & 0o777 == 0o600


def test_defaults_copy_retains_privileged_ownership_repair(tmp_path):
    script = source("restore_home_defaults.sh").rsplit("\nrestore_defaults", 1)[0]
    (tmp_path / "source").write_text("default")
    stubs = '''
cp() { return 1; }
sudo() {
    if [ "$1" = "-n" ]; then return 0; fi
    if [ "$1" = "cp" ]; then command "$@"; return; fi
    printf '%s\\n' "$*" > "$HOME/repair"
}
NB_UID=1234
NB_GID=5678
sync_file_from_defaults "$HOME/source" "$HOME/destination"
'''
    shell(script + stubs, tmp_path)
    assert (tmp_path / "destination").read_text() == "default"
    assert (tmp_path / "repair").read_text().strip() == f"chown 1234:5678 {tmp_path}/destination"
