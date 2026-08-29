"""Build-time workaround contract for pinned server-documents defects."""

import json
import subprocess

import pytest

from testlib import load_source_module, repo_path


CLIENTS_SOURCE = '''class YjsClientGroup:
    def get(self, client_id: str) -> YjsClient:
        """
        Gets a client from its ID.
        """
        if client_id in self.desynced: 
            client = self.desynced[client_id]
        if client_id in self.synced:
            client = self.synced[client_id]
        if client.websocket and client.websocket.ws_connection:
            return client
        error_message = f"The client_id '{client_id}' is not found in client group in room '{self.room_id}'"
        self.log.error(error_message)
        raise Exception(error_message)
'''

YROOM_SOURCE = '''class YRoom:
    inactivity_timeout = traitlets.Int(
        default_value=60,
        config=True,
        help="Number of seconds of inactivity before a room is considered inactive."
    )
    """
    Number of seconds of inactivity before a room is considered inactive.

    See `YRoom.inactive` for more details on how activity is tracked.
    """

    file_api_class = traitlets.Type(
        klass=YRoomFileAPI,
        help="The `YRoomFileAPI` class.",
        default_value=YRoomFileAPI,
        config=True,
    )

    def __init__(self, *args, **kwargs):
        self._stopped = False
        self._pending_ss2_future: asyncio.Future[bytes] | None = None
        self._pending_ss2_client_id: str | None = None
        self._save_task = None

    def add_message(self, client_id: str, message: bytes) -> None:
        """
        Adds new message to the message queue. Items placed in the message queue
        are handled one-at-a-time.

        If handle_sync_step1 is awaiting an SS2 reply from a client, the reply
        bypasses the queue and resolves the pending future directly.
        """
        if (
            self._pending_ss2_future is not None
            and not self._pending_ss2_future.done()
            and client_id == self._pending_ss2_client_id
            and len(message) >= 2
            and message[0] == YMessageType.SYNC
            and message[1] == YSyncMessageSubtype.SYNC_STEP2
        ):
            self._pending_ss2_future.set_result(message)
            return

        self._message_queue.put_nowait((client_id, message))

    async def _process_message_queue(self) -> None:
        while True:
            queue_item = await self._message_queue.get()
            if queue_item is None:
                break

            client_id, message = queue_item
            await self.handle_message(client_id, message)
            
            # Finally, inform the asyncio Queue that the task was complete
            # This is required for `self._message_queue.join()` to unblock once
            # queue is empty in `self.stop()`.
            self._message_queue.task_done()

    async def handle_message(self, client_id: str, message: bytes) -> None:
        if sync_message_subtype == YSyncMessageSubtype.SYNC_STEP1:
            await self.handle_sync(client_id, message)
        elif sync_message_subtype == YSyncMessageSubtype.SYNC_STEP2:
            self.log.warning("Received SS2 message in message loop, this should never happen.")
            return
        elif sync_message_subtype == YSyncMessageSubtype.SYNC_UPDATE:
            self.handle_sync_update(client_id, message)

    async def handle_sync(self, client_id: str, ss1_message: bytes) -> None:
        loop = asyncio.get_running_loop()
        self._pending_ss2_future = loop.create_future()
        self._pending_ss2_client_id = client_id

        self.log.info("Initiating handshake with client '%s' in room '%s'.", client_id, self.room_id)
        handshake_failed = False
        try:
            self.handle_sync_step1(client_id, ss1_message)
            ss2_message = await asyncio.wait_for(self._pending_ss2_future, timeout=5.0)
            self.handle_sync_step2(client_id, ss2_message)
            self.log.info("Completed handshake with client '%s' in room '%s'.", client_id, self.room_id)
        except asyncio.TimeoutError:
            self.log.warning(
                "Timed out waiting for SyncStep2 reply from client '%s' in room '%s'.",
                client_id,
                self.room_id
            )
            handshake_failed = True
        except Exception:
            self.log.exception("Exception raised during sync handshake with client '%s' in room '%s':", client_id, self.room_id)
            handshake_failed = True

        self.update_channel.resume(pre_sync_sv=pre_sync_sv)

        # Clear instance state
        self._pending_ss2_future = None
        self._pending_ss2_client_id = None

        # Cut the connection.
        if handshake_failed:
            self.log.error("Disconnecting client '%s' due to failed sync handshake in room '%s'.", client_id, self.room_id)
            self.clients.remove(client_id)
'''

