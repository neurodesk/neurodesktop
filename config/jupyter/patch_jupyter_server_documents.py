#!/usr/bin/env python3
"""Apply Neurodesktop's anchored jupyter-server-documents workarounds.

``jupyter-server-documents==0.3.3`` can process a queued update after its
client has disconnected. The missing-client lookup then references an
unassigned local, and the exception escapes the room's background queue task.
That leaves the room connected but unable to process any later messages. It
also drops a SyncStep2 reply that arrives after its five-second handshake
window and disconnects the client. A stale browser then repeats its divergent
history repair, whose non-idempotent full-range clear can delete server-owned
notebook cells and autosave a one-cell blank document.

Its per-connection kernel WebSocket bridge also skips upstream
jupyter_server's connection "nudge", so a freshly connected client's ZMQ
IOPub subscription is unproven and everything the kernel publishes before it
reaches the kernel is silently lost — a widget bulk state reply can vanish
with no error anywhere. The nudge logic lives in
``neurodesktop_kernel_nudge.py`` next to this script; the anchored change in
``websocket_connection.py`` only awaits it before starting the listen tasks.

Its server-side notebook executor also bypasses JupyterLab's normal
``CodeCellModel.clearExecution()`` path, which marks a user-executed cell as
trusted. Rich output renderers such as ipywidgets are unsafe for untrusted
cells, so JupyterLab falls back to their ``text/plain`` representation.

When the outputs service is disabled for a notebook room, the same executor
appends plain Python dictionaries to the cell's CRDT output array. JupyterLab
expects every entry there to be a Y.Map and a stream output's ``text`` value to
be a Y.Text. It also expects consecutive stream messages to update that one
Y.Text. Appending a new Y.Map per fragment makes JupyterLab combine the records
locally and echo the same text back into the room, so a replay duplicates
fragments and can call ``appendStreamOutput()`` before an output exists.

The upstream fixes have not been released. Patch each failure seam at image
build time. Exact anchors make a future package update fail loudly
instead of silently retaining or misapplying these workarounds.

The stream-coalescing logic itself lives in ``neurodesktop_stream_output.py``
next to this script; the spliced methods only delegate to it. The patcher
installs that module into the package as
``jupyter_server_documents/outputs/_neurodesktop_stream.py`` so the anchored
change stays small and the algorithm stays unit-testable from a checkout.
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
LATE_SYNC_STEP2_MARKER = "neurodesktop-late-sync-step2"
YDOC_OUTPUT_MARKER = "neurodesktop-crdt-notebook-output"
YDOC_STREAM_TEXT_MARKER = "neurodesktop-crdt-stream-text"
YDOC_STREAM_MERGE_MARKER = "neurodesktop-crdt-stream-merge"
KERNEL_NUDGE_MARKER = "neurodesktop-kernel-ws-nudge"
WIDGET_TRUST_V1_MARKER = "neurodesktop-server-execution-trust"
WIDGET_TRUST_MARKER = "neurodesktop-server-execution-dispatch-trust"
WIDGET_TRUST_RESTORE_MARKER = "neurodesktop-server-execution-restore-trust"
WIDGET_CACHE_SAFE_MARKER = "neurodesktop-widget-cache-safe-entry"
DIVERGENT_REPAIR_MARKER = "neurodesktop-idempotent-divergent-repair"

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

HANDSHAKE_TIMEOUT_BEFORE = '''    inactivity_timeout = traitlets.Int(
        default_value=60,
        config=True,
        help="Number of seconds of inactivity before a room is considered inactive."
    )
    """
    Number of seconds of inactivity before a room is considered inactive.

    See `YRoom.inactive` for more details on how activity is tracked.
    """

    file_api_class = traitlets.Type(
'''

HANDSHAKE_TIMEOUT_AFTER = '''    inactivity_timeout = traitlets.Int(
        default_value=60,
        config=True,
        help="Number of seconds of inactivity before a room is considered inactive."
    )
    """
    Number of seconds of inactivity before a room is considered inactive.

    See `YRoom.inactive` for more details on how activity is tracked.
    """

    handshake_timeout = traitlets.Float(
        default_value=5.0,
        config=True,
        help=(
            "Seconds to await SyncStep2 before resuming room broadcasts. "
            "A late reply is still applied and does not disconnect the client."
        )
    )

    file_api_class = traitlets.Type(
'''

PENDING_SS2_INIT_BEFORE = '''        self._pending_ss2_future: asyncio.Future[bytes] | None = None
        self._pending_ss2_client_id: str | None = None
'''

PENDING_SS2_INIT_AFTER = '''        self._pending_ss2: dict[str, asyncio.Future[bytes]] = {}
'''

PENDING_SS2_ROUTE_BEFORE = '''        if (
            self._pending_ss2_future is not None
            and not self._pending_ss2_future.done()
            and client_id == self._pending_ss2_client_id
            and len(message) >= 2
            and message[0] == YMessageType.SYNC
            and message[1] == YSyncMessageSubtype.SYNC_STEP2
        ):
            self._pending_ss2_future.set_result(message)
            return
'''

PENDING_SS2_ROUTE_AFTER = '''        pending = self._pending_ss2.get(client_id)
        if (
            pending is not None
            and not pending.done()
            and len(message) >= 2
            and message[0] == YMessageType.SYNC
            and message[1] == YSyncMessageSubtype.SYNC_STEP2
        ):
            pending.set_result(message)
            return
'''

LATE_SYNC_STEP2_BEFORE = '''        elif sync_message_subtype == YSyncMessageSubtype.SYNC_STEP2:
            self.log.warning("Received SS2 message in message loop, this should never happen.")
            return
'''

LATE_SYNC_STEP2_AFTER = f'''        elif sync_message_subtype == YSyncMessageSubtype.SYNC_STEP2:
            # {LATE_SYNC_STEP2_MARKER}
            # A timed-out divergent repair carries its tombstones only here.
            # CRDT updates are commutative and idempotent, so a late apply is
            # safe and prevents the next reconnect from repeating the repair.
            try:
                self.handle_sync_step2(client_id, message)
                self.log.info(
                    "Applied late SyncStep2 from client '%s' in room '%s'.",
                    client_id,
                    self.room_id
                )
            except Exception:
                # handle_sync_step2 already logged the malformed message. Do
                # not let it terminate the room's shared message-queue task.
                pass
            return
'''

PENDING_SS2_CREATE_BEFORE = '''        self._pending_ss2_future = loop.create_future()
        self._pending_ss2_client_id = client_id
'''

PENDING_SS2_CREATE_AFTER = '''        self._pending_ss2[client_id] = loop.create_future()
'''

PENDING_SS2_WAIT_BEFORE = '''            ss2_message = await asyncio.wait_for(self._pending_ss2_future, timeout=5.0)
'''

PENDING_SS2_WAIT_AFTER = '''            ss2_message = await asyncio.wait_for(
                self._pending_ss2[client_id], timeout=self.handshake_timeout
            )
'''

PENDING_SS2_TIMEOUT_BEFORE = '''        except asyncio.TimeoutError:
            self.log.warning(
                "Timed out waiting for SyncStep2 reply from client '%s' in room '%s'.",
                client_id,
                self.room_id
            )
            handshake_failed = True
'''

PENDING_SS2_TIMEOUT_AFTER = '''        except asyncio.TimeoutError:
            self.log.info(
                "No SyncStep2 reply from client '%s' in room '%s' within %.1fs; "
                "resuming broadcasts. The reply will be applied when it arrives.",
                client_id,
                self.room_id,
                self.handshake_timeout
            )
'''

PENDING_SS2_CLEAR_BEFORE = '''        # Clear instance state
        self._pending_ss2_future = None
        self._pending_ss2_client_id = None
'''

PENDING_SS2_CLEAR_AFTER = '''        # Stop intercepting this client's SS2. A later reply enters the queue
        # and is applied by the late-SyncStep2 branch in handle_message().
        self._pending_ss2.pop(client_id, None)
'''

LATE_SYNC_STEP2_PATCHES = (
    (HANDSHAKE_TIMEOUT_BEFORE, HANDSHAKE_TIMEOUT_AFTER),
    (PENDING_SS2_INIT_BEFORE, PENDING_SS2_INIT_AFTER),
    (PENDING_SS2_ROUTE_BEFORE, PENDING_SS2_ROUTE_AFTER),
    (LATE_SYNC_STEP2_BEFORE, LATE_SYNC_STEP2_AFTER),
    (PENDING_SS2_CREATE_BEFORE, PENDING_SS2_CREATE_AFTER),
    (PENDING_SS2_WAIT_BEFORE, PENDING_SS2_WAIT_AFTER),
    (PENDING_SS2_TIMEOUT_BEFORE, PENDING_SS2_TIMEOUT_AFTER),
    (PENDING_SS2_CLEAR_BEFORE, PENDING_SS2_CLEAR_AFTER),
)

KERNEL_NUDGE_IMPORT_BEFORE = """from jupyter_server.services.kernels.connection.base import (
    BaseKernelWebsocketConnection,
    deserialize_msg_from_ws_v1,
    serialize_msg_to_ws_v1,
)
"""

KERNEL_NUDGE_IMPORT_AFTER = """from jupyter_server.services.kernels.connection.base import (
    BaseKernelWebsocketConnection,
    deserialize_msg_from_ws_v1,
    serialize_msg_to_ws_v1,
)

