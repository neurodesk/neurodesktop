"""Exercise startup work against temporary homes without the image."""

import os
import shlex
import subprocess
import sys

import pytest

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


def test_defaults_restore_treats_directory_prefix_as_literal(tmp_path):
    defaults = tmp_path / "defaults [image]*"
    defaults.mkdir()
    (defaults / "welcome.txt").write_text("image welcome")
    nested = defaults / "nested"
    nested.mkdir()
    (nested / "settings.json").write_text('{"theme": "dark"}')
    home = tmp_path / "home with spaces"
    home.mkdir()
    script = source("restore_home_defaults.sh").replace(
        'DEFAULTS_DIR="/opt/jovyan_defaults"',
        f"DEFAULTS_DIR={shlex.quote(str(defaults))}",
    )

    shell(script, home)

    assert (home / "welcome.txt").read_text() == "image welcome"
    assert (home / "nested/settings.json").read_text() == '{"theme": "dark"}'


def test_defaults_restore_preserves_user_managed_symlink_paths(tmp_path):
    defaults = tmp_path / "defaults"
    default_tool = defaults / ".config/tool"
    default_tool.mkdir(parents=True)
    for name in ("existing.json", "dangling.json"):
        (default_tool / name).write_text(f"image {name}")
        os.utime(default_tool / name, (200, 200))

    home = tmp_path / "home"
    home_tool = home / ".config/tool"
    home_tool.mkdir(parents=True)
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    existing = dotfiles / "existing.json"
    existing.write_text("user setting")
    os.utime(existing, (100, 100))
    (home_tool / "existing.json").symlink_to(existing)
    dangling = dotfiles / "not-created.json"
    (home_tool / "dangling.json").symlink_to(dangling)
    linked_directory = dotfiles / "linked-tool"
    linked_directory.mkdir()
    (home / ".config/linked-tool").symlink_to(linked_directory, target_is_directory=True)
    (defaults / ".config/linked-tool").mkdir()
    (defaults / ".config/linked-tool/nested.json").write_text("image nested")

    script = source("restore_home_defaults.sh").rsplit("\nrestore_defaults", 1)[0]
    sync = """
for relative in .config/tool/existing.json .config/tool/dangling.json \
    .config/linked-tool/nested.json; do
    sync_file_from_defaults "$DEFAULTS/$relative" "$HOME/$relative"
done
"""
    shell(script + sync, home, DEFAULTS=str(defaults))

    assert (home_tool / "existing.json").is_symlink()
    assert existing.read_text() == "user setting"
    assert (home_tool / "dangling.json").is_symlink()
    assert not dangling.exists()
    assert (home / ".config/linked-tool").is_symlink()
    assert not (linked_directory / "nested.json").exists()


def append_bashrc(home, defaults, calls):
    script = source("restore_home_defaults.sh").rsplit("\nrestore_defaults", 1)[0]
    return shell(script + '''
DEFAULTS_DIR="$DEFAULTS"
touch() { printf 'touch %s\\n' "$*" >> "$CALLS"; command touch "$@"; }
tee() { printf 'tee %s\\n' "$*" >> "$CALLS"; command tee "$@"; }
sudo() {
    printf 'sudo %s\\n' "$*" >> "$CALLS"
    if [ "$1" = "-n" ]; then return 0; fi
    chmod u+w "$HOME/.bashrc"
    command "$@"
}
handle_bashrc_append
''', home, DEFAULTS=str(defaults), CALLS=str(calls))


@pytest.mark.parametrize("content,mode", [
    ("user setting\n", 0o640),
    ("user setting\n", 0o440),
    ("# Neurodesk bashrc additions\nuser setting\n", 0o640),
    (None, 0o640),
], ids=["existing", "read-only", "marked", "dangling"])
def test_bashrc_append_preserves_symlink_and_target(tmp_path, content, mode):
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / ".bashrc_append").write_text("export IMAGE_SETTING=enabled\n")
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "user bashrc"
    if content is not None:
        target.write_text(content)
        target.chmod(mode)
        os.utime(target, ns=(100_000_000_000, 200_000_000_000))
        before = target.stat()
    bashrc = home / ".bashrc"
    bashrc.symlink_to(target)
    link_before = bashrc.lstat()
    calls = tmp_path / "calls"

    append_bashrc(home, defaults, calls)

    assert bashrc.is_symlink()
    link_after = bashrc.lstat()
    assert bashrc.readlink() == target
    assert link_after == link_before
    if content is None:
        assert not target.exists()
    else:
        after = target.stat()
        assert target.read_text() == content
        assert after == before
        assert (after.st_atime_ns, after.st_mtime_ns, after.st_ctime_ns) == (
            before.st_atime_ns, before.st_mtime_ns, before.st_ctime_ns,
        )
    assert not calls.exists(), calls.read_text()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("linked_home", [False, True])
def test_bashrc_append_adds_defaults_once(tmp_path, existing, linked_home):
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / ".bashrc_append").write_text("export IMAGE_SETTING=enabled\n")
    home = tmp_path / "home"
    home.mkdir()
    if linked_home:
        link = tmp_path / "linked home"
        link.symlink_to(home, target_is_directory=True)
        home = link
    bashrc = home / ".bashrc"
    content = "export USER_SETTING=keep_me\n" if existing else ""
    if existing:
        bashrc.write_text(content)
        bashrc.chmod(0o640)
    calls = tmp_path / "calls"

    append_bashrc(home, defaults, calls)

    expected = content + "\n# Neurodesk bashrc additions\nexport IMAGE_SETTING=enabled\n"
    assert bashrc.read_text() == expected
    if existing:
        assert bashrc.stat().st_mode & 0o777 == 0o640
    before = bashrc.stat()
    append_bashrc(home, defaults, calls)
    assert bashrc.stat() == before
    assert bashrc.read_text() == expected
    if existing:
        assert not calls.exists()
    else:
        assert calls.read_text().splitlines() == [f"touch {bashrc}"]


@pytest.mark.skipif(os.geteuid() == 0, reason="Requires unprivileged file permission enforcement")
def test_bashrc_append_retains_permission_fallback(tmp_path):
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / ".bashrc_append").write_text("export IMAGE_SETTING=enabled\n")
    home = tmp_path / "home"
    home.mkdir()
    bashrc = home / ".bashrc"
    bashrc.write_text("user setting\n")
    bashrc.chmod(0o440)
    calls = tmp_path / "calls"

    append_bashrc(home, defaults, calls)

    assert bashrc.read_text() == "user setting\n\n# Neurodesk bashrc additions\nexport IMAGE_SETTING=enabled\n"
    assert calls.read_text().splitlines() == ["sudo -n true", f"sudo tee -a {bashrc}"]
    append_bashrc(home, defaults, calls)
    assert bashrc.read_text().count("# Neurodesk bashrc additions") == 1
    assert calls.read_text().splitlines() == ["sudo -n true", f"sudo tee -a {bashrc}"]


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
