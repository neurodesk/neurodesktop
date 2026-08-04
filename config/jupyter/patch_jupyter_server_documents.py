#!/usr/bin/env python3
"""Apply Neurodesktop's anchored workaround for upstream issue #271.

``jupyter-server-documents==0.3.2`` can process a queued update after its
client has disconnected. The missing-client lookup then references an
unassigned local, and the exception escapes the room's background queue task.
That leaves the room connected but unable to process any later messages.

The upstream fix has not been released. Patch both failure seams at image build
time: make the lookup raise its intended exception and keep the room queue alive
after one bad message. Exact anchors make a future package update fail loudly
instead of silently retaining or misapplying this workaround.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


CLIENT_LOOKUP_MARKER = "neurodesktop-issue-271-client-lookup"
QUEUE_GUARD_MARKER = "neurodesktop-issue-271-queue-guard"

CLIENT_LOOKUP_BEFORE = """        if client_id in self.desynced: 
            client = self.desynced[client_id]
        if client_id in self.synced:
            client = self.synced[client_id]
        if client.websocket and client.websocket.ws_connection:
            return client
"""

CLIENT_LOOKUP_AFTER = f"""        # {CLIENT_LOOKUP_MARKER}
        client = self.synced.get(client_id) or self.desynced.get(client_id)
        if client and client.websocket and client.websocket.ws_connection:
            return client
"""

QUEUE_GUARD_BEFORE = """            client_id, message = queue_item
            await self.handle_message(client_id, message)
            
            # Finally, inform the asyncio Queue that the task was complete
            # This is required for `self._message_queue.join()` to unblock once
            # queue is empty in `self.stop()`.
            self._message_queue.task_done()
"""

QUEUE_GUARD_AFTER = f"""            client_id, message = queue_item
            try:
                await self.handle_message(client_id, message)
            except Exception:
                # {QUEUE_GUARD_MARKER}
                # One stale client frame must not terminate this room's queue.
                self.log.exception(
                    f"Error handling message from client '{{client_id}}' in "
                    f"YRoom '{{self.room_id}}'. Skipping this message."
                )
            finally:
                # Always release Queue.join(), including for a rejected frame.
                self._message_queue.task_done()
"""


def installed_package_dir() -> Path:
    """Locate the installed package without importing its server extension."""
    spec = importlib.util.find_spec("jupyter_server_documents")
    if spec is None or not spec.submodule_search_locations:
        raise ValueError("jupyter_server_documents is not installed")
    return Path(next(iter(spec.submodule_search_locations)))


def patch_package(package_dir: Path) -> bool:
    """Patch *package_dir* and return whether files changed."""
    package_dir = Path(package_dir)
    clients_path = package_dir / "websockets" / "clients.py"
    yroom_path = package_dir / "rooms" / "yroom.py"
    clients_text = clients_path.read_text(encoding="utf-8")
    yroom_text = yroom_path.read_text(encoding="utf-8")

    client_patched = CLIENT_LOOKUP_MARKER in clients_text
    queue_patched = QUEUE_GUARD_MARKER in yroom_text
    if client_patched and queue_patched:
        return False
    if client_patched != queue_patched:
        raise ValueError("partial issue #271 workaround detected; refusing to continue")

    if clients_text.count(CLIENT_LOOKUP_BEFORE) != 1:
        raise ValueError(
            "client lookup anchor did not match exactly once; "
            "reassess the upstream issue #271 workaround"
        )
    if yroom_text.count(QUEUE_GUARD_BEFORE) != 1:
        raise ValueError(
            "message queue anchor did not match exactly once; "
            "reassess the upstream issue #271 workaround"
        )

    clients_path.write_text(
        clients_text.replace(CLIENT_LOOKUP_BEFORE, CLIENT_LOOKUP_AFTER),
        encoding="utf-8",
    )
    yroom_path.write_text(
        yroom_text.replace(QUEUE_GUARD_BEFORE, QUEUE_GUARD_AFTER),
        encoding="utf-8",
    )
    return True


def main() -> int:
    package_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else installed_package_dir()
    try:
        changed = patch_package(package_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch jupyter-server-documents: {exc}", file=sys.stderr)
        return 1

    state = "applied" if changed else "already present"
    print(f"jupyter-server-documents issue #271 workaround {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
