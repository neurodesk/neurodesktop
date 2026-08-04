"""Build-time workaround contract for jupyter-ai-acp-client per-event logging."""

import pytest

from testlib import load_source_module, repo_path


CLIENT_SOURCE = '''class DefaultAcpClient:
    async def _handle_agent_message_chunk(self, session_id, content):
        persona = self._personas_by_session.get(session_id)
        if persona is None:
            return
        message_id = self._tool_call_manager.get_or_create_text_message(session_id, persona)
        persona.log.info(f"agent_message_chunk: {len(text)} chars")

    async def session_update(self, session_id, update, **kwargs):
        persona = self._personas_by_session.get(session_id)
        if persona:
            persona.log.info(f"session_update: {type(update).__name__} for session {session_id}")
'''

MANAGER_SOURCE = '''class ToolCallManager:
    def handle_start(self, session_id, update, persona):
        persona.log.info(
            f"tool_call_start: id={update.tool_call_id} title={update.title!r}"
            f" kind={kind_str} locations={locations_paths}"
            f" diffs={len(diffs) if diffs else 0}"
        )

    def handle_progress(self, session_id, update, persona):
        persona.log.info(
            f"tool_call_progress: id={update.tool_call_id} title={update.title!r}"
            f" status={status_str} locations={locations_paths}"
            f" diffs={len(diffs) if diffs else 0}"
        )
'''


def load_patcher_module():
    return load_source_module(
        "jupyter_ai_acp_client_patch",
        "/opt/neurodesktop/patch_jupyter_ai_acp_client.py",
        "config/jupyter/patch_jupyter_ai_acp_client.py",
    )


def write_upstream_fixture(package_dir):
    (package_dir / "default_acp_client.py").write_text(CLIENT_SOURCE, encoding="utf-8")
    (package_dir / "tool_call_manager.py").write_text(MANAGER_SOURCE, encoding="utf-8")


def test_patch_demotes_all_per_event_logging_and_is_idempotent(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)

    assert patcher.patch_package(tmp_path)

    client = (tmp_path / "default_acp_client.py").read_text(encoding="utf-8")
    manager = (tmp_path / "tool_call_manager.py").read_text(encoding="utf-8")
    for text in (client, manager):
        assert patcher.MARKER in text
        assert "log.info" not in text
    assert 'persona.log.debug(f"agent_message_chunk' in client
    assert 'persona.log.debug(f"session_update' in client
    assert 'f"tool_call_start' in manager
    assert 'f"tool_call_progress' in manager
    assert manager.count("persona.log.debug(") == 2

    assert not patcher.patch_package(tmp_path)


def test_patch_refuses_anchor_drift_without_partial_changes(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    client_path = tmp_path / "default_acp_client.py"
    client_path.write_text("upstream changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="agent message chunk anchor"):
        patcher.patch_package(tmp_path)

    assert client_path.read_text(encoding="utf-8") == "upstream changed\n"
    assert (
        tmp_path / "tool_call_manager.py"
    ).read_text(encoding="utf-8") == MANAGER_SOURCE


def test_patch_refuses_partial_prior_application(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    manager_path = tmp_path / "tool_call_manager.py"
    manager_path.write_text(
        f"# {patcher.MARKER}\n" + MANAGER_SOURCE, encoding="utf-8"
    )

    with pytest.raises(ValueError, match="partial per-event log-level workaround"):
        patcher.patch_package(tmp_path)


def test_dockerfile_applies_workaround_after_pinned_package_install():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    package_pin = dockerfile.index("jupyter-ai-acp-client==0.2.1")
    patch_install = dockerfile.index(
        "/opt/neurodesktop/patch_jupyter_ai_acp_client.py"
    )
    patch_run = dockerfile.index(
        "/opt/conda/bin/python /opt/neurodesktop/patch_jupyter_ai_acp_client.py"
    )
    assert package_pin < patch_install < patch_run
