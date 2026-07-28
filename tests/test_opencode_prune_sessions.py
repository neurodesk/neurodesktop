"""Tests for the startup prune of OpenCode sessions with deleted directories.

The synthetic database mirrors the real opencode.db shape that matters here:
the cascading foreign keys from ``session``, the chained cascade from
``message`` to ``part``, and the ``event``/``event_sequence`` tables that key
off a session id but declare no foreign key at all.
"""

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    """Import the prune script from the image or the repository checkout."""
    for candidate in (
        Path("/opt/neurodesktop/opencode_prune_sessions.py"),
        REPO_ROOT / "config/agents/opencode_prune_sessions.py",
    ):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(
                "opencode_prune_sessions", candidate
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.SOURCE_PATH = candidate
            return module
    raise AssertionError("opencode_prune_sessions.py not found")


ocp = _load_module()

SCHEMA = """
CREATE TABLE session (
    id TEXT PRIMARY KEY, directory TEXT, title TEXT, time_created INTEGER
);
CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES session(id) ON DELETE CASCADE
);
CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT REFERENCES message(id) ON DELETE CASCADE,
    session_id TEXT
);
CREATE TABLE todo (
    session_id TEXT REFERENCES session(id) ON DELETE CASCADE, content TEXT
);
CREATE TABLE event (aggregate_id TEXT, type TEXT);
CREATE TABLE event_sequence (aggregate_id TEXT, seq INTEGER);
CREATE TABLE credential (id TEXT PRIMARY KEY, value TEXT);
"""


def _make_db(path, sessions):
    """Build a database holding ``sessions`` as (id, directory) pairs."""
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for index, (session_id, directory) in enumerate(sessions):
        con.execute(
            "insert into session values (?,?,?,?)",
            (session_id, directory, f"title {index}", 1785000000000),
        )
        con.execute("insert into message values (?,?)", (f"msg_{index}", session_id))
        con.execute(
            "insert into part values (?,?,?)",
            (f"prt_{index}", f"msg_{index}", session_id),
        )
        con.execute("insert into todo values (?,?)", (session_id, "todo"))
        con.execute("insert into event values (?,?)", (session_id, "session.created"))
        con.execute("insert into event_sequence values (?,?)", (session_id, 1))
    con.execute("insert into credential values ('cred_1','keep-me')")
    con.commit()
    con.close()


def _counts(path):
    """Return row counts for every table in the database."""
    con = sqlite3.connect(path)
    tables = [
        r[0] for r in con.execute("select name from sqlite_master where type='table'")
    ]
    counts = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in tables}
    con.close()
    return counts


def test_prune_removes_sessions_whose_directory_was_deleted(tmp_path):
    """A deleted project directory takes its session and its rows with it."""
    live = tmp_path / "live"
    live.mkdir()
    db = tmp_path / "opencode.db"
    _make_db(db, [
        ("ses_live", str(live)),
        ("ses_gone", str(tmp_path / "deleted")),
    ])

    removed = ocp.prune(str(db), apply=True, log=lambda *a: None)

    assert removed == 1
    counts = _counts(db)
    assert counts["session"] == 1
    con = sqlite3.connect(db)
    assert [r[0] for r in con.execute("select id from session")] == ["ses_live"]
    con.close()


def test_prune_cascades_to_messages_parts_todos_and_events(tmp_path):
    """Nothing belonging to a pruned session may survive it.

    SQLite leaves ``PRAGMA foreign_keys`` off by default, so a plain DELETE
    orphans every cascading table; ``event``/``event_sequence`` have no foreign
    key and never cascade at all.
    """
    db = tmp_path / "opencode.db"
    _make_db(db, [("ses_gone", str(tmp_path / "deleted"))])

    ocp.prune(str(db), apply=True, log=lambda *a: None)

    counts = _counts(db)
    assert counts["session"] == 0
    assert counts["message"] == 0
    assert counts["part"] == 0, "part cascades through message"
    assert counts["todo"] == 0
    assert counts["event"] == 0, "event has no foreign key; must be swept"
    assert counts["event_sequence"] == 0
    assert counts["credential"] == 1, "unrelated tables must be untouched"


def test_prune_keeps_sessions_under_an_unmounted_volume(tmp_path):
    """A whole missing subtree means "not mounted", not "deleted".

    This runs at container start, where a bind mount may not be in place yet.
    Treating a not-yet-mounted path as deleted would destroy the history of
    work that is perfectly alive, so the parent must still exist to prove the
    directory itself was removed.
    """
    db = tmp_path / "opencode.db"
    _make_db(db, [("ses_unmounted", str(tmp_path / "not-mounted" / "project"))])

    removed = ocp.prune(str(db), apply=True, log=lambda *a: None)

    assert removed == 0
    assert _counts(db)["session"] == 1