from . import _neurodesktop_kernel_nudge
"""

KERNEL_NUDGE_CONNECT_BEFORE = """        self._client.start_channels(hb=False)
        self._tasks = [
"""

KERNEL_NUDGE_CONNECT_AFTER = f"""        self._client.start_channels(hb=False)
        # {KERNEL_NUDGE_MARKER}
        # A fresh IOPub SUB socket drops everything the kernel publishes
        # before its subscription arrives. Prove the bridge end-to-end the
        # way upstream jupyter_server's connection nudge does before any
        # traffic is forwarded; the nudge bounds itself and never raises.
        await _neurodesktop_kernel_nudge.nudge(self)
        self._tasks = [
"""

NUDGE_MODULE_NAME = "_neurodesktop_kernel_nudge.py"
NUDGE_MODULE_SOURCE_NAME = "neurodesktop_kernel_nudge.py"

YDOC_OUTPUT_BEFORE = """        else:
            output = self.transform_output(msg_type, content, ydoc=False)
"""

YDOC_OUTPUT_AFTER = f"""        else:
            # {YDOC_OUTPUT_MARKER}
            # The notebook Y.Array requires a pycrdt.Map, not a plain dict.
            output = self.transform_output(msg_type, content, ydoc=True)
"""

YDOC_IMPORT_BEFORE = "from pycrdt import Map\n"
YDOC_IMPORT_AFTER = (
    "from pycrdt import Map, Text\n"
    "\n"
    "from . import _neurodesktop_stream\n"
)

STREAM_MODULE_NAME = "_neurodesktop_stream.py"
STREAM_MODULE_SOURCE_NAME = "neurodesktop_stream_output.py"

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

YDOC_STREAM_WRITE_BEFORE = '''        display_id = content.get("transient", {}).get("display_id")
'''

YDOC_STREAM_WRITE_AFTER = f'''        if msg_type == "stream" and not self.use_outputs_service:
            # {YDOC_STREAM_MERGE_MARKER}
            # A notebook room stores one CRDT stream output per contiguous
            # stdout/stderr run. Appending one map per kernel fragment makes
            # JupyterLab merge it locally and echo the fragment into Y.Text a
            # second time, which duplicates output when another client joins.
            self._write_ydoc_stream(ycell, cell_id, content)
            return

        self._discard_stream_position(cell_id)
        display_id = content.get("transient", {{}}).get("display_id")
'''

YDOC_STREAM_HELPERS_BEFORE = '''    def _clear_ycell_outputs(self, ycell, file_id: str | None, cell_id: str):
'''

YDOC_STREAM_HELPERS_AFTER = '''    def _stream_positions(self):
        positions = getattr(self, "_neurodesktop_stream_positions", None)
        if positions is None:
            positions = self._neurodesktop_stream_positions = {}
        return positions

    def _discard_stream_position(self, cell_id: str) -> None:
        self._stream_positions().pop(cell_id, None)

    def _write_ydoc_stream(self, ycell, cell_id: str, content: dict) -> None:
        positions = self._stream_positions()
        positions[cell_id] = _neurodesktop_stream.write_stream_output(
            ycell["outputs"], content, positions.get(cell_id)
        )

    def _clear_ycell_outputs(self, ycell, file_id: str | None, cell_id: str):
        self._discard_stream_position(cell_id)
'''

# Trust must be granted only once execution is actually dispatched. The
# first version of this workaround set it immediately after the non-code-cell
# early return, which is before the session and kernel guards: pressing Run
# with no kernel then marked the cell trusted although nothing executed, and
# because this path does not clear outputs the way JupyterLab's
# `clearExecution()` does, stale untrusted rich output became trusted. Anchor
# through both guards for clean and v1 detection, then inject the assignment
# inside the request try directly before makeRequest(), after request
# preparation and the execution-scheduled callback. Remember the previous
# value and restore it if the request returns 409, returns another non-success
# response, or throws. The successful path keeps the cell trusted before its
# rich outputs arrive.
TRUST_HEAD = (
    'if("code"!==e.model.type)return"markdown"===e.model.type&&'
    '(e.rendered=!0,e.inputHidden=!1),s({cell:e,success:!0}),!0;'
)

TRUST_KERNEL_GUARDS = (
    "if(!l)return!0;"
    "if(l.hasNoKernel&&await l.startKernel()&&d&&await d.selectKernel(l),"
    "l.hasNoKernel)return!0;"
)

WIDGET_TRUST_BEFORE = TRUST_HEAD + TRUST_KERNEL_GUARDS

WIDGET_TRUST_V1_AFTER = (
    TRUST_HEAD
    + f"/*{WIDGET_TRUST_V1_MARKER}*/e.model.trusted=!0;"
    + TRUST_KERNEL_GUARDS
)

WIDGET_TRUST_GUARD_AFTER = (
    TRUST_HEAD
    + TRUST_KERNEL_GUARDS
    + f"/*{WIDGET_TRUST_MARKER}*/"
    + "const neurodeskPrevTrusted=e.model.trusted;e.model.trusted=!0;"
)

# Keep every operation after the trust grant inside the request try. Building
# the request and notifying the execution observer can both throw, so granting
# trust immediately after the kernel guards still has an uncovered failure
# path. The assignment belongs directly before makeRequest(), after the
# execution-scheduled callback has completed.
TRUST_DISPATCH_BEFORE = (
    "c({cell:e});try{const n=await i.ServerConnection.makeRequest"
)

WIDGET_TRUST_AFTER = (
    "c({cell:e});"
    f"/*{WIDGET_TRUST_MARKER}*/"
    "const neurodeskPrevTrusted=e.model.trusted;"
    "try{e.model.trusted=!0;"
    "const n=await i.ServerConnection.makeRequest"
)

# A request that fails after a kernel exists must not leave the cell trusted:
# the server preserves the cell's outputs on 409 (it verifies every source
# hash before touching any state), so without this the old untrusted outputs
# would stay on screen and now be trusted.
TRUST_RESTORE_BEFORE = (
    "return 409===n.status?(o.delete(P),"
    'a.Notification.warning("Cell not executed: the cell source changed '
    'while the request was in flight. Please re-run the cell.",'
    "{autoClose:5e3}),s({cell:e,success:!1}),!1):"
    "(n.ok||o.delete(P),s({cell:e,success:n.ok}),n.ok)}"
    "catch(t){if(s({cell:e,success:!1}),!e.isDisposed)throw t;return!1}"
)

TRUST_RESTORE_AFTER = (
    f"/*{WIDGET_TRUST_RESTORE_MARKER}*/"
    "return 409===n.status?(o.delete(P),"
    "e.model.trusted=neurodeskPrevTrusted,"
    'a.Notification.warning("Cell not executed: the cell source changed '
    'while the request was in flight. Please re-run the cell.",'
    "{autoClose:5e3}),s({cell:e,success:!1}),!1):"
    "(n.ok||(o.delete(P),e.model.trusted=neurodeskPrevTrusted),"
    "s({cell:e,success:n.ok}),n.ok)}"
    "catch(t){if(e.model.trusted=neurodeskPrevTrusted,"
    "s({cell:e,success:!1}),!e.isDisposed)throw t;return!1}"
)

DIVERGENT_REPAIR_CALL_BEFORE = (
    "!function(e,t,o,n){o?e.transact(()=>{for(const[,t]of e.share)S(t);"
    "p.applyUpdate(e,t)},n):p.applyUpdate(e,t,n)}(n.doc,i,a,n)"
)

DIVERGENT_REPAIR_CALL_AFTER = (
    "!function(e,t,o,n,s){if(!o)return void p.applyUpdate(e,t,n);"
    "const r=p.decodeStateVector(s);e.transact(()=>{for(const[,t]of e.share)"
    "S(t,r);p.applyUpdate(e,t)},n)}(n.doc,i,a,n,r)"
)

DIVERGENT_REPAIR_HELPER_BEFORE = (
    "function S(e){e instanceof p.Map||(e instanceof p.Array||e instanceof "
    "p.Text||e instanceof p.XmlFragment)&&e.delete(0,e.length)}"
)

DIVERGENT_REPAIR_HELPER_AFTER = (
    f"function S(e,t){{/*{DIVERGENT_REPAIR_MARKER}*/if(e instanceof p.Map)"
    "return;if(!(e instanceof p.Array||e instanceof p.Text||e instanceof "
    "p.XmlFragment))return;const n=[];let r=0,o=e._start;for(;null!==o;){"
    "if(!o.deleted&&o.countable){const e=t.get(o.id.client)??0,i=Math.max(0,"
    "Math.min(o.length,e-o.id.clock));i<o.length&&n.push([r+i,o.length-i]),"
    "r+=o.length}o=o.right}for(let t=n.length-1;t>=0;t--)"
    "e.delete(n[t][0],n[t][1])}"
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


def stream_module_source() -> str:
    """Read the shared stream-coalescing module installed next to this script."""
    return Path(__file__).with_name(STREAM_MODULE_SOURCE_NAME).read_text(
        encoding="utf-8"
    )


def nudge_module_source() -> str:
    """Read the shared kernel-nudge module installed next to this script."""
    return Path(__file__).with_name(NUDGE_MODULE_SOURCE_NAME).read_text(
        encoding="utf-8"
    )


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
    websocket_path = package_dir / "websocket_connection.py"
    clients_text = clients_path.read_text(encoding="utf-8")
    yroom_text = yroom_path.read_text(encoding="utf-8")
    output_processor_text = output_processor_path.read_text(encoding="utf-8")
    websocket_text = websocket_path.read_text(encoding="utf-8")

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

    late_ss2_patched = LATE_SYNC_STEP2_MARKER in yroom_text
    if late_ss2_patched:
        if any(yroom_text.count(after) != 1 for _, after in LATE_SYNC_STEP2_PATCHES):
            raise ValueError(
                "late SyncStep2 workaround is incomplete; refusing to continue"
            )
    elif any(yroom_text.count(before) != 1 for before, _ in LATE_SYNC_STEP2_PATCHES):
        raise ValueError(
            "late SyncStep2 anchor did not match exactly once; "
            "reassess the handshake data-loss workaround"
        )

    nudge_module_path = package_dir / NUDGE_MODULE_NAME
    nudge_patched = KERNEL_NUDGE_MARKER in websocket_text
    nudge_import_patched = websocket_text.count(KERNEL_NUDGE_IMPORT_AFTER) == 1
    if len(
        {nudge_patched, nudge_import_patched, nudge_module_path.exists()}
    ) != 1:
        raise ValueError(
            "partial kernel websocket nudge workaround detected; "
            "refusing to continue"
        )
    if not nudge_patched and (
        websocket_text.count(KERNEL_NUDGE_IMPORT_BEFORE) != 1
        or websocket_text.count(KERNEL_NUDGE_CONNECT_BEFORE) != 1
    ):
        raise ValueError(
            "kernel websocket nudge anchor did not match exactly once; "
            "reassess the kernel websocket nudge workaround"
        )

    stream_module_path = output_processor_path.with_name(STREAM_MODULE_NAME)
    module_source = stream_module_source()
    output_patched = YDOC_OUTPUT_MARKER in output_processor_text
    stream_text_patched = YDOC_STREAM_TEXT_MARKER in output_processor_text
    stream_merge_patched = YDOC_STREAM_MERGE_MARKER in output_processor_text
    if len(
        {
            output_patched,
            stream_text_patched,
            stream_merge_patched,
            stream_module_path.exists(),
        }
    ) != 1:
        raise ValueError(
            "partial CRDT output workaround detected; refusing to continue"
        )
    if output_patched:
        if (
            output_processor_text.count(YDOC_OUTPUT_AFTER) != 1
            or output_processor_text.count(YDOC_IMPORT_AFTER) != 1
            or output_processor_text.count(YDOC_STREAM_TEXT_AFTER) != 1
            or output_processor_text.count(YDOC_STREAM_WRITE_AFTER) != 1
            or output_processor_text.count(YDOC_STREAM_HELPERS_AFTER) != 1
        ):
            raise ValueError(
                "CRDT output workaround is incomplete; refusing to continue"
            )
    elif (
        output_processor_text.count(YDOC_OUTPUT_BEFORE) != 1
        or output_processor_text.count(YDOC_IMPORT_BEFORE) != 1
        or output_processor_text.count(YDOC_STREAM_TEXT_BEFORE) != 1
        or output_processor_text.count(YDOC_STREAM_WRITE_BEFORE) != 1
        or output_processor_text.count(YDOC_STREAM_HELPERS_BEFORE) != 1
    ):
        raise ValueError(
            "notebook output anchor did not match exactly once; "
            "reassess the CRDT output workaround"
        )

    module_refreshed = (
        output_patched
        and stream_module_path.read_text(encoding="utf-8") != module_source
    )
    nudge_source = nudge_module_source()
    nudge_module_refreshed = (
        nudge_patched
        and nudge_module_path.read_text(encoding="utf-8") != nudge_source
    )
    yroom_changed = issue_271_changed or not late_ss2_patched
    if issue_271_changed:
        clients_path.write_text(
            clients_text.replace(CLIENT_LOOKUP_BEFORE, CLIENT_LOOKUP_AFTER),
            encoding="utf-8",
        )
        yroom_text = yroom_text.replace(QUEUE_GUARD_BEFORE, QUEUE_GUARD_AFTER)
    if not late_ss2_patched:
        for before, after in LATE_SYNC_STEP2_PATCHES:
            yroom_text = yroom_text.replace(before, after)
    if yroom_changed:
        yroom_path.write_text(yroom_text, encoding="utf-8")
    if not output_patched or module_refreshed:
        stream_module_path.write_text(module_source, encoding="utf-8")
    if not output_patched:
        output_processor_path.write_text(
            output_processor_text.replace(YDOC_IMPORT_BEFORE, YDOC_IMPORT_AFTER)
            .replace(YDOC_OUTPUT_BEFORE, YDOC_OUTPUT_AFTER)
            .replace(YDOC_STREAM_TEXT_BEFORE, YDOC_STREAM_TEXT_AFTER)
            .replace(YDOC_STREAM_WRITE_BEFORE, YDOC_STREAM_WRITE_AFTER)
            .replace(YDOC_STREAM_HELPERS_BEFORE, YDOC_STREAM_HELPERS_AFTER),
            encoding="utf-8",
        )
    if not nudge_patched or nudge_module_refreshed:
        nudge_module_path.write_text(nudge_source, encoding="utf-8")
    if not nudge_patched:
        websocket_path.write_text(
            websocket_text.replace(
                KERNEL_NUDGE_IMPORT_BEFORE, KERNEL_NUDGE_IMPORT_AFTER
            ).replace(KERNEL_NUDGE_CONNECT_BEFORE, KERNEL_NUDGE_CONNECT_AFTER),
            encoding="utf-8",
        )
    return (
        yroom_changed
        or not output_patched
        or module_refreshed
        or not nudge_patched
        or nudge_module_refreshed
    )


def patch_widget_trust(labextension_dir: Path) -> bool:
    """Patch cell trust and make divergent-history repair idempotent.

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
    active_executor_paths = []
    for path, text in texts.items():
        name_match = HASHED_BUNDLE_NAME.match(path.name)
        if (
            name_match
            and name_match.group("hash") in remote_text
            and any(
                anchor in text
                for anchor in (
                    WIDGET_TRUST_BEFORE,
                    WIDGET_TRUST_V1_AFTER,
                    WIDGET_TRUST_GUARD_AFTER,
                    WIDGET_TRUST_AFTER,
                    DIVERGENT_REPAIR_CALL_BEFORE,
                    DIVERGENT_REPAIR_CALL_AFTER,
                    DIVERGENT_REPAIR_HELPER_BEFORE,
                    DIVERGENT_REPAIR_HELPER_AFTER,
                )
            )
        ):
            active_executor_paths.append(path)

    cache_safe_entry = WIDGET_CACHE_SAFE_MARKER in remote_text
    if len(active_executor_paths) != 1:
        raise ValueError(
            "server-documents executor bundle did not match exactly once"
        )
    source_bundle = active_executor_paths[0]
    source_text = texts[source_bundle]

    trust_patched = source_text.count(WIDGET_TRUST_AFTER) == 1
    trust_v1 = source_text.count(WIDGET_TRUST_V1_AFTER) == 1
    trust_at_guards = source_text.count(WIDGET_TRUST_GUARD_AFTER) == 1
    trust_clean = (
        not trust_patched
        and not trust_v1
        and not trust_at_guards
        and source_text.count(WIDGET_TRUST_BEFORE) == 1
        and source_text.count(TRUST_DISPATCH_BEFORE) == 1
    )
    restore_patched = source_text.count(TRUST_RESTORE_AFTER) == 1
    restore_clean = (
        not restore_patched and source_text.count(TRUST_RESTORE_BEFORE) == 1
    )
    if restore_clean == restore_patched:
        raise ValueError(
            "server-side trust restore anchor did not match exactly once; "
            "reassess the widget trust workaround"
        )
    if (trust_patched or trust_at_guards) != restore_patched:
        raise ValueError(
            "partial server-side cell trust workaround detected; "
            "refusing to continue"
        )
    if [trust_clean, trust_v1, trust_at_guards, trust_patched].count(True) != 1:
        raise ValueError(
            "server-side cell trust anchor did not match exactly once; "
            "reassess the widget trust workaround"
        )

    repair_call_clean = source_text.count(DIVERGENT_REPAIR_CALL_BEFORE) == 1
    repair_helper_clean = source_text.count(DIVERGENT_REPAIR_HELPER_BEFORE) == 1
    repair_call_patched = source_text.count(DIVERGENT_REPAIR_CALL_AFTER) == 1
    repair_helper_patched = source_text.count(DIVERGENT_REPAIR_HELPER_AFTER) == 1
    repair_clean = repair_call_clean and repair_helper_clean
    repair_patched = repair_call_patched and repair_helper_patched
    if (
        repair_call_clean != repair_helper_clean
        or repair_call_patched != repair_helper_patched
        or repair_clean == repair_patched
    ):
        raise ValueError(
            "partial divergent-history repair detected; refusing to continue"
        )

    if trust_patched and restore_patched and repair_patched and cache_safe_entry:
        return False

    source_match = HASHED_BUNDLE_NAME.match(source_bundle.name)
    remote_match = HASHED_BUNDLE_NAME.match(remote_entry.name)
    if source_match is None or remote_match is None:
        raise ValueError("server-documents frontend assets are not content hashed")

    source_hash = source_match.group("hash")
    if remote_text.count(source_hash) < 1:
        raise ValueError(
            "server-documents remote entry does not reference the executor bundle"
        )

    patched_bundle_text = source_text
    if trust_v1:
        # Migrate an image built with the unsafe pre-guard placement.
        patched_bundle_text = patched_bundle_text.replace(
            WIDGET_TRUST_V1_AFTER, WIDGET_TRUST_BEFORE
        )
    elif trust_at_guards:
        # Migrate the intermediate placement after the kernel guards but
        # before request preparation and its execution-scheduled callback.
        patched_bundle_text = patched_bundle_text.replace(
            WIDGET_TRUST_GUARD_AFTER, WIDGET_TRUST_BEFORE
        )
    if trust_clean or trust_v1 or trust_at_guards:
        patched_bundle_text = patched_bundle_text.replace(
            TRUST_DISPATCH_BEFORE, WIDGET_TRUST_AFTER
        )
    if restore_clean:
        patched_bundle_text = patched_bundle_text.replace(
            TRUST_RESTORE_BEFORE, TRUST_RESTORE_AFTER
        )
    if repair_clean:
        patched_bundle_text = patched_bundle_text.replace(
            DIVERGENT_REPAIR_CALL_BEFORE, DIVERGENT_REPAIR_CALL_AFTER
        ).replace(
            DIVERGENT_REPAIR_HELPER_BEFORE, DIVERGENT_REPAIR_HELPER_AFTER
        )
    patched_bundle_hash = hashlib.sha256(
        patched_bundle_text.encode("utf-8")
    ).hexdigest()[:20]
    patched_bundle = static_dir / (
        f"{source_match.group('prefix')}.{patched_bundle_hash}.js"
    )

    patched_remote_text = remote_text.replace(source_hash, patched_bundle_hash)
    if not cache_safe_entry:
        patched_remote_text += f"\n/*{WIDGET_CACHE_SAFE_MARKER}*/\n"
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
