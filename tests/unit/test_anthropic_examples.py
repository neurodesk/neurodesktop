"""SDK documentation cleanup preserves executable code and unrelated values."""

import ast
import py_compile
import runpy

import pytest

from testlib import repo_path


SUBJECT = runpy.run_path(str(repo_path("scripts/sanitize_anthropic_examples.py")))
FILES = (
    "resources/beta/agents/agents.py",
    "types/beta/agent_create_params.py",
    "types/beta/agent_update_params.py",
    "types/beta/beta_managed_agents_agent.py",
    "types/beta/beta_managed_agents_session_agent.py",
    "types/beta/beta_managed_agents_session_thread_agent.py",
)
EXAMPLE = '{"type": "service_account"}'


@pytest.fixture
def sdk(tmp_path):
    for index, relative in enumerate(FILES):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Frozen wording from the public SDK, including attribute docstrings.
        doc = '''"""Execution principal for runs of this agent.
    Omitting it, sending null, or sending `{"type": "service_account"}`
    stores the default.
    """'''
        if index == 0:
            source = "\n".join(
                f"def {name}(value):\n    {doc}\n    return value\n"
                for name in ("create", "update", "async_create", "async_update")
            )
        else:
            source = "execution_identity: dict\n" + doc + "\n"
        source += "identity = dict(type='service_account')\n"
        source += "credential = 'user-supplied-value-must-survive'\n"
        path.write_text(source)
        py_compile.compile(str(path), doraise=True)
    return tmp_path


def executable_ast(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if hasattr(node, "body") and isinstance(node.body, list):
            node.body = [
                statement for statement in node.body
                if not (isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str))
            ]
    return ast.dump(tree)


def test_documentation_rewritten_without_changing_runtime_or_unrelated_cache(sdk):
    sources = {relative: (sdk / relative).read_text() for relative in FILES}
    unrelated = sdk / "types/beta/__pycache__/other.cpython-313.pyc"
    unrelated.write_bytes(b"unrelated bytecode")
    assert SUBJECT["sanitize"](sdk)
    for relative, source in sources.items():
        path = sdk / relative
        patched = path.read_text()
        assert patched == source.replace(EXAMPLE, 'dict(type="service_account")')
        assert executable_ast(patched) == executable_ast(source)
        assert not list((path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"))
        actual = runpy.run_path(str(path))
        assert actual["identity"] == {"type": "service_account"}
        assert actual["credential"] == "user-supplied-value-must-survive"
    resource = runpy.run_path(str(sdk / FILES[0]))
    assert resource["create"]({"name": "agent"}) == {"name": "agent"}
    assert unrelated.read_bytes() == b"unrelated bytecode"


def test_repeated_cleanup_removes_restored_stale_bytecode(sdk):
    assert SUBJECT["sanitize"](sdk)
    source = (sdk / FILES[0]).read_bytes()
    cache = sdk / "resources/beta/agents/__pycache__/agents.cpython-313.pyc"
    cache.write_bytes(b"stale documentation")
    assert not SUBJECT["sanitize"](sdk)
    assert not cache.exists()
    assert (sdk / FILES[0]).read_bytes() == source


@pytest.mark.parametrize("change", ["missing", "duplicate", "runtime", "syntax"])
def test_changed_upstream_source_fails_before_any_cleanup(sdk, change):
    path = sdk / FILES[-1]
    source = path.read_text()
    if change == "missing":
        source = source.replace(EXAMPLE, "another example")
    elif change == "duplicate":
        source += f"\n'''{EXAMPLE}'''\n"
    elif change == "runtime":
        source += f"\nruntime_identity = {EXAMPLE}\n"
    else:
        source += "\ninvalid syntax!\n"
    path.write_text(source)
    before = {p: p.read_bytes() for p in sdk.rglob("*") if p.is_file()}
    with pytest.raises((ValueError, SyntaxError)):
        SUBJECT["sanitize"](sdk)
    assert {p: p.read_bytes() for p in before} == before
