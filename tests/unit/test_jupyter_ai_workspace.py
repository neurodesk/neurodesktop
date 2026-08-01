"""Workspace initialization contracts for newly created Jupyter AI chats."""

from types import SimpleNamespace

from testlib import load_source_module, repo_path


def load_workspace_module():
    return load_source_module(
        "jupyter_ai_workspace",
        "/opt/neurodesktop/jupyter_ai_workspace.py",
        "config/jupyter/jupyter_ai_workspace.py",
    )


def test_new_chat_seeds_agents_file_without_overwriting(tmp_path, monkeypatch):
    workspace = load_workspace_module()
    seed = tmp_path / "seed-AGENTS.md"
    seed.write_text("Neurodesktop guidance\n", encoding="utf-8")
    monkeypatch.setattr(workspace, "AGENTS_SOURCE", seed)

    chat_dir = tmp_path / "project"
    chat_dir.mkdir()
    hook_context = SimpleNamespace(log=SimpleNamespace(warning=lambda *a, **k: None))

    workspace.seed_agents_on_chat_save(
        os_path=str(chat_dir / "first.chat"),
        model={"type": "file", "path": "project/first.chat"},
        contents_manager=hook_context,
    )

    agents_file = chat_dir / "AGENTS.md"
    assert agents_file.read_text(encoding="utf-8") == "Neurodesktop guidance\n"

    agents_file.write_text("Project-specific guidance\n", encoding="utf-8")
    workspace.seed_agents_on_chat_save(
        os_path=str(chat_dir / "second.chat"),
        model={"type": "file", "path": "project/second.chat"},
        contents_manager=hook_context,
    )

    assert agents_file.read_text(encoding="utf-8") == "Project-specific guidance\n"


def test_non_chat_save_does_not_seed_agents_file(tmp_path, monkeypatch):
    workspace = load_workspace_module()
    seed = tmp_path / "seed-AGENTS.md"
    seed.write_text("Neurodesktop guidance\n", encoding="utf-8")
    monkeypatch.setattr(workspace, "AGENTS_SOURCE", seed)

    workspace.seed_agents_on_chat_save(
        os_path=str(tmp_path / "analysis.ipynb"),
        model={"type": "notebook", "path": "analysis.ipynb"},
        contents_manager=SimpleNamespace(
            log=SimpleNamespace(warning=lambda *a, **k: None)
        ),
    )

    assert not (tmp_path / "AGENTS.md").exists()


def test_missing_seed_warns_without_blocking_chat_save(tmp_path, monkeypatch):
    workspace = load_workspace_module()
    monkeypatch.setattr(workspace, "AGENTS_SOURCE", tmp_path / "missing-AGENTS.md")
    warnings = []

    workspace.seed_agents_on_chat_save(
        os_path=str(tmp_path / "new.chat"),
        model={"type": "file", "path": "new.chat"},
        contents_manager=SimpleNamespace(
            log=SimpleNamespace(
                warning=lambda message, *args, **kwargs: warnings.append(
                    (message, args, kwargs)
                )
            )
        ),
    )

    assert not (tmp_path / "AGENTS.md").exists()
    assert len(warnings) == 1
    assert "Unable to seed AGENTS.md for Jupyter AI chat" in warnings[0][0]


def test_image_installs_and_configures_the_chat_workspace_hook():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    server_extra = repo_path(
        "config/jupyter/jupyter_server_config_extra.py"
    ).read_text(encoding="utf-8")
    template = repo_path(
        "config/jupyter/jupyter_notebook_config.py.template"
    ).read_text(encoding="utf-8")

    assert (
        "install -m 0644 /tmp/jupyter/jupyter_ai_workspace.py "
        "/opt/neurodesktop/jupyter_ai_workspace.py"
    ) in dockerfile
    # The hook must be registered in the server config, which ServerApp loads
    # exactly once. Registering it in jupyter_notebook_config.py would apply
    # it once per shimmed legacy config load (nbclassic and notebook) and
    # warn "Overriding existing post_save_hook" at every startup.
    assert (
        "cat /tmp/jupyter/jupyter_server_config_extra.py "
        ">> /etc/jupyter/jupyter_server_config.py"
    ) in dockerfile
    assert "c.FileContentsManager.post_save_hook" not in template
    # The import must stay guarded so a missing or broken seeding module only
    # disables AGENTS.md seeding instead of aborting server configuration.
    assert (
        "try:\n"
        "    from jupyter_ai_workspace import seed_agents_on_chat_save\n"
        "except Exception"
    ) in server_extra
    assert (
        "else:\n"
        "    c.FileContentsManager.post_save_hook = seed_agents_on_chat_save"
    ) in server_extra
