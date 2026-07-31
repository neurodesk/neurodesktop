"""Runtime contracts for Jupyter AI and the MyST/ASTRA report toolchain.

The `astra` CLI and the ASTRA agent skill are covered by
``test_astra_agent_skills_image.py``.
"""

import asyncio
import importlib.metadata
import logging
from pathlib import Path

from testlib import load_source_module, run_cmd


def test_jupyter_ai_acp_stack_registers_neurodesktop_agent_personas():
    assert importlib.metadata.version("jupyter_ai") == "3.1.1"
    assert importlib.metadata.version("jupyter-ai-acp-client") == "0.2.1"

    from jupyter_ai_acp_client.acp_personas.claude import ClaudeAcpPersona
    from jupyter_ai_acp_client.acp_personas.codex import CodexAcpPersona
    from jupyter_ai_acp_client.acp_personas.opencode import OpenCodeAcpPersona

    assert ClaudeAcpPersona.__name__ == "ClaudeAcpPersona"
    assert CodexAcpPersona.__name__ == "CodexAcpPersona"
    assert OpenCodeAcpPersona.__name__ == "OpenCodeAcpPersona"
    persona_names = {
        entry.name
        for entry in importlib.metadata.entry_points(group="jupyter_ai.personas")
    }
    assert {"claude-acp", "codex-acp", "opencode-acp"} <= persona_names

    for command in ("claude-agent-acp", "codex-acp"):
        code, output = run_cmd(f"command -v {command}")
        assert code == 0, output


def test_new_jupyter_ai_chat_seeds_editable_agents_file(tmp_path):
    from jupyter_server.services.contents.filemanager import FileContentsManager

    workspace_module = load_source_module(
        "jupyter_ai_workspace_image",
        "/opt/neurodesktop/jupyter_ai_workspace.py",
        "config/jupyter/jupyter_ai_workspace.py",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = FileContentsManager(root_dir=str(workspace))
    manager.register_post_save_hook(workspace_module.seed_agents_on_chat_save)

    first_chat = manager.new_untitled(path="", type="file", ext=".chat")
    assert first_chat["path"].endswith(".chat")

    agents_file = workspace / "AGENTS.md"
    assert agents_file.read_bytes() == Path("/opt/AGENTS.md").read_bytes()

    agents_file.write_text("Project-specific guidance\n", encoding="utf-8")
    second_chat = manager.new_untitled(path="", type="file", ext=".chat")
    assert second_chat["path"].endswith(".chat")
    assert agents_file.read_text(encoding="utf-8") == "Project-specific guidance\n"


def test_jupyter_server_documents_stale_message_does_not_kill_room_queue():
    from jupyter_server_documents.rooms.yroom import YRoom
    from jupyter_server_documents.websockets.clients import YjsClientGroup

    group = YjsClientGroup(room_id="test-room", log=logging.getLogger("test"))
    try:
        group.get("removed-client")
    except Exception as exc:
        assert not isinstance(exc, UnboundLocalError)
    else:
        raise AssertionError("a removed client lookup must fail")

    class QueueHarness:
        file_api = None
        room_id = "test-room"
        log = logging.getLogger("test")

        def __init__(self):
            self._message_queue = asyncio.Queue()
            self.handled = []

        async def handle_message(self, client_id, message):
            self.handled.append((client_id, message))
            if client_id == "removed-client":
                raise Exception("stale queued frame")

    async def exercise_queue():
        room = QueueHarness()
        room._message_queue.put_nowait(("removed-client", b"stale"))
        room._message_queue.put_nowait(("live-client", b"valid"))
        room._message_queue.put_nowait(None)
        await YRoom._process_message_queue(room)
        assert room.handled == [
            ("removed-client", b"stale"),
            ("live-client", b"valid"),
        ]
        assert room._message_queue._unfinished_tasks == 1  # stop sentinel only

    asyncio.run(exercise_queue())


def test_jupyter_ai_server_and_frontend_extensions_are_compatible():
    code, server_output = run_cmd("jupyter server extension list", timeout=60)
    assert code == 0, server_output
    for extension in (
        "jupyter_ai_acp_client",
        "jupyter_ai_persona_manager",
        "jupyter_ai_router",
        "jupyter_server_documents",
        "jupyter_server_mcp",
        "jupyterlab_chat",
    ):
        assert extension in server_output

    code, lab_output = run_cmd("jupyter labextension list --verbose", timeout=60)
    assert code == 0, lab_output
    assert "@jupyter/collaboration-extension v4.4.1" in lab_output
    assert "@jupyter/docprovider-extension v4.4.1" in lab_output
    assert "not compatible with the current JupyterLab" not in lab_output


def test_mystra_inventory_bundle_and_myst_cli_are_available():
    bundle = Path("/opt/neurodesktop/mystra/mystra.mjs")
    revision = Path("/opt/neurodesktop/mystra/REVISION")
    assert bundle.is_file()
    assert revision.read_text(encoding="utf-8").strip() == (
        "b01be473a4be988e58aa254c3efbf10c24f4d7bd"
    )

    code, output = run_cmd("myst --version")
    assert code == 0, output
    assert "1.10.1" in output

    code, output = run_cmd(
        "node -e \"import('/opt/neurodesktop/mystra/mystra.mjs')"
        ".then(m => { if (m.default?.name !== 'astra') process.exit(1) })\""
    )
    assert code == 0, output


def test_astra_article_and_book_themes_are_available_offline():
    theme_root = Path("/opt/neurodesktop/astra-theme")
    assert (theme_root / "REVISION").read_text(encoding="utf-8").strip() == (
        "3939ceadcbde34b509896fe1a332fdaa611d0dab"
    )

    for flavor in ("article", "book"):
        theme = theme_root / flavor
        assert (theme / "template.yml").is_file()
        assert (theme / "server.js").is_file()
        assert (theme / "build/index.js").is_file()
        assert (theme / "public/build").is_dir()

        code, output = run_cmd(
            "node -e \""
            "const pkg=require('./package.json');"
            "if(pkg.version !== '0.0.8') process.exit(1);"
            "for(const name of Object.keys(pkg.dependencies)) require.resolve(name);"
            "\"",
            cwd=theme,
        )
        assert code == 0, output
