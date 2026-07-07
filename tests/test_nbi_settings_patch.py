"""Tests for patch_nbi.py.

The Notebook Intelligence settings panel auto-saves its client-side cache on
open, which reverts the OpenCode -> NBI model sync (see nbi_setup.sh). The
patcher rewrites the "open settings" command in the bundled labextension so
the panel is rebuilt from freshly fetched capabilities instead.
"""

import glob
import importlib.util
from pathlib import Path

import pytest


def first_existing_path(*candidates):
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    raise AssertionError(f"None of these paths exist: {candidates}")


def load_patcher_module():
    script = first_existing_path(
        "/opt/neurodesktop/patch_nbi.py",
        Path(__file__).resolve().parents[1] / "config/agents/patch_nbi.py",
    )
    spec = importlib.util.spec_from_file_location("nbi_patch", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Verbatim excerpts from the notebook_intelligence 5.2.1 labextension bundle:
# the NBI API class holding fetchCapabilities, and the settings command whose
# execute callback shows a panel built from the stale client-side cache.
BUNDLE_FIXTURE = (
    'class T{static async initialize(){await this.fetchCapabilities(),'
    "this.updateGitHubLoginStatus()}}"
    "const O=()=>{const t=new Qo({onSave:()=>{T.fetchCapabilities()}}),"
    'n=new a.MainAreaWidget({content:t});return n.id="nbi-settings",n};'
    "let U=O();"
    "e.commands.addCommand(pe.openConfigurationDialog,"
    '{label:"Notebook Intelligence Settings",'
    "execute:t=>{U.isDisposed&&(U=O()),"
    'U.isAttached||e.shell.add(U,"main"),'
    "e.shell.activateById(U.id)}})"
)


def test_settings_patch_rewrites_settings_command():
    patcher = load_patcher_module()

    patched, changed = patcher.patch_settings_bundle_text(BUNDLE_FIXTURE)

    assert changed
    assert patcher.SETTINGS_MARKER in patched
    # The panel is rebuilt after awaiting fresh capabilities.
    assert "execute:async t=>{" in patched
    assert "await T.fetchCapabilities()" in patched
    assert "U.isDisposed||U.dispose();" in patched
    # The stale-cache fast path is gone.
    assert "U.isDisposed&&(U=O())" not in patched
    # Everything around the command registration is untouched.
    assert patched.startswith("class T{static async initialize()")
    assert patched.endswith("e.shell.activateById(U.id)}})")


def test_settings_patch_is_idempotent():
    patcher = load_patcher_module()

    patched, _ = patcher.patch_settings_bundle_text(BUNDLE_FIXTURE)
    repatched, changed = patcher.patch_settings_bundle_text(patched)

    assert not changed
    assert repatched == patched


def test_settings_patch_leaves_unrelated_text_alone():
    patcher = load_patcher_module()

    text = "console.log('no settings command here')"
    patched, changed = patcher.patch_settings_bundle_text(text)

    assert not changed
    assert patched == text


def test_settings_patch_refuses_partial_match():
    """A settings command without a locatable API class must not be patched."""
    patcher = load_patcher_module()

    command_only = BUNDLE_FIXTURE.replace(
        "class T{static async initialize(){await this.fetchCapabilities(),"
        "this.updateGitHubLoginStatus()}}",
        "",
    )
    with pytest.raises(ValueError):
        patcher.patch_settings_bundle_text(command_only)


def test_main_fails_when_bundle_anchor_missing(tmp_path):
    """A notebook_intelligence upgrade that changes the bundle must fail the
    image build instead of silently reintroducing the stale-save bug."""
    patcher = load_patcher_module()

    bundle = tmp_path / "chunk.js"
    bundle.write_text("var unrelated=1;", encoding="utf-8")

    import sys

    argv = sys.argv
    sys.argv = ["patch_nbi.py", str(tmp_path / "*.js")]
    try:
        assert patcher.main() == 1
    finally:
        sys.argv = argv


def test_main_patches_and_reruns_cleanly(tmp_path):
    patcher = load_patcher_module()

    bundle = tmp_path / "chunk.js"
    bundle.write_text(BUNDLE_FIXTURE, encoding="utf-8")

    import sys

    argv = sys.argv
    sys.argv = ["patch_nbi.py", str(tmp_path / "*.js")]
    try:
        assert patcher.main() == 0
        assert patcher.SETTINGS_MARKER in bundle.read_text(encoding="utf-8")
        # Second run: already patched, still success.
        assert patcher.main() == 0
    finally:
        sys.argv = argv


def test_installed_labextension_is_patched():
    """In the container image, the shipped bundle must carry the patch."""
    patcher = load_patcher_module()

    bundles = glob.glob(patcher.DEFAULT_BUNDLE_GLOB)
    if not bundles:
        pytest.skip("notebook_intelligence labextension not installed here")

    assert any(
        patcher.SETTINGS_MARKER in Path(bundle).read_text(encoding="utf-8")
        for bundle in bundles
    ), "labextension bundle is missing the settings-refresh patch"
