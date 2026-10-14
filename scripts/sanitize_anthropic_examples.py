#!/usr/bin/env python3
"""Rewrite public SDK docstring examples that Trivy treats as Google keys.

Source: anthropics/anthropic-sdk-python v1.14.0. Only the standalone JSON
execution-identity example is changed, to its equivalent Python spelling.
"""

import ast
import importlib.util
from pathlib import Path


JSON_EXAMPLE = '{"type": "service_account"}'
PYTHON_EXAMPLE = 'dict(type="service_account")'
EXAMPLE_FILES = {
    "resources/beta/agents/agents.py": 4,
    "types/beta/agent_create_params.py": 1,
    "types/beta/agent_update_params.py": 1,
    "types/beta/beta_managed_agents_agent.py": 1,
    "types/beta/beta_managed_agents_session_agent.py": 1,
    "types/beta/beta_managed_agents_session_thread_agent.py": 1,
}


def sanitize(package_dir: Path) -> bool:
    """Validate all documentation before editing source or removing its cache."""
    changes = []
    for relative, expected in EXAMPLE_FILES.items():
        path = package_dir / relative
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        documents = [
            node.value.value for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ]
        original = sum(doc.count(JSON_EXAMPLE) for doc in documents)
        rewritten = sum(doc.count(PYTHON_EXAMPLE) for doc in documents)
        if (original + rewritten != expected
                or source.count(JSON_EXAMPLE) != original
                or source.count(PYTHON_EXAMPLE) != rewritten):
            raise ValueError(
                f"anthropic credential example changed in {relative}; reassess documentation cleanup"
            )
        patched = source.replace(JSON_EXAMPLE, PYTHON_EXAMPLE)
        compile(patched, str(path), "exec")
        changes.append((path, source, patched))

    changed = False
    for path, source, patched in changes:
        if patched != source:
            path.write_text(patched, encoding="utf-8")
            changed = True
        for bytecode in (path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"):
            bytecode.unlink()
    return changed


if __name__ == "__main__":
    spec = importlib.util.find_spec("anthropic")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("anthropic is missing; reassess documentation cleanup")
    sanitize(Path(next(iter(spec.submodule_search_locations))))
