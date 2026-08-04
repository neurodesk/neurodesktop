"""The shipped Notebook Intelligence must carry the build-time patches.

The patcher's own logic is unit-tested in
``tests/unit/test_nbi_settings_patch.py``; this asserts the build actually
applied it to the labextension bundle and the Ollama provider module that
ended up in the image.
"""

import glob
from pathlib import Path

from testlib import load_source_module


def load_patcher():
    return load_source_module(
        "nbi_patch",
        "/opt/neurodesktop/patch_nbi.py",
        "config/agents/patch_nbi.py",
    )


def test_installed_labextension_is_patched():
    patcher = load_patcher()

    bundles = glob.glob(patcher.DEFAULT_BUNDLE_GLOB)
    assert bundles, (
        f"no notebook_intelligence labextension bundle at {patcher.DEFAULT_BUNDLE_GLOB}"
    )

    assert any(
        patcher.SETTINGS_MARKER in Path(bundle).read_text(encoding="utf-8")
        for bundle in bundles
    ), "labextension bundle is missing the settings-refresh patch"


def test_installed_ollama_provider_is_patched():
    patcher = load_patcher()

    providers = glob.glob(patcher.DEFAULT_OLLAMA_PROVIDER_GLOB)
    assert providers, (
        f"no Ollama provider module at {patcher.DEFAULT_OLLAMA_PROVIDER_GLOB}"
    )

    for provider in providers:
        text = Path(provider).read_text(encoding="utf-8")
        assert patcher.OLLAMA_MARKER in text, (
            f"{provider} is missing the Ollama context-length fallback patch"
        )
        assert patcher.OLLAMA_ANCHOR not in text
