import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from testlib import repo_path


def startup_bashrc(*, repair_home=False):
    source = repo_path("config/jupyter/before_notebook.sh").read_text()
    helpers = source[source.index("is_apptainer_runtime() {"):source.index("link_data_dir_if_present() {")]
    writer = source[source.index("# Source custom scripts in .bashrc"):source.index("# Note: environment_variables.sh")]
    return helpers + ("\nfix_home_ownership_if_needed\n" if repair_home else "") + writer


def run_bashrc(home, *, prefix="", repair_home=False, euid=None, **env):
    script = startup_bashrc(repair_home=repair_home)
    if euid is not None:
        # Bash makes EUID readonly; substitute only that input for portable branch coverage.
        script = script.replace('"$EUID"', '"$TEST_EUID"')
        env["TEST_EUID"] = str(euid)
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eu", "-c", prefix + script],
        env={"PATH": os.environ["PATH"], "HOME": str(home),
             "NB_USER": "jovyan", "NB_UID": "1234", "NB_GID": "5678", **env},
        capture_output=True, text=True, timeout=10, check=True,
    )


def test_root_bashrc_writer_requests_ownership_repair(tmp_path):
    result = run_bashrc(tmp_path, euid=0, prefix='''
chown() { printf '%s\\n' "$@" > "$HOME/chown-args"; }
sudo() { echo unexpected-sudo >&2; exit 99; }
''')

    calls = tmp_path / "chown-args"
    assert calls.exists(), "Root startup created .bashrc without repairing ownership"
    assert calls.read_text().splitlines() == ["-h", "1234:5678", str(tmp_path / ".bashrc")]
    assert "# Neurodesk persistent bash history" in (tmp_path / ".bashrc").read_text()
    assert not result.stderr


@pytest.mark.parametrize("euid,marker", [
    (1234, None),
    (0, "APPTAINER_CONTAINER"), (0, "APPTAINER_NAME"), (0, "APPTAINER_COMMAND"),
    (0, "SINGULARITY_CONTAINER"), (0, "SINGULARITY_NAME"), (0, "SINGULARITY_COMMAND"),
])
def test_bashrc_nonroot_and_apptainer_skip_chown_and_sudo(tmp_path, euid, marker):
    env = {marker: "test-runtime"} if marker else {}
    run_bashrc(tmp_path, euid=euid, prefix='''
chown() { echo unexpected-chown >&2; exit 99; }
sudo() { echo unexpected-sudo >&2; exit 99; }
''', **env)

    assert "# Neurodesk persistent bash history" in (tmp_path / ".bashrc").read_text()


def test_bashrc_unsupported_chown_preserves_content_and_idempotency(tmp_path):
    bashrc = tmp_path / ".bashrc"
    bashrc.write_text("export USER_SETTING=keep_me\n")
    bashrc.chmod(0o640)
    prefix = '''
chown() { echo attempted >> "$HOME/chown-calls"; return 1; }
sudo() { echo unexpected-sudo >&2; exit 99; }
'''

    first = run_bashrc(tmp_path, euid=0, prefix=prefix)
    content = bashrc.read_text()
    second = run_bashrc(tmp_path, euid=0, prefix=prefix)

    assert content.startswith("export USER_SETTING=keep_me\n")
    assert content.count("# Neurodesk persistent bash history") == 1
    assert content.count("then source /usr/share/module.sh; fi") == 1
    assert bashrc.read_text() == content
    assert bashrc.stat().st_mode & 0o777 == 0o640
    assert (tmp_path / "chown-calls").read_text().splitlines() == ["attempted", "attempted"]
    assert "[WARN]" in first.stdout
    assert "[WARN]" in second.stdout


@pytest.mark.skipif(os.geteuid() != 0, reason="Requires root for real numeric ownership changes")
@pytest.mark.parametrize("uid,gid", [(1000, 100), (1234, 5678)])
@pytest.mark.parametrize("existing", [False, True])
def test_root_home_repair_then_bashrc_creation(tmp_path, uid, gid, existing):
    home = tmp_path / "custom home"
    home.mkdir()
    bashrc = home / ".bashrc"
    if existing:
        bashrc.write_text("export USER_SETTING=keep_me\n")
    sibling = home / "unrelated"
    sibling.write_text("keep ownership")
    sibling_owner = sibling.stat().st_uid, sibling.stat().st_gid

    run_bashrc(home, repair_home=True, NB_UID=str(uid), NB_GID=str(gid))

    assert (home.stat().st_uid, home.stat().st_gid) == (uid, gid)
    assert (bashrc.stat().st_uid, bashrc.stat().st_gid) == (uid, gid)
    assert (sibling.stat().st_uid, sibling.stat().st_gid) == sibling_owner
    if existing:
        assert bashrc.read_text().startswith("export USER_SETTING=keep_me\n")
    content = bashrc.read_text()
    run_bashrc(home, repair_home=True, NB_UID=str(uid), NB_GID=str(gid))
    assert bashrc.read_text() == content
    assert (bashrc.stat().st_uid, bashrc.stat().st_gid) == (uid, gid)


@pytest.mark.skipif(os.geteuid() != 0, reason="Requires root for real numeric ownership changes")
def test_root_bashrc_repair_does_not_chown_symlink_target(tmp_path):
    target = tmp_path / "shell-settings"
    target.write_text("export USER_SETTING=keep_me\n")
    target_owner = target.stat().st_uid, target.stat().st_gid
    home = tmp_path / "home"
    home.mkdir()
    bashrc = home / ".bashrc"
    bashrc.symlink_to(target)

    run_bashrc(home)

    assert bashrc.is_symlink()
    assert (bashrc.lstat().st_uid, bashrc.lstat().st_gid) == (1234, 5678)
    assert (target.stat().st_uid, target.stat().st_gid) == target_owner
    assert target.read_text().startswith("export USER_SETTING=keep_me\n")


@pytest.mark.skipif(os.geteuid() != 0, reason="Requires root to create a fallback home under /home")
@pytest.mark.parametrize("home_value", [None, ""])
def test_root_bashrc_home_fallback(home_value):
    with tempfile.TemporaryDirectory(prefix="bashrc-test-", dir="/home") as directory:
        home = Path(directory)
        prefix = "unset HOME\n" if home_value is None else ""
        run_bashrc("", prefix=prefix, repair_home=True, NB_USER=home.name)

        bashrc = home / ".bashrc"
        assert (bashrc.stat().st_uid, bashrc.stat().st_gid) == (1234, 5678)
