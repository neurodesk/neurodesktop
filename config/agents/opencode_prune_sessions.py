#!/usr/bin/env python3
"""Prune OpenCode sessions and projects whose directory no longer exists.

OpenCode keeps session history in ~/.local/share/opencode/opencode.db, not in
the working directory, and never prunes it. Deleting a project directory
therefore leaves its sessions and project in the database, pointing at paths
that are gone, and OpenCode's unscoped session and project lists keep showing
them.

This is a manual recovery and administration tool. It is deliberately not run
at container startup: applying it writes a full database backup and runs
``VACUUM``, so a large database can take minutes and must not delay Jupyter or
contend with a newly launched OpenCode process. Set
NEURODESKTOP_OPENCODE_PRUNE_SESSIONS=0 to disable an explicit invocation.

Usage:
  opencode_prune_sessions.py              # dry run: report what would go
  opencode_prune_sessions.py --apply      # delete
  opencode_prune_sessions.py --db PATH    # operate on another database
"""

import argparse
import datetime
import os
import shutil
import sqlite3
import sys

DEFAULT_DB = "~/.local/share/opencode/opencode.db"
BACKUP_SUFFIX = ".prune-backup"
DISABLE_ENV = "NEURODESKTOP_OPENCODE_PRUNE_SESSIONS"

# OpenCode 2 keeps sessions in session_v2. A home upgraded from OpenCode 1 also
# keeps the migrated rows in the original session table, so both are pruned.
SESSION_TABLES = ("session_v2", "session")

# Every per-session table declares ON DELETE CASCADE from its session table,
# so PRAGMA foreign_keys=ON clears messages, parts, inboxes, instructions,
# schedules and blob references. These key off the session id but declare no
# foreign key, so they must be swept by hand or they accumulate. Deleting an
# event_sequence row cascades to its events.
ORPHAN_COLUMNS = (
    ("event_sequence", "aggregate_id"),
    ("event", "aggregate_id"),
    ("job", "session_id"),
    ("job", "child_session_id"),
)

# A project's sessions, permissions, directories and worktrees cascade from it,
# so a project is only removed once none of its sessions survive. Its events
# key off the project id without a foreign key.
GLOBAL_PROJECT = "global"
PROJECT_DIRECTORY_TABLES = ("project_directory", "worktree")
PROJECT_ORPHAN_COLUMNS = (
    ("event_sequence", "aggregate_id"),
    ("event", "aggregate_id"),
)


def directory_is_stale(directory, isdir=os.path.isdir):
    """Report whether ``directory`` is a deleted project directory.

    A missing directory alone is not enough. This runs at container start,
    when a bind mount may not be in place yet, and treating a not-yet-mounted
    path as deleted would destroy history for work that is perfectly alive. So
    the parent must still exist: that proves the filesystem is there and the
    directory itself really was removed. A session under an unmounted volume
    keeps its whole subtree missing and is left alone.
    """
    if not isinstance(directory, str) or not directory:
        return False
    if isdir(directory):
        return False
    parent = os.path.dirname(directory.rstrip("/")) or "/"
    return isdir(parent)


def _tables(connection):
    return {
        row[0]
        for row in connection.execute("select name from sqlite_master where type='table'")
    }


def stale_sessions(connection, isdir=os.path.isdir):
    """Return the session rows whose working directory is gone, once per id."""
    tables = _tables(connection)
    rows = {}
    for table in SESSION_TABLES:
        if table in tables:
            for row in connection.execute(
                f"select id, directory, title, time_created from {table}"
            ):
                rows.setdefault(row["id"], row)
    return [row for row in rows.values() if directory_is_stale(row["directory"], isdir)]


