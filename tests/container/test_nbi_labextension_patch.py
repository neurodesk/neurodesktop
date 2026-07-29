"""The shipped Notebook Intelligence bundle must carry the settings patch.

The patcher's own logic is unit-tested in
``tests/unit/test_nbi_settings_patch.py``; this asserts the build actually
applied it to the labextension that ended up in the image.
"""

import glob
from pathlib import Path

from testlib import load_source_module


def test_installed_labextension_is_patched():
    patcher = load_source_module(
        "nbi_patch",
        "/opt/neurodesktop/patch_nbi.py",
        "config/agents/patch_nbi.py",
    )

    bundles = glob.glob(patcher.DEFAULT_BUNDLE_GLOB)
    assert bundles, (
        f"no notebook_intelligence labextension bundle at {patcher.DEFAULT_BUNDLE_GLOB}"
    )

    assert any(
        patcher.SETTINGS_MARKER in Path(bundle).read_text(encoding="utf-8")
        for bundle in bundles
    ), "labextension bundle is missing the settings-refresh patch"