WEBSOCKET_SOURCE = '''"""
Per-connection kernel WebSocket bridge.
"""
import asyncio
import typing as t

from tornado.websocket import WebSocketClosedError
from jupyter_server.services.kernels.connection.base import (
    BaseKernelWebsocketConnection,
    deserialize_msg_from_ws_v1,
    serialize_msg_to_ws_v1,
)


class KernelWebsocketConnection(BaseKernelWebsocketConnection):
    """WebSocket bridge that owns its own AsyncKernelClient per connection."""

    kernel_ws_protocol = "v1.kernel.websocket.jupyter.org"

    _client: t.Any = None
    _tasks: t.List[asyncio.Task] = []

    async def connect(self) -> None:
        self._client = self.kernel_manager.client()
        self._client.load_connection_info(self.kernel_manager.get_connection_info())
        self._client.start_channels(hb=False)
        self._tasks = [
            asyncio.create_task(self._listen(ch))
            for ch in ("shell", "control", "stdin", "iopub")
        ]

    def disconnect(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        if self._client is not None:
            self._client.stop_channels()
            self._client = None
'''

OUTPUT_PROCESSOR_SOURCE = '''from pycrdt import Map


class OutputProcessor:
    def _write_output(
        self,
        msg_type: str,
        ycell,
        file_id: str | None,
        cell_id: str,
        content: dict,
    ):
        display_id = content.get("transient", {}).get("display_id")

        if self.use_outputs_service and file_id:
            output = self.transform_output(msg_type, content, ydoc=False)
            output = self.outputs_manager.write(
                file_id=file_id,
                cell_id=cell_id,
                output=output,
            )
        else:
            output = self.transform_output(msg_type, content, ydoc=False)

        if output is None:
            return

        outputs = ycell["outputs"]
        outputs.append(output)

    def _clear_ycell_outputs(self, ycell, file_id: str | None, cell_id: str):
        del ycell["outputs"][:]
        if self.use_outputs_service and file_id:
            self.outputs_manager.clear(file_id=file_id, cell_id=cell_id)

    def transform_output(self, msg_type: str, content: dict, ydoc: bool = False):
        factory = Map if ydoc else (lambda x: x)
        if msg_type == "stream":
            return factory({
                "output_type": "stream",
                "text": content["text"],
                "name": content["name"],
            })
        return None
'''


def load_patcher_module():
    return load_source_module(
        "jupyter_server_documents_patch",
        "/opt/neurodesktop/patch_jupyter_server_documents.py",
        "config/jupyter/patch_jupyter_server_documents.py",
    )


def write_upstream_fixture(package_dir):
    websocket_dir = package_dir / "websockets"
    rooms_dir = package_dir / "rooms"
    outputs_dir = package_dir / "outputs"
    websocket_dir.mkdir(parents=True)
    rooms_dir.mkdir(parents=True)
    outputs_dir.mkdir(parents=True)
    (websocket_dir / "clients.py").write_text(CLIENTS_SOURCE, encoding="utf-8")
    (rooms_dir / "yroom.py").write_text(YROOM_SOURCE, encoding="utf-8")
    (outputs_dir / "output_processor.py").write_text(
        OUTPUT_PROCESSOR_SOURCE, encoding="utf-8"
    )
    (package_dir / "websocket_connection.py").write_text(
        WEBSOCKET_SOURCE, encoding="utf-8"
    )


