#!/usr/bin/env python3
"""Keep ipywidgets control-state replies on the requesting comm.

``ipywidgets==8.1.9`` stores the most recently opened widget control comm on
the ``Widget`` class. Every registered callback then sends through that shared
reference. If two JupyterLab clients restore widgets at the same time, a
request received on the first comm can therefore send its response to the
second client. The first widget manager never finishes restoring and its
outputs remain at ``Loading widget...``.

Capture the originating comm in each callback while retaining the class
attribute for callers that invoke the handler directly. Exact anchors make a
future package update fail loudly so this workaround is reassessed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MARKER = "neurodesktop-widget-control-comm-routing"

REGISTER_BEFORE = """\
        cls._control_comm = comm
        cls._control_comm.on_msg(cls._handle_control_comm_msg)
"""

REGISTER_AFTER = """\
        cls._control_comm = comm
        comm.on_msg(
            lambda msg: cls._handle_control_comm_msg(msg, control_comm=comm)
        )
"""

HANDLER_BEFORE = """\
    @classmethod
    def _handle_control_comm_msg(cls, msg):
        # This shouldn't happen unless someone calls this method manually
        if cls._control_comm is None:
            raise RuntimeError('Control comm has not been properly opened')

        data = msg['content']['data']
"""

HANDLER_AFTER = f"""\
    @classmethod
    def _handle_control_comm_msg(cls, msg, control_comm=None):
        # {MARKER}
        if control_comm is None:
            control_comm = cls._control_comm
        if control_comm is None:
            raise RuntimeError('Control comm has not been properly opened')

        data = msg['content']['data']
"""

SEND_BEFORE = "            cls._control_comm.send(dict(\n"
SEND_AFTER = "            control_comm.send(dict(\n"


def installed_package_dir() -> Path:
    """Locate ipywidgets without importing its widget registry."""
    spec = importlib.util.find_spec("ipywidgets")
    if spec is None or not spec.submodule_search_locations:
        raise ValueError("ipywidgets is not installed")
    return Path(next(iter(spec.submodule_search_locations)))


def patch_package(package_dir: Path) -> bool:
    """Patch *package_dir* and return whether files changed."""
    widget_path = Path(package_dir) / "widgets" / "widget.py"
    text = widget_path.read_text(encoding="utf-8")

    if MARKER in text:
        return False

    anchors = (
        ("control comm registration", REGISTER_BEFORE, REGISTER_AFTER),
        ("control message handler", HANDLER_BEFORE, HANDLER_AFTER),
        ("control state reply", SEND_BEFORE, SEND_AFTER),
    )
    for name, before, _ in anchors:
        if text.count(before) != 1:
            raise ValueError(
                f"{name} anchor did not match exactly once; "
                "reassess the widget control-comm workaround"
            )

    for _, before, after in anchors:
        text = text.replace(before, after)
    compile(text, str(widget_path), "exec")
    widget_path.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    package_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else installed_package_dir()
    try:
        changed = patch_package(package_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch ipywidgets: {exc}", file=sys.stderr)
        return 1

    state = "applied" if changed else "already present"
    print(f"ipywidgets control-comm workaround {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
