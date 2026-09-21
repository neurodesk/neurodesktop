"""Remove only published documentation samples, including cached bytecode."""

import py_compile
import runpy
from pathlib import Path

import pytest

from testlib import repo_path


SUBJECT = runpy.run_path(str(repo_path("scripts/sanitize_ghapi_examples.py")))


def test_examples_cleaned_without_changing_api_or_other_values(tmp_path):
    """Executable spec metadata survives, and unrelated literals remain intact."""
    examples = SUBJECT["EXAMPLES"]
    unrelated = "ghp_" + "z" * 36
    operation = {
        "path": "/credentials/revoke", "verb": "POST", "body_params": ["credentials"],
        "body_examples": {"default": {"value": {"credentials": list(examples)}}},
    }
    source = "spec = " + repr({"ops": [operation], "unrelated": unrelated}) + "\n"
    path = tmp_path / "gh_spec.py"
    path.write_text(source)
    bytecode = Path(py_compile.compile(str(path), doraise=True))
    other_cache = bytecode.parent / "other_module.cpython-313.pyc"
    other_cache.write_bytes(b"unrelated cache")
    assert SUBJECT["sanitize"](tmp_path)
    assert not bytecode.exists()
    assert other_cache.read_bytes() == b"unrelated cache"
    actual = runpy.run_path(str(path))["spec"]
    operation["body_examples"]["default"]["value"]["credentials"] = list(examples.values())
    assert actual == {"ops": [operation], "unrelated": unrelated}
    assert all(sample not in path.read_text() for sample in examples)
    # Also remove an old cache restored alongside already-cleaned source.
    bytecode.write_bytes(b"stale sample cache")
    assert not SUBJECT["sanitize"](tmp_path)
    assert not bytecode.exists()


@pytest.mark.parametrize("change", ["missing", "duplicate"])
def test_changed_examples_fail_without_partial_cleanup(tmp_path, change):
    """Unexpected upstream examples must be investigated rather than ignored."""
    samples = list(SUBJECT["EXAMPLES"])
    samples = samples[:-1] if change == "missing" else samples + [samples[0]]
    path = tmp_path / "gh_spec.py"
    source = "examples = " + repr(samples) + "\n"
    path.write_text(source)
    with pytest.raises(ValueError, match="example changed"):
        SUBJECT["sanitize"](tmp_path)
    assert path.read_text() == source