def test_directory_is_stale_requires_a_missing_dir_with_a_live_parent():
    """The staleness rule itself, independent of any database."""
    existing = {"/home/jovyan", "/home/jovyan/live", "/tmp"}
    isdir = existing.__contains__

    assert ocp.directory_is_stale("/home/jovyan/gone", isdir)
    assert ocp.directory_is_stale("/home/jovyan/gone/", isdir)
    assert not ocp.directory_is_stale("/home/jovyan/live", isdir)
    assert not ocp.directory_is_stale("/mnt/unmounted/project", isdir)
    assert not ocp.directory_is_stale("", isdir)
    assert not ocp.directory_is_stale(None, isdir)


def test_dry_run_is_the_default_and_deletes_nothing(tmp_path):
    """Running without --apply reports but never removes."""
    db = tmp_path / "opencode.db"
    _make_db(db, [("ses_gone", str(tmp_path / "deleted"))])
    before = _counts(db)

    removed = ocp.prune(str(db), apply=False, log=lambda *a: None)

    assert removed == 0
    assert _counts(db) == before


def test_prune_writes_a_single_rolling_backup(tmp_path):
    """Unattended startup runs must not grow the home directory unbounded."""
    db = tmp_path / "opencode.db"
    _make_db(db, [
        ("ses_a", str(tmp_path / "deleted_a")),
        ("ses_b", str(tmp_path / "deleted_b")),
    ])

    ocp.prune(str(db), apply=True, log=lambda *a: None)
    backup = Path(str(db) + ocp.BACKUP_SUFFIX)
    assert backup.is_file()
    assert sqlite3.connect(backup).execute(
        "select count(*) from session"
    ).fetchone()[0] == 2

    _make_db(tmp_path / "second.db", [("ses_c", str(tmp_path / "deleted_c"))])
    ocp.prune(str(db), apply=True, log=lambda *a: None)
    assert len(list(tmp_path.glob("*" + ocp.BACKUP_SUFFIX))) == 1


def test_missing_or_unreadable_database_is_a_quiet_no_op(tmp_path):
    """Startup must not fail because OpenCode has never run."""
    assert ocp.prune(str(tmp_path / "absent.db"), apply=True, log=lambda *a: None) == 0

    not_a_db = tmp_path / "garbage.db"
    not_a_db.write_text("not a database", encoding="utf-8")
    assert ocp.prune(str(not_a_db), apply=True, log=lambda *a: None) == 0


def test_disable_env_var_skips_the_prune_entirely(tmp_path, monkeypatch):
    """NEURODESKTOP_OPENCODE_PRUNE_SESSIONS=0 keeps every session."""
    db = tmp_path / "opencode.db"
    _make_db(db, [("ses_gone", str(tmp_path / "deleted"))])

    for value in ("0", "false", "no", "off"):
        monkeypatch.setenv(ocp.DISABLE_ENV, value)
        assert ocp.main(["--db", str(db), "--apply"]) == 0
        assert _counts(db)["session"] == 1

    monkeypatch.setenv(ocp.DISABLE_ENV, "1")
    assert ocp.main(["--db", str(db), "--apply"]) == 0
    assert _counts(db)["session"] == 0


def test_script_runs_standalone_as_a_dry_run(tmp_path):
    """The installed script is executable and defaults to reporting only."""
    db = tmp_path / "opencode.db"
    _make_db(db, [("ses_gone", str(tmp_path / "deleted"))])

    result = subprocess.run(
        [sys.executable, str(ocp.SOURCE_PATH), "--db", str(db)],
        capture_output=True, text=True, check=True,
    )

    assert "pass --apply" in result.stdout
    assert _counts(db)["session"] == 1
    assert os.access(ocp.SOURCE_PATH, os.X_OK)


def test_startup_script_runs_the_prune():
    """jupyterlab_startup.sh must actually invoke it, with --apply."""
    startup = REPO_ROOT / "config/jupyter/jupyterlab_startup.sh"
    if not startup.is_file():
        pytest.skip("repository checkout not available")
    text = startup.read_text(encoding="utf-8")
    assert "/opt/neurodesktop/opencode_prune_sessions.py --apply" in text
    assert "-x /opt/neurodesktop/opencode_prune_sessions.py" in text


def test_dockerfile_installs_the_prune_script_executable():
    """The image must ship it at the path jupyterlab_startup.sh calls."""
    dockerfile = REPO_ROOT / "Dockerfile"
    if not dockerfile.is_file():
        dockerfile = Path("/opt/tests/Dockerfile")
    if not dockerfile.is_file():
        pytest.skip("Dockerfile not available")
    text = dockerfile.read_text(encoding="utf-8")
    assert (
        "install -m 0755 -o root -g users "
        "/tmp/agents/opencode_prune_sessions.py "
        "/opt/neurodesktop/opencode_prune_sessions.py"
    ) in text
