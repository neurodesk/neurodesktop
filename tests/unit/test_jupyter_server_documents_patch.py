"""Build-time workaround contract for jupyter-server-documents issue #271."""

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


def test_dockerfile_applies_workaround_after_pinned_package_install():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    package_pin = dockerfile.index("jupyter-server-documents==0.3.2")
    patch_install = dockerfile.index(
        "/opt/neurodesktop/patch_jupyter_server_documents.py"
    )
    patch_run = dockerfile.index(
        "/opt/conda/bin/python /opt/neurodesktop/patch_jupyter_server_documents.py"
    )
    assert package_pin < patch_install < patch_run
