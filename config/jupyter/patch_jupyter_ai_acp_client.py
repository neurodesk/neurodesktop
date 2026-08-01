#!/usr/bin/env python3
"""Demote jupyter-ai-acp-client's per-event INFO logging to DEBUG.

``jupyter-ai-acp-client==0.2.1`` logs every streamed agent message chunk at
INFO — two lines per chunk, where a chunk is often a handful of characters —
plus one line per tool-call start and per once-a-second tool-call progress
tick. A single agent reply floods the Jupyter server log with thousands of
lines, burying everything else. No fixed release exists yet.

Patch the four per-event log statements down to DEBUG at image build time so
the server log stays readable at its default INFO level. Exact anchors make a
future package update fail loudly instead of silently retaining or misapplying
this workaround.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MARKER = "neurodesktop-acp-per-event-debug-log"

MESSAGE_CHUNK_BEFORE = """\
        persona.log.info(f"agent_message_chunk: {len(text)} chars")
"""

MESSAGE_CHUNK_AFTER = f"""\
        # {MARKER}
        persona.log.debug(f"agent_message_chunk: {{len(text)}} chars")
"""

SESSION_UPDATE_BEFORE = """\
            persona.log.info(f"session_update: {type(update).__name__} for session {session_id}")
"""

SESSION_UPDATE_AFTER = f"""\
            # {MARKER}
            persona.log.debug(f"session_update: {{type(update).__name__}} for session {{session_id}}")
"""

TOOL_CALL_START_BEFORE = """\
        persona.log.info(
            f"tool_call_start: id={update.tool_call_id} title={update.title!r}"
"""

TOOL_CALL_START_AFTER = f"""\
        # {MARKER}
        persona.log.debug(
            f"tool_call_start: id={{update.tool_call_id}} title={{update.title!r}}"
"""

TOOL_CALL_PROGRESS_BEFORE = """\
        persona.log.info(
            f"tool_call_progress: id={update.tool_call_id} title={update.title!r}"
"""

TOOL_CALL_PROGRESS_AFTER = f"""\
        # {MARKER}
        persona.log.debug(
            f"tool_call_progress: id={{update.tool_call_id}} title={{update.title!r}}"
"""

CLIENT_SEAMS = (
    ("agent message chunk", MESSAGE_CHUNK_BEFORE, MESSAGE_CHUNK_AFTER),
    ("session update", SESSION_UPDATE_BEFORE, SESSION_UPDATE_AFTER),
)

MANAGER_SEAMS = (
    ("tool call start", TOOL_CALL_START_BEFORE, TOOL_CALL_START_AFTER),
    ("tool call progress", TOOL_CALL_PROGRESS_BEFORE, TOOL_CALL_PROGRESS_AFTER),
)


def installed_package_dir() -> Path:
    """Locate the installed package without importing its server extension."""
    spec = importlib.util.find_spec("jupyter_ai_acp_client")
    if spec is None or not spec.submodule_search_locations:
        raise ValueError("jupyter_ai_acp_client is not installed")
    return Path(next(iter(spec.submodule_search_locations)))


def _apply_seams(text: str, seams) -> str:
    for name, before, after in seams:
        if text.count(before) != 1:
            raise ValueError(
                f"{name} anchor did not match exactly once; "
                "reassess the per-event log-level workaround"
            )
        text = text.replace(before, after)
    return text


def patch_package(package_dir: Path) -> bool:
    """Patch *package_dir* and return whether files changed."""
    package_dir = Path(package_dir)
    client_path = package_dir / "default_acp_client.py"
    manager_path = package_dir / "tool_call_manager.py"
    client_text = client_path.read_text(encoding="utf-8")
    manager_text = manager_path.read_text(encoding="utf-8")

    client_patched = MARKER in client_text
    manager_patched = MARKER in manager_text
    if client_patched and manager_patched:
        return False
    if client_patched != manager_patched:
        raise ValueError(
            "partial per-event log-level workaround detected; refusing to continue"
        )

    client_text = _apply_seams(client_text, CLIENT_SEAMS)
    manager_text = _apply_seams(manager_text, MANAGER_SEAMS)

    client_path.write_text(client_text, encoding="utf-8")
    manager_path.write_text(manager_text, encoding="utf-8")
    return True


def main() -> int:
    package_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else installed_package_dir()
    try:
        changed = patch_package(package_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch jupyter-ai-acp-client: {exc}", file=sys.stderr)
        return 1

    state = "applied" if changed else "already present"
    print(f"jupyter-ai-acp-client per-event log-level workaround {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
