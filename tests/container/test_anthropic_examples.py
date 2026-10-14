"""Public SDK examples and their bytecode must not resemble Google keys."""

import importlib.metadata
import json


def test_installed_anthropic_documentation_and_bytecode_are_clean():
    package = importlib.metadata.distribution("anthropic")
    example = json.dumps(dict(type="service_account"))
    for relative in (
        "resources/beta/agents/agents.py",
        "types/beta/agent_create_params.py",
        "types/beta/agent_update_params.py",
        "types/beta/beta_managed_agents_agent.py",
        "types/beta/beta_managed_agents_session_agent.py",
        "types/beta/beta_managed_agents_session_thread_agent.py",
    ):
        path = package.locate_file("anthropic/" + relative)
        source = path.read_text(encoding="utf-8")
        assert example not in source, relative
        assert 'dict(type="service_account")' in source, relative
        for bytecode in (path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"):
            assert example.encode() not in bytecode.read_bytes(), bytecode