def stale_projects(connection, doomed_ids, isdir=os.path.isdir):
    """Return the project rows whose every directory is gone and whose
    sessions are all in ``doomed_ids``."""
    tables = _tables(connection)
    if "project" not in tables:
        return []
    directories = {}
    for row in connection.execute("select id, worktree from project"):
        directories[row["id"]] = [row["worktree"]]
    for table in PROJECT_DIRECTORY_TABLES:
        if table in tables:
            for row in connection.execute(f"select project_id, directory from {table}"):
                directories.setdefault(row["project_id"], []).append(row["directory"])
    survivors = set()
    for table in SESSION_TABLES:
        if table in tables:
            for row in connection.execute(f"select id, project_id from {table}"):
                if row["id"] not in doomed_ids:
                    survivors.add(row["project_id"])
    return [
        (project_id, paths[0])
        for project_id, paths in directories.items()
        if project_id != GLOBAL_PROJECT
        and project_id not in survivors
        and all(directory_is_stale(path, isdir) for path in paths)
    ]


def _connect(db_path):
    """Open the database with cascades on and a lock wait, or return None."""
    if not os.path.isfile(db_path):
        return None
    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def prune(db_path, apply=False, isdir=os.path.isdir, log=print):
    """Remove sessions and projects for deleted directories.

    Returns how many sessions went.

    Never raises for an absent or locked database: this runs unattended during
    startup, where failing loudly would be worse than skipping a cleanup.
    """
    try:
        connection = _connect(db_path)
    except sqlite3.Error as exc:
        log(f"[WARN] could not open {db_path}: {exc}")
        return 0
    if connection is None:
        return 0

    try:
        doomed = stale_sessions(connection, isdir)
        projects = stale_projects(connection, {row["id"] for row in doomed}, isdir)
        if not doomed and not projects:
            return 0

        for row in doomed:
            when = datetime.datetime.fromtimestamp(
                (row["time_created"] or 0) / 1000
            ).strftime("%Y-%m-%d")
            log(f"  {when}  {row['directory']}  {row['title'] or ''}")
        for _, worktree in projects:
            log(f"  project  {worktree}")

        if not apply:
            log(
                f"[INFO] {len(doomed)} stale session(s) and {len(projects)} stale "
                "project(s); pass --apply to delete"
            )
            return 0

        # A single rolling backup rather than one per run, so repeated manual
        # maintenance cannot grow the home directory without bound.
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copy2(db_path, db_path + BACKUP_SUFFIX)
        except (OSError, sqlite3.Error) as exc:
            log(f"[WARN] could not back up {db_path}: {exc}")

        ids = [(row["id"],) for row in doomed]
        tables = _tables(connection)
        with connection:
            for table in SESSION_TABLES:
                if table in tables:
                    connection.executemany(f"delete from {table} where id=?", ids)
            for table, column in ORPHAN_COLUMNS:
                if table in tables:
                    connection.executemany(
                        f"delete from {table} where {column}=?", ids
                    )
            project_ids = [(project_id,) for project_id, _ in projects]
            connection.executemany("delete from project where id=?", project_ids)
            for table, column in PROJECT_ORPHAN_COLUMNS:
                if table in tables:
                    connection.executemany(
                        f"delete from {table} where {column}=?", project_ids
                    )
    except sqlite3.Error as exc:
        log(f"[WARN] could not prune {db_path}: {exc}")
        return 0
    finally:
        connection.close()

    # VACUUM needs its own connection outside any transaction; without it the
    # file keeps its high-water mark however many rows were removed.
    try:
        vacuum = sqlite3.connect(db_path, timeout=30)
        vacuum.isolation_level = None
        vacuum.execute("VACUUM")
        vacuum.close()
    except sqlite3.Error as exc:
        log(f"[WARN] could not vacuum {db_path}: {exc}")

    log(
        f"[INFO] pruned {len(doomed)} OpenCode session(s) and {len(projects)} "
        "project(s) for deleted directories"
    )
    return len(doomed)


def main(argv=None):
    """Parse arguments and run the prune."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help="database path")
    parser.add_argument(
        "--apply", action="store_true", help="delete (default is a dry run)"
    )
    args = parser.parse_args(argv)

    if os.environ.get(DISABLE_ENV, "1").strip().lower() in (
        "0", "false", "no", "off"
    ):
        return 0

    prune(os.path.expanduser(args.db), apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
