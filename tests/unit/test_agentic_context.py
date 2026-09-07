"""Preserve as much task evidence as the serialized prompt budget permits."""

import json

import pytest

from testlib import load_source_module


@pytest.mark.parametrize("unit", ["A", "é", "😀", '"\\\n'])
def test_context_string_uses_largest_prefix_that_fits_json_budget(unit):
    worker = load_source_module(
        "agentic_context_test", "/opt/agentic_worker.py", ".github/scripts/agentic_worker.py"
    )
    original = unit * 65000
    context = worker.bounded_context({"issue": {"number": 42, "title": "Bug", "body": original}})
    body = context["issue"]["body"]
    suffix = "\n[Evidence truncated]"
    assert body.endswith(suffix)
    prefix = body[:-len(suffix)]
    assert original.startswith(prefix)
    assert len(json.dumps(body)) <= 20000
    assert len(json.dumps(prefix + original[len(prefix)] + suffix)) > 20000
    if unit == "A":
        assert len(prefix) > 19000
    assert context["issue"]["number"] == 42
    assert "issue" in context["truncation_notice"]
