#!/usr/bin/env python3
"""Prune OpenCode sessions whose working directory no longer exists.

OpenCode keeps session history in ~/.local/share/opencode/opencode.db, not in
the working directory, and never prunes it. Deleting a project directory
therefore leaves its sessions on the Home page forever, pointing at paths that
are gone. Worse, opening one replays its stale directory back into the API
(the SPA encodes it into the URL and the ``x-opencode-directory`` header), so
dead directories keep resurfacing as session targets.

jupyterlab_startup.sh runs this once per container start. Set
NEURODESKTOP_OPENCODE_PRUNE_SESSIONS=0 to disable it.

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

# message/todo/session_share/session_message/session_input/session_context_epoch
# declare ON DELETE CASCADE from session, and part cascades from message, so
# PRAGMA foreign_keys=ON clears them all. These two key off the session id but
# declare no foreign key, so they must be swept by hand or they accumulate.
ORPHAN_TABLES = ("event", "event_sequence")


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


def stale_sessions(connection, isdir=os.path.isdir):
    """Return the session rows whose working directory is gone."""
    rows = connection.execute(
        "select id, directory, title, time_created from session"
    ).fetchall()
    return [row for row in rows if directory_is_stale(row["directory"], isdir)]


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
    """Remove sessions for deleted directories. Returns how many went.

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
        if not doomed:
            return 0

        for row in doomed:
            when = datetime.datetime.fromtimestamp(
                (row["time_created"] or 0) / 1000
            ).strftime("%Y-%m-%d")
            log(f"  {when}  {row['directory']}  {row['title']}")

        if not apply:
            log(f"[INFO] {len(doomed)} stale session(s); pass --apply to delete")
            return 0

        # A single rolling backup rather than one per run, so an unattended
        # startup cleanup cannot grow the home directory without bound.
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copy2(db_path, db_path + BACKUP_SUFFIX)
        except (OSError, sqlite3.Error) as exc:
            log(f"[WARN] could not back up {db_path}: {exc}")

        with connection:
            connection.executemany(
                "delete from session where id=?", [(row["id"],) for row in doomed]
            )
            for table in ORPHAN_TABLES:
                connection.execute(
                    f"delete from {table} "
                    "where aggregate_id not in (select id from session)"
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

    log(f"[INFO] pruned {len(doomed)} OpenCode session(s) for deleted directories")
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
