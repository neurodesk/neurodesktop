import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from testlib import repo_path


sys.path.insert(0, str(repo_path("extensions/t3-code-server")))


@pytest.fixture
def prepare():
    from neurodesk_t3_code import supervisor

    return supervisor.prepare_startup_settings


@pytest.fixture
def profile(tmp_path):
    policy = SimpleNamespace(base_dir=tmp_path / ".t3", provider_bin=tmp_path / "providers")
    return policy, policy.base_dir / "userdata/settings.json"


def test_fresh_preparation_applies_both_policies_and_stays_private(prepare, profile):
    policy, path = profile
    prepare(policy)

    assert json.loads(path.read_text()) == {
        "providerInstances": {
            "codex": {"driver": "codex", "config": {"binaryPath": str(policy.provider_bin / "codex")}},
            "opencode": {"driver": "opencode", "enabled": True,
                         "config": {"binaryPath": str(policy.provider_bin / "opencode")}},
        },
        "enableProviderUpdateChecks": False,
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("provider_fields", [
    {"providers": []},
    {"providers": None},
    {"providerInstances": []},
    {"providerInstances": {"broken": None}},
])
def test_unreadable_provider_fields_do_not_block_update_suppression(prepare, profile, provider_fields):
    policy, path = profile
    path.parent.mkdir(parents=True)
    original = {**provider_fields, "theme": "user", "enableProviderUpdateChecks": True}
    path.write_text(json.dumps(original))
    prepare(policy)

    assert json.loads(path.read_text()) == {**original, "enableProviderUpdateChecks": False}
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("value", [0, 1, None, "false"])
def test_update_policy_requires_the_boolean_false(prepare, profile, value):
    policy, path = profile
    path.parent.mkdir(parents=True)
    original = {"providerInstances": {"broken": None}, "enableProviderUpdateChecks": value}
    path.write_text(json.dumps(original))
    prepare(policy)

    actual = json.loads(path.read_text())
    assert actual["enableProviderUpdateChecks"] is False
    assert actual["providerInstances"] == original["providerInstances"]


@pytest.mark.parametrize("content", [b"{invalid", b"\xff", b"[]", b"null", b"true", b'"user"'])
def test_unreadable_documents_keep_their_bytes_and_inode(prepare, profile, content):
    policy, path = profile
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    inode = path.stat().st_ino
    prepare(policy)

    assert path.read_bytes() == content
    assert path.stat().st_ino == inode
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("existing", [
    {"providerInstances": {
        "work": {"driver": "codex", "config": {"binaryPath": "/custom/codex"}},
        "other": {"driver": "opencode", "enabled": False},
    }},
    {"providerInstances": {"broken": None}},
])
def test_prepared_document_keeps_user_formatting_and_inode(prepare, profile, existing):
    policy, path = profile
    path.parent.mkdir(parents=True)
    original = json.dumps({**existing, "enableProviderUpdateChecks": False}, separators=(",", ":"))
    path.write_text(original)
    inode = path.stat().st_ino
    prepare(policy)

    assert path.read_text() == original
    assert path.stat().st_ino == inode


def test_repeat_preparation_neither_rewrites_nor_leaves_temporary_files(prepare, profile):
    policy, path = profile
    prepare(policy)
    before = path.read_bytes()
    inode = path.stat().st_ino
    prepare(policy)

    assert path.read_bytes() == before
    assert path.stat().st_ino == inode
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("initial", [None, {"theme": "user"}, {"providerInstances": [], "enableProviderUpdateChecks": False}])
def test_preparation_reads_once_and_commits_only_when_needed(prepare, profile, monkeypatch, initial):
    from neurodesk_t3_code import supervisor

    policy, path = profile
    if initial is not None:
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(initial))
    reads = []
    replacements = []
    read_text = Path.read_text
    replace = supervisor.os.replace

    def observe_read(target, *args, **kwargs):
        if target == path:
            reads.append(target)
        return read_text(target, *args, **kwargs)

    def observe_replace(source, target):
        replacements.append(Path(target))
        return replace(source, target)

    with monkeypatch.context() as observation:
        observation.setattr(Path, "read_text", observe_read)
        observation.setattr(supervisor.os, "replace", observe_replace)
        prepare(policy)

    assert reads == [path]
    assert replacements == ([] if initial and initial.get("enableProviderUpdateChecks") is False else [path])
    assert json.loads(path.read_text())["enableProviderUpdateChecks"] is False
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("existing", [False, True])
def test_failed_commit_preserves_the_original_and_removes_temporary_files(prepare, profile, monkeypatch, existing):
    from neurodesk_t3_code import supervisor

    policy, path = profile
    original = b'{"theme":"user"}'
    if existing:
        path.parent.mkdir(parents=True)
        path.write_bytes(original)
        inode = path.stat().st_ino

    def fail_replace(source, target):
        raise PermissionError("replacement denied")

    monkeypatch.setattr(supervisor.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="replacement denied"):
        prepare(policy)

    if existing:
        assert path.read_bytes() == original
        assert path.stat().st_ino == inode
        assert list(path.parent.iterdir()) == [path]
    else:
        assert not path.exists()
        assert not list(path.parent.iterdir())
