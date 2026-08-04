#!/usr/bin/env python3
"""Make jupyter_server_mcp honor FASTMCP_SHOW_SERVER_BANNER.

``jupyter-server-mcp==0.2.1`` starts FastMCP through its own embedded HTTP
runner and calls ``log_server_banner()`` unconditionally, bypassing
FastMCP's ``show_server_banner`` setting. The banner is a multi-line ASCII
box (including a hosting ad) printed into the Jupyter server log on every
boot; the image sets ``FASTMCP_SHOW_SERVER_BANNER=0`` and this patch makes
the extension respect it.

Exact anchors make a future package update fail loudly instead of silently
retaining or misapplying this workaround.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MARKER = "neurodesktop-mcp-honor-banner-setting"

BANNER_BEFORE = """\
        log_server_banner(server=self.mcp)
"""

BANNER_AFTER = f"""\
        # {MARKER}
        if fastmcp_settings.show_server_banner:
            log_server_banner(server=self.mcp)
"""

# The replacement relies on this existing import in mcp_server.py.
SETTINGS_IMPORT = "from fastmcp import settings as fastmcp_settings\n"


def installed_package_dir() -> Path:
    """Locate the installed package without importing its server extension."""
    spec = importlib.util.find_spec("jupyter_server_mcp")
    if spec is None or not spec.submodule_search_locations:
        raise ValueError("jupyter_server_mcp is not installed")
    return Path(next(iter(spec.submodule_search_locations)))


def patch_package(package_dir: Path) -> bool:
    """Patch *package_dir* and return whether files changed."""
    server_path = Path(package_dir) / "mcp_server.py"
    text = server_path.read_text(encoding="utf-8")

    if MARKER in text:
        return False

    if SETTINGS_IMPORT not in text:
        raise ValueError(
            "mcp_server.py no longer imports fastmcp settings; "
            "reassess the banner workaround"
        )
    if text.count(BANNER_BEFORE) != 1:
        raise ValueError(
            "banner anchor did not match exactly once; "
            "reassess the banner workaround"
        )

    server_path.write_text(text.replace(BANNER_BEFORE, BANNER_AFTER), encoding="utf-8")
    return True


def main() -> int:
    package_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else installed_package_dir()
    try:
        changed = patch_package(package_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch jupyter-server-mcp: {exc}", file=sys.stderr)
        return 1

    state = "applied" if changed else "already present"
    print(f"jupyter-server-mcp banner-setting workaround {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
