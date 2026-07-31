"""Offline packaging contracts for the Neurodesktop ASTRA anywidget."""

import re

from testlib import repo_path


ROOT = repo_path("extensions/astra-viewer")
DOCKERFILE = repo_path("Dockerfile").read_text(encoding="utf-8")


def test_viewer_has_no_npm_builder_or_runtime_network_import():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    javascript = (ROOT / "neurodesk_astra_view/static/index.js").read_text(
        encoding="utf-8"
    )

    assert "hatch-jupyter-builder" not in pyproject
    assert "npm" not in pyproject
    assert "http://" not in javascript
    assert "https://" not in javascript
    assert "fetch(" not in javascript


def test_cytoscape_is_vendored_with_its_license_header():
    vendor = ROOT / "neurodesk_astra_view/static/vendor/cytoscape.min.js"
    license_file = ROOT / "neurodesk_astra_view/static/vendor/LICENSE.cytoscape.txt"

    assert vendor.stat().st_size > 400_000
    assert "Copyright (c) 2016-2026, The Cytoscape Consortium" in vendor.read_text(
        encoding="utf-8"
    )[:1000]
    license_text = license_file.read_text(encoding="utf-8")
    assert "Copyright (c) 2016-2026, The Cytoscape Consortium" in license_text
    assert "Permission is hereby granted" in license_text
    assert "The above copyright notice and this permission notice" in license_text
    assert "OUT OF OR IN CONNECTION WITH THE SOFTWARE" in license_text


def test_frontend_cleanup_removes_only_its_registered_model_listeners():
    javascript = (ROOT / "neurodesk_astra_view/static/index.js").read_text(
        encoding="utf-8"
    )

    for event, callback in (
        ("change:mode", "applyMode"),
        ("change:collapsed", "applyCollapsed"),
        ("change:selected_node", "onSelectedNodeChange"),
    ):
        assert javascript.count(f'model.on("{event}", {callback})') == 1
        assert javascript.count(f'model.off("{event}", {callback})') == 1


def test_viewer_is_installed_without_resolving_its_own_dependencies():
    """`--no-deps` is what keeps the wheel from re-resolving the pinned stack.

    The pinned versions themselves are asserted against the installed packages
    in ``tests/container/test_astra_view_image.py``; repeating them as
    Dockerfile substrings here would only add a second place to edit.
    """
    assert "source=extensions/astra-viewer" in DOCKERFILE
    assert "pip install --no-deps /tmp/astra-viewer" in DOCKERFILE
    # The viewer revalidates receipts through the same implementation the CLI
    # uses, so the module has to be importable and not just on PATH.
    assert "/opt/neurodesktop/lib/neurodesktop_pilot_receipt.py" in DOCKERFILE


def test_viewer_pins_match_the_image_pins():
    """One pin, one place: the wheel and the image must not drift apart."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    for package, argument in (
        ("astra-spec", "ASTRA_SPEC_VERSION"),
        ("astra-tools", "ASTRA_TOOLS_VERSION"),
        ("anywidget", "ANYWIDGET_VERSION"),
    ):
        match = re.search(rf'ARG {argument}="([^"]+)"', DOCKERFILE)
        assert match, argument
        assert f'"{package}=={match.group(1)}"' in pyproject, package


def test_adapter_is_the_only_schema_aware_viewer_module():
    package = ROOT / "neurodesk_astra_view"
    for path in package.glob("*.py"):
        if path.name == "adapter.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "astra.validation" not in text
        assert "from astra" not in text
