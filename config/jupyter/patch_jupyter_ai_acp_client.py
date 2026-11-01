#!/usr/bin/env python3
"""Patch two jupyter-ai-acp-client 0.3.4 behaviors at image build time.

``jupyter-ai-acp-client==0.3.4`` logs every streamed agent message chunk at
INFO — two lines per chunk, where a chunk is often a handful of characters —
plus one line per tool-call start and per once-a-second tool-call progress
tick. A single agent reply floods the Jupyter server log with thousands of
lines, burying everything else. The first workaround demotes the four
per-event log statements to DEBUG so the server log stays readable at its
default INFO level.

Its OpenCode persona also refuses every OpenCode release from 2.0 on, although
OpenCode 2 still speaks the same Agent Client Protocol through ``opencode acp``.
The second workaround admits the 2.x line the image installs.

No fixed release exists yet. Exact anchors make a future package update fail
loudly instead of silently retaining or misapplying either workaround.
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

OPENCODE_MARKER = "neurodesktop-acp-opencode-2"

OPENCODE_VERSION_BEFORE = """\
    if current_version < (1, 0, 0) or current_version[0] >= 2:
        raise PersonaRequirementsUnmet(
            f"opencode version {version_str} is installed,"
            " but version >=1.0.0,<2 is required."
"""

OPENCODE_VERSION_AFTER = f"""\
    # {OPENCODE_MARKER}
    if current_version < (1, 0, 0) or current_version[0] >= 3:
        raise PersonaRequirementsUnmet(
            f"opencode version {{version_str}} is installed,"
            " but version >=1.0.0,<3 is required."
"""

OPENCODE_SEAMS = (
    ("OpenCode version guard", OPENCODE_VERSION_BEFORE, OPENCODE_VERSION_AFTER),
)

WORKAROUNDS = (
    (
        "per-event log-level",
        MARKER,
        {"default_acp_client.py": CLIENT_SEAMS, "tool_call_manager.py": MANAGER_SEAMS},
    ),
    ("OpenCode 2 persona", OPENCODE_MARKER, {"acp_personas/opencode.py": OPENCODE_SEAMS}),
)


def installed_package_dir() -> Path:
    """Locate the installed package without importing its server extension."""
    spec = importlib.util.find_spec("jupyter_ai_acp_client")
    if spec is None or not spec.submodule_search_locations:
        raise ValueError("jupyter_ai_acp_client is not installed")
    return Path(next(iter(spec.submodule_search_locations)))


def _apply_seams(text: str, seams, workaround: str) -> str:
    for name, before, after in seams:
        if text.count(before) != 1:
            raise ValueError(
                f"{name} anchor did not match exactly once; "
                f"reassess the {workaround} workaround"
            )
        text = text.replace(before, after)
    return text


def patch_package(package_dir: Path) -> bool:
    """Patch *package_dir* and return whether files changed.

    Every anchor is checked before any file is written, so a mismatch leaves
    the package untouched.
    """
    package_dir = Path(package_dir)
    writes = {}
    for workaround, marker, files in WORKAROUNDS:
        texts = {
            name: (package_dir / name).read_text(encoding="utf-8") for name in files
        }
        patched = {marker in text for text in texts.values()}
        if patched == {True}:
            continue
        if len(patched) > 1:
            raise ValueError(
                f"partial {workaround} workaround detected; refusing to continue"
            )
        for name, seams in files.items():
            writes[package_dir / name] = _apply_seams(texts[name], seams, workaround)

    for path, text in writes.items():
        path.write_text(text, encoding="utf-8")
    return bool(writes)


def main() -> int:
    package_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else installed_package_dir()
    try:
        changed = patch_package(package_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch jupyter-ai-acp-client: {exc}", file=sys.stderr)
        return 1

    state = "applied" if changed else "already present"
    print(f"jupyter-ai-acp-client workarounds {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
