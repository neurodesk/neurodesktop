#!/usr/bin/env python3
"""Apply Neurodesktop's anchored jupyter-server-documents workarounds.

``jupyter-server-documents==0.3.3`` can process a queued update after its
client has disconnected. The missing-client lookup then references an
unassigned local, and the exception escapes the room's background queue task.
That leaves the room connected but unable to process any later messages.

Its server-side notebook executor also bypasses JupyterLab's normal
``CodeCellModel.clearExecution()`` path, which marks a user-executed cell as
trusted. Rich output renderers such as ipywidgets are unsafe for untrusted
cells, so JupyterLab falls back to their ``text/plain`` representation.

When the outputs service is disabled for a notebook room, the same executor
appends plain Python dictionaries to the cell's CRDT output array. JupyterLab
expects every entry there to be a Y.Map and a stream output's ``text`` value to
be a Y.Text. Otherwise a following stream update fails in
``appendStreamOutput()`` before later rich output can render.

The upstream fixes have not been released. Patch all four failure seams at
image build time. Exact anchors make a future package update fail loudly
instead of silently retaining or misapplying these workarounds.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path


CLIENT_LOOKUP_MARKER = "neurodesktop-issue-271-client-lookup"
QUEUE_GUARD_MARKER = "neurodesktop-issue-271-queue-guard"
YDOC_OUTPUT_MARKER = "neurodesktop-crdt-notebook-output"
YDOC_STREAM_TEXT_MARKER = "neurodesktop-crdt-stream-text"
WIDGET_TRUST_MARKER = "neurodesktop-server-execution-trust"
WIDGET_CACHE_SAFE_MARKER = "neurodesktop-widget-cache-safe-entry"

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

YDOC_OUTPUT_BEFORE = """        else:
            output = self.transform_output(msg_type, content, ydoc=False)
"""

YDOC_OUTPUT_AFTER = f"""        else:
            # {YDOC_OUTPUT_MARKER}
            # The notebook Y.Array requires a pycrdt.Map, not a plain dict.
            output = self.transform_output(msg_type, content, ydoc=True)
