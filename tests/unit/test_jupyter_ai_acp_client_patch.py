"""Build-time workarounds for jupyter-ai-acp-client logging and OpenCode 2."""

import re

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

OPENCODE_SOURCE = '''def _check_opencode() -> None:
    version_str = version_match.group(1)
    current_version = tuple(int(x) for x in version_str.split("."))

    if current_version < (1, 0, 0) or current_version[0] >= 2:
        raise PersonaRequirementsUnmet(
            f"opencode version {version_str} is installed,"
            " but version >=1.0.0,<2 is required."
            " Please upgrade opencode. See https://opencode.ai for instructions."
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
    (package_dir / "acp_personas").mkdir()
    (package_dir / "acp_personas/opencode.py").write_text(OPENCODE_SOURCE, encoding="utf-8")


def opencode_guard(package_dir):
    """Run the patched persona version guard against an ``opencode --version``."""

    class PersonaRequirementsUnmet(Exception):
        pass

    def check(version):
        namespace = {
            "PersonaRequirementsUnmet": PersonaRequirementsUnmet,
            "version_match": re.search(r"(\d+\.\d+\.\d+)", version),
        }
        exec((package_dir / "acp_personas/opencode.py").read_text(), namespace)
        try:
            namespace["_check_opencode"]()
        except PersonaRequirementsUnmet as error:
            return str(error)
        return None

    return check


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


def test_patch_admits_opencode_2_and_still_rejects_other_majors(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    check = opencode_guard(tmp_path)
    assert "<2 is required" in check("opencode v2.2.5")

    assert patcher.patch_package(tmp_path)

    assert check("opencode v2.2.5") is None
    assert check("1.19.5") is None
    assert "<3 is required" in check("opencode v3.0.0")
    assert "<3 is required" in check("0.9.0")


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

    package_pin = dockerfile.index("jupyter-ai-acp-client==0.3.4")
    patch_install = dockerfile.index(
        "/opt/neurodesktop/patch_jupyter_ai_acp_client.py"
    )
    patch_run = dockerfile.index(
        "/opt/conda/bin/python /opt/neurodesktop/patch_jupyter_ai_acp_client.py"
    )
    assert package_pin < patch_install < patch_run
