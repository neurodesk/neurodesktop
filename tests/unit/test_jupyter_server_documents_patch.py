"""Build-time workaround contract for jupyter-server-documents issue #271."""

import json

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
    websocket_dir.mkdir(parents=True)
    rooms_dir.mkdir(parents=True)
    (websocket_dir / "clients.py").write_text(CLIENTS_SOURCE, encoding="utf-8")
    (rooms_dir / "yroom.py").write_text(YROOM_SOURCE, encoding="utf-8")


def write_frontend_fixture(labextension_dir, source):
    static_dir = labextension_dir / "static"
    static_dir.mkdir(parents=True)
    bundle = static_dir / "278.aaaaaaaaaaaaaaaaaaaa.js"
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


def test_patch_applies_both_issue_271_guards_and_is_idempotent(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)

    assert patcher.patch_package(tmp_path)

    clients = (tmp_path / "websockets/clients.py").read_text(encoding="utf-8")
    yroom = (tmp_path / "rooms/yroom.py").read_text(encoding="utf-8")
    assert patcher.CLIENT_LOOKUP_MARKER in clients
    assert "self.synced.get(client_id) or self.desynced.get(client_id)" in clients
    assert "if client and client.websocket" in clients
    assert patcher.QUEUE_GUARD_MARKER in yroom
    assert "except Exception:" in yroom
    assert "finally:" in yroom
    assert "self._message_queue.task_done()" in yroom

    assert not patcher.patch_package(tmp_path)


def test_patch_refuses_anchor_drift_without_partial_changes(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    clients_path = tmp_path / "websockets/clients.py"
    clients_path.write_text("upstream changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="client lookup anchor"):
        patcher.patch_package(tmp_path)

    assert clients_path.read_text(encoding="utf-8") == "upstream changed\n"
    assert (tmp_path / "rooms/yroom.py").read_text(encoding="utf-8") == YROOM_SOURCE


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
    assert original_bundle.read_text(encoding="utf-8") == patcher.WIDGET_TRUST_BEFORE
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

    assert bundle.read_text(encoding="utf-8") == "upstream changed"


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
    assert legacy_bundle.read_text(encoding="utf-8") == patcher.WIDGET_TRUST_AFTER
    assert not patcher.patch_widget_trust(tmp_path)


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
