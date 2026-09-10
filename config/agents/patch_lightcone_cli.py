#!/usr/bin/env python3
"""Adapt Lightcone 0.4.2 to the current ASTRA Tools callback contract.

Lightcone 0.4.2 is the latest stable pipeline release. It pins ASTRA Tools
0.2.11 because ASTRA 0.2.14 made ``astra init`` idempotent and added two
required callback arguments. Neurodesktop uses the current ASTRA package for
the viewer, CLI, and agent skills, so this source patch updates both the
declared dependency and Lightcone's single callback before building its wheel.

Exact anchors make a future Lightcone release fail closed so this workaround
is reviewed rather than silently carried forward.
"""

from __future__ import annotations

import sys
from pathlib import Path


MARKER = "neurodesktop-lightcone-current-astra"
DEPENDENCY_BEFORE = '    "astra-tools==0.2.11",\n'
CALLBACK_BEFORE = (
    "        astra_init.callback(directory=directory, no_git=True)  "
    "# type: ignore[misc]\n"
)
CALLBACK_AFTER = f"""\
        # {MARKER}
        astra_init.callback(
            directory=directory, no_git=True, check_only=False, as_json=False
        )
"""


def patch_source(source_dir: Path, astra_tools_version: str) -> bool:
    """Patch an unpacked Lightcone 0.4.2 source tree."""
    source_dir = Path(source_dir)
    pyproject_path = source_dir / "pyproject.toml"
    commands_path = source_dir / "src" / "lightcone" / "cli" / "commands.py"
    pyproject = pyproject_path.read_text(encoding="utf-8")
    commands = commands_path.read_text(encoding="utf-8")
    dependency_after = f'    "astra-tools=={astra_tools_version}",\n'

    marker_present = MARKER in commands
    dependency_current = dependency_after in pyproject
    if marker_present or dependency_current:
        if marker_present and dependency_current:
            return False
        raise ValueError("Lightcone source is only partially patched")

    if pyproject.count(DEPENDENCY_BEFORE) != 1:
        raise ValueError(
            "dependency anchor did not match exactly once; reassess the workaround"
        )
    if commands.count(CALLBACK_BEFORE) != 1:
        raise ValueError(
            "callback anchor did not match exactly once; reassess the workaround"
        )

    pyproject_path.write_text(
        pyproject.replace(DEPENDENCY_BEFORE, dependency_after), encoding="utf-8"
    )
    commands_path.write_text(
        commands.replace(CALLBACK_BEFORE, CALLBACK_AFTER), encoding="utf-8"
    )
    return True


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: patch_lightcone_cli.py SOURCE_DIR ASTRA_TOOLS_VERSION",
            file=sys.stderr,
        )
        return 2
    try:
        changed = patch_source(Path(sys.argv[1]), sys.argv[2])
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch lightcone-cli: {exc}", file=sys.stderr)
        return 1
    state = "applied" if changed else "already present"
    print(f"lightcone-cli current-ASTRA workaround {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