def write_frontend_fixture(labextension_dir, source, *, include_repair=True):
    static_dir = labextension_dir / "static"
    static_dir.mkdir(parents=True)
    bundle = static_dir / "278.aaaaaaaaaaaaaaaaaaaa.js"
    if include_repair:
        source += "\n" + patcher_source_divergent_repair_before()
    bundle.write_text(source, encoding="utf-8")
    remote_entry = static_dir / "remoteEntry.bbbbbbbbbbbbbbbbbbbb.js"
    remote_entry.write_text(
        'T.u=e=>e+"."+{278:"aaaaaaaaaaaaaaaaaaaa"}[e]+".js?v="+'
        '{278:"aaaaaaaaaaaaaaaaaaaa"}[e]',
        encoding="utf-8",
    )
    package_json = labextension_dir / "package.json"
    package_json.write_text(
        json.dumps(
            {
                "name": "@jupyter-ai-contrib/server-documents",
                "jupyterlab": {
                    "_build": {
                        "load": "static/remoteEntry.bbbbbbbbbbbbbbbbbbbb.js"
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return bundle, remote_entry, package_json


def patcher_source_divergent_repair_before():
    patcher = load_patcher_module()
    return (
        patcher.DIVERGENT_REPAIR_CALL_BEFORE
        + "\n"
        + patcher.DIVERGENT_REPAIR_HELPER_BEFORE
    )


def test_patch_applies_backend_guards_and_crdt_outputs_and_is_idempotent(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)

    assert patcher.patch_package(tmp_path)

    clients = (tmp_path / "websockets/clients.py").read_text(encoding="utf-8")
    yroom = (tmp_path / "rooms/yroom.py").read_text(encoding="utf-8")
    output_processor = (tmp_path / "outputs/output_processor.py").read_text(
        encoding="utf-8"
    )
    assert patcher.CLIENT_LOOKUP_MARKER in clients
    assert "self.synced.get(client_id) or self.desynced.get(client_id)" in clients
    assert "if client and client.websocket" in clients
    assert patcher.QUEUE_GUARD_MARKER in yroom
    assert "except Exception:" in yroom
    assert "finally:" in yroom
    assert "self._message_queue.task_done()" in yroom
    assert patcher.LATE_SYNC_STEP2_MARKER in yroom
    assert "handshake_timeout = traitlets.Float(" in yroom
    assert "self._pending_ss2: dict[str, asyncio.Future[bytes]] = {}" in yroom
    assert "pending = self._pending_ss2.get(client_id)" in yroom
    assert "self.handle_sync_step2(client_id, message)" in yroom
    assert "timeout=self.handshake_timeout" in yroom
    assert "self._pending_ss2.pop(client_id, None)" in yroom
    timeout_block = yroom[yroom.index("except asyncio.TimeoutError:") :]
    timeout_block = timeout_block[: timeout_block.index("except Exception:")]
    assert "handshake_failed = True" not in timeout_block
    assert patcher.YDOC_OUTPUT_MARKER in output_processor
    assert "self.transform_output(msg_type, content, ydoc=True)" in output_processor
    assert patcher.YDOC_STREAM_TEXT_MARKER in output_processor
    assert patcher.YDOC_STREAM_MERGE_MARKER in output_processor
    assert "from pycrdt import Map, Text" in output_processor
    assert 'text = Text(content["text"]) if ydoc else content["text"]' in (
        output_processor
    )
    assert "self._write_ydoc_stream(ycell, cell_id, content)" in output_processor
    assert "_neurodesktop_stream.write_stream_output(" in output_processor
    assert "self._discard_stream_position(cell_id)" in output_processor

    # The coalescing logic is installed as an importable module, not spliced
    # into the anchored change.
    module_path = tmp_path / "outputs/_neurodesktop_stream.py"
    assert module_path.read_text(encoding="utf-8") == patcher.stream_module_source()
    assert "def process_stream_text" in module_path.read_text(encoding="utf-8")

    # The kernel websocket bridge nudges every fresh connection before its
    # listen tasks start, from a module installed next to it.
    websocket = (tmp_path / "websocket_connection.py").read_text(encoding="utf-8")
    assert patcher.KERNEL_NUDGE_MARKER in websocket
    assert "from . import _neurodesktop_kernel_nudge" in websocket
    assert "await _neurodesktop_kernel_nudge.nudge(self)" in websocket
    assert websocket.index("start_channels(hb=False)") < websocket.index(
        "await _neurodesktop_kernel_nudge.nudge(self)"
    ) < websocket.index("asyncio.create_task(self._listen(ch))")
    nudge_module_path = tmp_path / "_neurodesktop_kernel_nudge.py"
    nudge_module = nudge_module_path.read_text(encoding="utf-8")
    assert nudge_module == patcher.nudge_module_source()
    assert "async def nudge" in nudge_module

    assert not patcher.patch_package(tmp_path)


def test_patch_refreshes_an_outdated_installed_stream_module(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    assert patcher.patch_package(tmp_path)

    module_path = tmp_path / "outputs/_neurodesktop_stream.py"
    module_path.write_text("outdated copy\n", encoding="utf-8")

    assert patcher.patch_package(tmp_path)
    assert module_path.read_text(encoding="utf-8") == patcher.stream_module_source()
    assert not patcher.patch_package(tmp_path)


def test_patch_refreshes_an_outdated_installed_nudge_module(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    assert patcher.patch_package(tmp_path)

    module_path = tmp_path / "_neurodesktop_kernel_nudge.py"
    module_path.write_text("outdated copy\n", encoding="utf-8")

    assert patcher.patch_package(tmp_path)
    assert module_path.read_text(encoding="utf-8") == patcher.nudge_module_source()
    assert not patcher.patch_package(tmp_path)


def test_patch_refuses_a_missing_nudge_module_as_partial(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    assert patcher.patch_package(tmp_path)

    (tmp_path / "_neurodesktop_kernel_nudge.py").unlink()

    with pytest.raises(ValueError, match="partial kernel websocket nudge"):
        patcher.patch_package(tmp_path)


def test_patch_refuses_websocket_nudge_anchor_drift(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    websocket_path = tmp_path / "websocket_connection.py"
    websocket_path.write_text("upstream changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="kernel websocket nudge anchor"):
        patcher.patch_package(tmp_path)

    # Validation failures must leave every file untouched.
    assert websocket_path.read_text(encoding="utf-8") == "upstream changed\n"
    assert (
        tmp_path / "websockets/clients.py"
    ).read_text(encoding="utf-8") == CLIENTS_SOURCE
    assert not (tmp_path / "_neurodesktop_kernel_nudge.py").exists()
    assert not (tmp_path / "outputs/_neurodesktop_stream.py").exists()


def test_patch_refuses_a_missing_stream_module_as_partial(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    assert patcher.patch_package(tmp_path)

    (tmp_path / "outputs/_neurodesktop_stream.py").unlink()

    with pytest.raises(ValueError, match="partial CRDT output workaround"):
        patcher.patch_package(tmp_path)


def test_patch_refuses_an_incomplete_late_sync_step2_workaround(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    assert patcher.patch_package(tmp_path)

    yroom_path = tmp_path / "rooms/yroom.py"
    yroom_path.write_text(
        yroom_path.read_text(encoding="utf-8").replace(
            patcher.PENDING_SS2_CLEAR_AFTER,
            "        self._pending_ss2.pop(client_id, None)\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="late SyncStep2 workaround is incomplete"):
        patcher.patch_package(tmp_path)


def test_patch_refuses_anchor_drift_without_partial_changes(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    clients_path = tmp_path / "websockets/clients.py"
    clients_path.write_text("upstream changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="client lookup anchor"):
        patcher.patch_package(tmp_path)

    assert clients_path.read_text(encoding="utf-8") == "upstream changed\n"
    assert (tmp_path / "rooms/yroom.py").read_text(encoding="utf-8") == YROOM_SOURCE
    assert (
        tmp_path / "outputs/output_processor.py"
    ).read_text(encoding="utf-8") == OUTPUT_PROCESSOR_SOURCE
    assert not (tmp_path / "outputs/_neurodesktop_stream.py").exists()


def test_patch_marks_server_executed_code_cells_trusted(tmp_path):
    patcher = load_patcher_module()
    original_bundle, original_remote_entry, package_json = write_frontend_fixture(
        tmp_path, patcher.WIDGET_TRUST_BEFORE
    )

    assert patcher.patch_widget_trust(tmp_path)

    load_path = json.loads(package_json.read_text(encoding="utf-8"))["jupyterlab"][
        "_build"
    ]["load"]
    patched_remote_entry = tmp_path / load_path
    assert patched_remote_entry != original_remote_entry
    assert patched_remote_entry.exists()

    patched_remote_text = patched_remote_entry.read_text(encoding="utf-8")
    patched_bundles = [
        path
        for path in (tmp_path / "static").glob("278.*.js")
        if path != original_bundle
    ]
    assert len(patched_bundles) == 1
    patched_bundle = patched_bundles[0]
    patched_hash = patched_bundle.name.split(".")[1]
    assert patched_hash in patched_remote_text
    assert "aaaaaaaaaaaaaaaaaaaa" not in patched_remote_text

    patched = patched_bundle.read_text(encoding="utf-8")
    assert patcher.WIDGET_TRUST_MARKER in patched
    assert "e.model.trusted=!0" in patched
    assert patcher.DIVERGENT_REPAIR_MARKER in patched
    assert "p.decodeStateVector(s)" in patched
    assert "o.id.client" in patched
    assert "o.id.clock" in patched
    assert patcher.DIVERGENT_REPAIR_CALL_BEFORE not in patched
    original_text = original_bundle.read_text(encoding="utf-8")
    assert original_text.startswith(patcher.WIDGET_TRUST_BEFORE)
    assert patcher.DIVERGENT_REPAIR_CALL_BEFORE in original_text
    assert (
        original_remote_entry.read_text(encoding="utf-8")
        == 'T.u=e=>e+"."+{278:"aaaaaaaaaaaaaaaaaaaa"}[e]+".js?v="+'
        '{278:"aaaaaaaaaaaaaaaaaaaa"}[e]'
    )
    assert not patcher.patch_widget_trust(tmp_path)


def test_widget_trust_patch_refuses_frontend_anchor_drift(tmp_path):
    patcher = load_patcher_module()
    bundle, _, _ = write_frontend_fixture(tmp_path, "upstream changed")

    with pytest.raises(ValueError, match="cell trust anchor"):
        patcher.patch_widget_trust(tmp_path)

    unchanged = bundle.read_text(encoding="utf-8")
    assert unchanged.startswith("upstream changed\n")
    assert patcher.DIVERGENT_REPAIR_CALL_BEFORE in unchanged


def test_widget_trust_patch_migrates_legacy_in_place_patch(tmp_path):
    patcher = load_patcher_module()
    legacy_bundle, legacy_remote_entry, package_json = write_frontend_fixture(
        tmp_path, patcher.WIDGET_TRUST_AFTER
    )

    assert patcher.patch_widget_trust(tmp_path)

    load_path = json.loads(package_json.read_text(encoding="utf-8"))["jupyterlab"][
        "_build"
    ]["load"]
    patched_remote_entry = tmp_path / load_path
    assert patched_remote_entry != legacy_remote_entry
    assert patcher.WIDGET_CACHE_SAFE_MARKER in patched_remote_entry.read_text(
        encoding="utf-8"
    )
    legacy_text = legacy_bundle.read_text(encoding="utf-8")
    assert legacy_text.startswith(patcher.WIDGET_TRUST_AFTER)
    assert patcher.DIVERGENT_REPAIR_CALL_BEFORE in legacy_text
    assert not patcher.patch_widget_trust(tmp_path)


def test_frontend_patch_extends_a_cache_safe_trust_only_entry(tmp_path):
    patcher = load_patcher_module()
    trust_bundle, remote_entry, package_json = write_frontend_fixture(
        tmp_path, patcher.WIDGET_TRUST_AFTER
    )
    remote_entry.write_text(
        remote_entry.read_text(encoding="utf-8")
        + f"\n/*{patcher.WIDGET_CACHE_SAFE_MARKER}*/\n",
        encoding="utf-8",
    )

    assert patcher.patch_widget_trust(tmp_path)

    load_path = json.loads(package_json.read_text(encoding="utf-8"))["jupyterlab"][
        "_build"
    ]["load"]
    patched_remote = tmp_path / load_path
    assert patched_remote != remote_entry
    patched_bundles = [
        path
        for path in (tmp_path / "static").glob("278.*.js")
        if path != trust_bundle
    ]
    assert len(patched_bundles) == 1
    patched = patched_bundles[0].read_text(encoding="utf-8")
    assert patcher.WIDGET_TRUST_MARKER in patched
    assert patcher.DIVERGENT_REPAIR_MARKER in patched
    assert not patcher.patch_widget_trust(tmp_path)


def test_frontend_patch_refuses_partial_divergent_repair(tmp_path):
    patcher = load_patcher_module()
    source = (
        patcher.WIDGET_TRUST_BEFORE
        + "\n"
        + patcher.DIVERGENT_REPAIR_CALL_AFTER
        + "\n"
        + patcher.DIVERGENT_REPAIR_HELPER_BEFORE
    )
    bundle, _, _ = write_frontend_fixture(
        tmp_path, source, include_repair=False
    )

    with pytest.raises(ValueError, match="partial divergent-history repair"):
        patcher.patch_widget_trust(tmp_path)

    assert bundle.read_text(encoding="utf-8").startswith(source)


def test_divergent_repair_deletes_only_server_unknown_item_ranges():
    patcher = load_patcher_module()
    script = f'''const p = {{}};
p.Map = class {{}};
p.Array = class {{
  constructor(items) {{
    this._start = items[0] ?? null;
    this.deletions = [];
    for (let index = 0; index < items.length; index++) {{
      items[index].right = items[index + 1] ?? null;
    }}
  }}
  delete(index, length) {{ this.deletions.push([index, length]); }}
}};
p.Text = class extends p.Array {{}};
p.XmlFragment = class extends p.Array {{}};
{patcher.DIVERGENT_REPAIR_HELPER_AFTER}
const item = (client, clock, length, deleted = false, countable = true) =>
  ({{ id: {{ client, clock }}, length, deleted, countable, right: null }});

const serverOwned = item(7, 0, 10);
const clientOnly = item(8, 0, 4);
const mixed = new p.Array([serverOwned, clientOnly]);
S(mixed, new Map([[7, 10]]));
if (JSON.stringify(mixed.deletions) !== JSON.stringify([[10, 4]])) process.exit(1);

clientOnly.deleted = true;
S(mixed, new Map([[7, 10]]));
if (mixed.deletions.length !== 1) process.exit(2);

const partial = new p.Text([item(7, 8, 5)]);
S(partial, new Map([[7, 10]]));
if (JSON.stringify(partial.deletions) !== JSON.stringify([[2, 3]])) process.exit(3);

const metadata = new p.Map();
S(metadata, new Map());
'''
    subprocess.run(["node", "-e", script], check=True)


def test_dockerfile_applies_workaround_after_pinned_package_install():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    package_pin = dockerfile.index("jupyter-server-documents==0.3.3")
    patch_install = dockerfile.index(
        "/opt/neurodesktop/patch_jupyter_server_documents.py"
    )
    patch_run = dockerfile.index(
        "/opt/conda/bin/python /opt/neurodesktop/patch_jupyter_server_documents.py"
    )
    assert package_pin < patch_install < patch_run
