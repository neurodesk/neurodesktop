"""Initialize the workspace directory used by a new Jupyter AI chat.

Jupyter AI creates each chat as a ``.chat`` file and gives ACP agents the
file's parent directory as their working directory. The terminal Codex and
OpenCode wrappers are not part of that ACP launch path, so this Jupyter
contents hook provides the equivalent editable ``AGENTS.md`` seed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


AGENTS_SOURCE = Path("/opt/AGENTS.md")
AGENTS_FILENAME = "AGENTS.md"


def _warn(contents_manager: Any, error: OSError) -> None:
    """Report a seed failure without turning it into a failed chat save."""
    log = getattr(contents_manager, "log", None)
    if log is not None:
        log.warning(
            "Unable to seed AGENTS.md for Jupyter AI chat from %s: %s",
            AGENTS_SOURCE,
            error,
        )


def seed_agents_on_chat_save(
    *, os_path: str, model: dict[str, Any], contents_manager: Any
) -> None:
    """Copy the Neurodesktop guidance beside a newly saved ``.chat`` file.

    The destination is created exclusively so simultaneous chat creations
    cannot overwrite each other or a project-authored ``AGENTS.md``. Seeding
    is deliberately fail-open: the chat remains usable if the image seed is
    unexpectedly unavailable or its workspace is not writable.
    """
    if model.get("type") != "file" or Path(os_path).suffix != ".chat":
        return

    destination = Path(os_path).parent / AGENTS_FILENAME
    try:
        guidance = AGENTS_SOURCE.read_bytes()
    except OSError as error:
        _warn(contents_manager, error)
        return

    file_descriptor: int | None = None
    created_destination = False
    try:
        file_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
        created_destination = True
        with os.fdopen(file_descriptor, "wb") as destination_file:
            file_descriptor = None
            destination_file.write(guidance)
    except FileExistsError:
        return
    except OSError as error:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if created_destination:
            try:
                destination.unlink()
            except OSError:
                pass
        _warn(contents_manager, error)