"""

YDOC_IMPORT_BEFORE = "from pycrdt import Map\n"
YDOC_IMPORT_AFTER = "from pycrdt import Map, Text\n"

YDOC_STREAM_TEXT_BEFORE = '''        if msg_type == "stream":
            return factory({
                "output_type": "stream",
                "text": content["text"],
                "name": content["name"],
            })
'''

YDOC_STREAM_TEXT_AFTER = f'''        if msg_type == "stream":
            # {YDOC_STREAM_TEXT_MARKER}
            text = Text(content["text"]) if ydoc else content["text"]
            return factory({{
                "output_type": "stream",
                "text": text,
                "name": content["name"],
            }})
'''

WIDGET_TRUST_BEFORE = (
    'if("code"!==e.model.type)return"markdown"===e.model.type&&'
    '(e.rendered=!0,e.inputHidden=!1),s({cell:e,success:!0}),!0;'
    "if(!l)return!0;"
)

WIDGET_TRUST_AFTER = (
    'if("code"!==e.model.type)return"markdown"===e.model.type&&'
    '(e.rendered=!0,e.inputHidden=!1),s({cell:e,success:!0}),!0;'
    f"/*{WIDGET_TRUST_MARKER}*/e.model.trusted=!0;"
    "if(!l)return!0;"
)

HASHED_BUNDLE_NAME = re.compile(
    r"^(?P<prefix>.+)\.(?P<hash>[0-9a-f]{20})\.js$"
)


def installed_package_dir() -> Path:
    """Locate the installed package without importing its server extension."""
    spec = importlib.util.find_spec("jupyter_server_documents")
    if spec is None or not spec.submodule_search_locations:
        raise ValueError("jupyter_server_documents is not installed")
    return Path(next(iter(spec.submodule_search_locations)))


def installed_labextension_dir() -> Path:
    """Locate the installed frontend bundle paired with the Python package."""
    return (
        Path(sys.prefix)
        / "share/jupyter/labextensions/@jupyter-ai-contrib/server-documents"
    )


def patch_package(package_dir: Path) -> bool:
    """Patch *package_dir* and return whether files changed."""
    package_dir = Path(package_dir)
    clients_path = package_dir / "websockets" / "clients.py"
    yroom_path = package_dir / "rooms" / "yroom.py"
    output_processor_path = package_dir / "outputs" / "output_processor.py"
    clients_text = clients_path.read_text(encoding="utf-8")
    yroom_text = yroom_path.read_text(encoding="utf-8")
    output_processor_text = output_processor_path.read_text(encoding="utf-8")

    client_patched = CLIENT_LOOKUP_MARKER in clients_text
    queue_patched = QUEUE_GUARD_MARKER in yroom_text
    if client_patched != queue_patched:
        raise ValueError("partial issue #271 workaround detected; refusing to continue")

    issue_271_changed = not client_patched
    if issue_271_changed:
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

    output_patched = YDOC_OUTPUT_MARKER in output_processor_text
    stream_text_patched = YDOC_STREAM_TEXT_MARKER in output_processor_text
    if output_patched != stream_text_patched:
        raise ValueError(
            "partial CRDT output workaround detected; refusing to continue"
        )
    if output_patched:
        if (
            output_processor_text.count(YDOC_OUTPUT_AFTER) != 1
            or output_processor_text.count(YDOC_IMPORT_AFTER) != 1
            or output_processor_text.count(YDOC_STREAM_TEXT_AFTER) != 1
        ):
            raise ValueError(
                "CRDT output workaround is incomplete; refusing to continue"
            )
    elif (
        output_processor_text.count(YDOC_OUTPUT_BEFORE) != 1
        or output_processor_text.count(YDOC_IMPORT_BEFORE) != 1
        or output_processor_text.count(YDOC_STREAM_TEXT_BEFORE) != 1
    ):
        raise ValueError(
            "notebook output anchor did not match exactly once; "
            "reassess the CRDT output workaround"
        )

    if issue_271_changed:
        clients_path.write_text(
            clients_text.replace(CLIENT_LOOKUP_BEFORE, CLIENT_LOOKUP_AFTER),
            encoding="utf-8",
        )
        yroom_path.write_text(
            yroom_text.replace(QUEUE_GUARD_BEFORE, QUEUE_GUARD_AFTER),
            encoding="utf-8",
        )
    if not output_patched:
        output_processor_path.write_text(
            output_processor_text.replace(YDOC_IMPORT_BEFORE, YDOC_IMPORT_AFTER)
            .replace(YDOC_OUTPUT_BEFORE, YDOC_OUTPUT_AFTER)
            .replace(YDOC_STREAM_TEXT_BEFORE, YDOC_STREAM_TEXT_AFTER),
            encoding="utf-8",
        )
    return issue_271_changed or not output_patched


def patch_widget_trust(labextension_dir: Path) -> bool:
    """Make server-side execution trust code cells initiated by the user.

    Jupyter serves federated extension assets as immutable for one year. Keep
    the upstream files intact, publish the patched chunk and remote entry under
    new content-derived names, and point ``package.json`` at the new entry.
    Mutating the original hashed chunk in place would leave existing browsers
    running their cached, unpatched copy after an image update.
    """
    labextension_dir = Path(labextension_dir)
    static_dir = labextension_dir / "static"
    bundles = sorted(static_dir.glob("*.js"))
    if not bundles:
        raise ValueError("server-documents frontend bundles were not found")

    package_path = labextension_dir / "package.json"
    package_text = package_path.read_text(encoding="utf-8")
    try:
        package_data = json.loads(package_text)
        load_path = package_data["jupyterlab"]["_build"]["load"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(
            "server-documents package.json has no valid jupyterlab build entry"
        ) from exc
    if not isinstance(load_path, str) or not load_path.startswith("static/"):
        raise ValueError("server-documents remote entry path is invalid")

    remote_entry = labextension_dir / load_path
    remote_text = remote_entry.read_text(encoding="utf-8")
    texts = {path: path.read_text(encoding="utf-8") for path in bundles}
    before_paths = [
        path
        for path, text in texts.items()
        if WIDGET_TRUST_BEFORE in text
    ]
    active_marker_paths = []
    for path, text in texts.items():
        name_match = HASHED_BUNDLE_NAME.match(path.name)
        if (
            name_match
            and WIDGET_TRUST_MARKER in text
            and name_match.group("hash") in remote_text
        ):
            active_marker_paths.append(path)

    cache_safe_entry = WIDGET_CACHE_SAFE_MARKER in remote_text
    if len(active_marker_paths) == 1 and cache_safe_entry:
        return False
    clean_upstream = len(before_paths) == 1 and not active_marker_paths
    legacy_in_place_patch = (
        not before_paths
        and len(active_marker_paths) == 1
        and not cache_safe_entry
    )
    if not clean_upstream and not legacy_in_place_patch:
        raise ValueError(
            "server-side cell trust anchor did not match exactly once; "
            "reassess the widget trust workaround"
        )

    source_bundle = (
        active_marker_paths[0] if legacy_in_place_patch else before_paths[0]
    )
    source_match = HASHED_BUNDLE_NAME.match(source_bundle.name)
    remote_match = HASHED_BUNDLE_NAME.match(remote_entry.name)
    if source_match is None or remote_match is None:
        raise ValueError("server-documents frontend assets are not content hashed")

    source_hash = source_match.group("hash")
    if remote_text.count(source_hash) < 1:
        raise ValueError(
            "server-documents remote entry does not reference the executor bundle"
        )

    patched_bundle_text = texts[source_bundle]
    if clean_upstream:
        patched_bundle_text = patched_bundle_text.replace(
            WIDGET_TRUST_BEFORE, WIDGET_TRUST_AFTER
        )
    patched_bundle_hash = hashlib.sha256(
        patched_bundle_text.encode("utf-8")
    ).hexdigest()[:20]
    patched_bundle = static_dir / (
        f"{source_match.group('prefix')}.{patched_bundle_hash}.js"
    )

    patched_remote_text = (
        remote_text.replace(source_hash, patched_bundle_hash)
        + f"\n/*{WIDGET_CACHE_SAFE_MARKER}*/\n"
    )
    patched_remote_hash = hashlib.sha256(
        patched_remote_text.encode("utf-8")
    ).hexdigest()[:20]
    patched_remote_entry = static_dir / (
        f"{remote_match.group('prefix')}.{patched_remote_hash}.js"
    )
    patched_load_path = f"static/{patched_remote_entry.name}"
    if package_text.count(load_path) != 1:
        raise ValueError(
            "server-documents package.json remote entry did not match exactly once"
        )

    patched_bundle.write_text(patched_bundle_text, encoding="utf-8")
    patched_remote_entry.write_text(patched_remote_text, encoding="utf-8")
    package_path.write_text(
        package_text.replace(load_path, patched_load_path), encoding="utf-8"
    )
    return True


def main() -> int:
    package_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else installed_package_dir()
    labextension_dir = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else installed_labextension_dir()
    )
    try:
        backend_changed = patch_package(package_dir)
        frontend_changed = patch_widget_trust(labextension_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch jupyter-server-documents: {exc}", file=sys.stderr)
        return 1

    state = "applied" if backend_changed or frontend_changed else "already present"
    print(f"jupyter-server-documents workarounds {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
