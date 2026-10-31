"""Tests for the manual prune of OpenCode sessions with deleted directories.

The databases are built from the schema OpenCode 2.2.5 leaves after migrating
an OpenCode 1 home: sessions live in ``session_v2`` and, for upgraded homes,
also in the original ``session`` table. Per-session rows cascade from either,
while ``event_sequence`` and ``job`` key off a session id with no foreign key.
"""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from testlib import load_source_module, repo_path


def _load_module():
    """Import the prune script from the image or the repository checkout."""
    return load_source_module(
        "opencode_prune_sessions",
        "/opt/neurodesktop/opencode_prune_sessions.py",
        "config/agents/opencode_prune_sessions.py",
    )


ocp = _load_module()

SCHEMA = repo_path("tests/fixtures/opencode-2.2.5-upgraded-schema.sql")
OPENCODE_1_TABLES = ("part", "message", "todo", "session_share", "session")


def _insert(con, table, **values):
    """Insert a row, filling the remaining NOT NULL columns with blanks."""
    for _, name, kind, notnull, default, _ in con.execute(f"pragma table_info({table})"):
        if notnull and default is None and name not in values:
            values[name] = 0 if kind.lower() == "integer" else ""
    columns = ", ".join(f"`{name}`" for name in values)
    marks = ", ".join("?" for _ in values)
    con.execute(f"insert into `{table}` ({columns}) values ({marks})", list(values.values()))


def _make_db(path, sessions, upgraded=True):
    """Build a database holding ``sessions`` as (id, directory) pairs."""
    con = sqlite3.connect(path)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    if not upgraded:
        for table in OPENCODE_1_TABLES:
            con.execute(f"drop table `{table}`")
    _insert(con, "project", id="prj", worktree="/")
    for index, (session_id, directory) in enumerate(sessions):
        row = {
            "id": session_id,
            "project_id": "prj",
            "directory": directory,
            "title": f"title {index}",
            "time_created": 1785000000000,
        }
        _insert(con, "session_v2", **row)
        _insert(con, "session_message", id=f"msg_{index}", session_id=session_id)
        _insert(
            con, "session_part", message_id=f"msg_{index}", session_id=session_id, ordinal=0
        )
        _insert(con, "instruction_entry", session_id=session_id, key="k")
        _insert(con, "event_sequence", aggregate_id=session_id, seq=1)
        _insert(con, "event", id=f"evt_{index}", aggregate_id=session_id, seq=1)
        _insert(con, "job", notification_id=f"job_{index}", id=f"job_{index}", session_id=session_id)
        if upgraded:
            _insert(con, "session", **row)
            _insert(con, "message", id=f"v1msg_{index}", session_id=session_id)
            _insert(con, "part", id=f"v1prt_{index}", message_id=f"v1msg_{index}", session_id=session_id)
            _insert(con, "todo", session_id=session_id, content="todo", position=0)
    _insert(con, "credential", id="cred_1", label="l", value="keep-me")
    _insert(con, "event_sequence", aggregate_id="prj", seq=1)
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


@pytest.mark.parametrize("upgraded", [True, False], ids=["upgraded", "fresh"])
def test_prune_removes_sessions_whose_directory_was_deleted(tmp_path, upgraded):
    """A deleted project directory takes its session and its rows with it."""
    live = tmp_path / "live"
    live.mkdir()
    db = tmp_path / "opencode.db"
    _make_db(db, [
        ("ses_live", str(live)),
        ("ses_gone", str(tmp_path / "deleted")),
    ], upgraded=upgraded)

    removed = ocp.prune(str(db), apply=True, log=lambda *a: None)

    assert removed == 1
    con = sqlite3.connect(db)
    for table in ("session_v2", "session") if upgraded else ("session_v2",):
        assert [r[0] for r in con.execute(f"select id from {table}")] == ["ses_live"]
    con.close()


def test_prune_cascades_to_every_row_of_the_session(tmp_path):
    """Nothing belonging to a pruned session may survive it.

    SQLite leaves ``PRAGMA foreign_keys`` off by default, so a plain DELETE
    orphans every cascading table; ``event_sequence`` and ``job`` have no
    foreign key to a session and never cascade at all.
    """
    db = tmp_path / "opencode.db"
    _make_db(db, [("ses_gone", str(tmp_path / "deleted"))])

    ocp.prune(str(db), apply=True, log=lambda *a: None)

    counts = _counts(db)
    for table in (
        "session_v2", "session_message", "session_part", "instruction_entry",
        "event", "job", "session", "message", "part", "todo",
    ):
        assert counts[table] == 0, table
    assert counts["event_sequence"] == 1, "only the session's sequence goes"
    assert counts["credential"] == 1, "unrelated tables must be untouched"
    assert counts["project"] == 1


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
    assert _counts(db)["session_v2"] == 1


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
        "select count(*) from session_v2"
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
        assert _counts(db)["session_v2"] == 1

    monkeypatch.setenv(ocp.DISABLE_ENV, "1")
    assert ocp.main(["--db", str(db), "--apply"]) == 0
    assert _counts(db)["session_v2"] == 0


def test_script_runs_standalone_as_a_dry_run(tmp_path):
    """The installed script is executable and defaults to reporting only."""
    db = tmp_path / "opencode.db"
    _make_db(db, [("ses_gone", str(tmp_path / "deleted"))])

    result = subprocess.run(
        [sys.executable, str(ocp.SOURCE_PATH), "--db", str(db)],
        capture_output=True, text=True, check=True,
    )

    assert "pass --apply" in result.stdout
    assert _counts(db)["session_v2"] == 1
    assert os.access(ocp.SOURCE_PATH, os.X_OK)


def test_startup_script_does_not_run_the_manual_prune():
    """Database maintenance must not delay startup or race a live OpenCode."""
    startup = repo_path("config/jupyter/jupyterlab_startup.sh")
    if not startup.is_file():
        pytest.skip("repository checkout not available")
    text = startup.read_text(encoding="utf-8")
    assert "opencode_prune_sessions.py" not in text


def test_dockerfile_installs_the_prune_script_executable():
    """The image must retain the manual recovery tool at its documented path."""
    text = repo_path("Dockerfile").read_text(encoding="utf-8")
    assert (
        "install -m 0755 -o root -g users "
        "/tmp/agents/opencode_prune_sessions.py "
        "/opt/neurodesktop/opencode_prune_sessions.py"
    ) in text
