#!/usr/bin/env python3
"""Bound jupyter-server-proxy's buffered HTTP clients to 1024 MiB.

``jupyter-server-proxy==4.5.0`` constructs ``SimpleAsyncHTTPClient`` directly
for Unix sockets. That bypasses the ``AsyncHTTPClient`` factory defaults set by
Neurodesktop. JupyterHub also replaces those mutable factory defaults later in
single-user startup, which returns both proxy branches to Tornado's 100 MiB
default.

Patch both branches to pass the bounded limits directly while keeping the Unix
branch on the configured factory and preserving its ``UnixResolver``. Exact
anchors make a future package update fail loudly rather than silently retaining
or misapplying this workaround.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


UNIX_MARKER = "neurodesktop-bounded-unix-http-client"
TCP_MARKER = "neurodesktop-bounded-tcp-http-client"

IMPORT_BEFORE = "from tornado.simple_httpclient import SimpleAsyncHTTPClient\n"

UNIX_CLIENT_BEFORE = """            client = SimpleAsyncHTTPClient(
                force_instance=True, resolver=UnixResolver(self.unix_socket)
            )
"""

UNIX_CLIENT_AFTER = f"""            # {UNIX_MARKER}
            client = httpclient.AsyncHTTPClient(
                force_instance=True,
                resolver=UnixResolver(self.unix_socket),
                max_buffer_size=1024 * 1024 * 1024,
                max_body_size=1024 * 1024 * 1024,
            )
"""

TCP_CLIENT_BEFORE = """        else:
            client = httpclient.AsyncHTTPClient(force_instance=True)
"""

TCP_CLIENT_AFTER = f"""        else:
            # {TCP_MARKER}
            client = httpclient.AsyncHTTPClient(
                force_instance=True,
                max_buffer_size=1024 * 1024 * 1024,
                max_body_size=1024 * 1024 * 1024,
            )
"""


def installed_package_dir() -> Path:
    """Locate the installed package without importing its server extension."""
    spec = importlib.util.find_spec("jupyter_server_proxy")
    if spec is None or not spec.submodule_search_locations:
        raise ValueError("jupyter_server_proxy is not installed")
    return Path(next(iter(spec.submodule_search_locations)))


def patch_package(package_dir: Path) -> bool:
    """Patch *package_dir* and return whether ``handlers.py`` changed."""
    handlers_path = Path(package_dir) / "handlers.py"
    handlers_text = handlers_path.read_text(encoding="utf-8")

    markers_present = (
        UNIX_MARKER in handlers_text,
        TCP_MARKER in handlers_text,
    )
    if any(markers_present):
        if not all(markers_present):
            raise ValueError(
                "partial bounded-client workaround detected; refusing to continue"
            )
        if (
            IMPORT_BEFORE in handlers_text
            or UNIX_CLIENT_BEFORE in handlers_text
            or TCP_CLIENT_BEFORE in handlers_text
            or handlers_text.count(UNIX_CLIENT_AFTER) != 1
            or handlers_text.count(TCP_CLIENT_AFTER) != 1
        ):
            raise ValueError(
                "inconsistent bounded-client workaround detected; refusing to continue"
            )
        return False

    if handlers_text.count(IMPORT_BEFORE) != 1:
        raise ValueError(
            "SimpleAsyncHTTPClient import anchor did not match exactly once; "
            "reassess the Unix-socket client workaround"
        )
    if handlers_text.count(UNIX_CLIENT_BEFORE) != 1:
        raise ValueError(
            "Unix-socket client anchor did not match exactly once; "
            "reassess the bounded-client workaround"
        )
    if handlers_text.count(TCP_CLIENT_BEFORE) != 1:
        raise ValueError(
            "TCP client anchor did not match exactly once; "
            "reassess the bounded-client workaround"
        )

    handlers_text = handlers_text.replace(IMPORT_BEFORE, "")
    handlers_text = handlers_text.replace(UNIX_CLIENT_BEFORE, UNIX_CLIENT_AFTER)
    handlers_text = handlers_text.replace(TCP_CLIENT_BEFORE, TCP_CLIENT_AFTER)
    handlers_path.write_text(handlers_text, encoding="utf-8")
    return True


def main() -> int:
    package_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else installed_package_dir()
    try:
        changed = patch_package(package_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch jupyter-server-proxy: {exc}", file=sys.stderr)
        return 1

    state = "applied" if changed else "already present"
    print(f"jupyter-server-proxy bounded-client workaround {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
