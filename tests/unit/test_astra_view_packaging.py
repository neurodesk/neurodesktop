"""Offline packaging contracts for the Neurodesktop ASTRA anywidget."""

import re

from testlib import repo_path


ROOT = repo_path("extensions/astra-viewer")
EXAMPLE = repo_path("tests/fixtures/astra-bet")
DOCKERFILE = repo_path("Dockerfile").read_text(encoding="utf-8")
UNIT_TEST_WORKFLOW = repo_path(".github/workflows/unit-tests.yml").read_text(
    encoding="utf-8"
)


def test_viewer_has_no_npm_builder_or_runtime_network_import():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    javascript = (ROOT / "neurodesk_astra_view/static/index.js").read_text(
        encoding="utf-8"
    )

    assert "hatch-jupyter-builder" not in pyproject
    assert "npm" not in pyproject
    # The W3C namespace URI is an inert identifier createElementNS requires,
    # not a network reference; nothing else may look like a URL.
    without_namespace = javascript.replace("http://www.w3.org/2000/svg", "")
    assert "http://" not in without_namespace
    assert "https://" not in without_namespace
    assert "fetch(" not in javascript


def test_the_renderer_is_self_contained_with_no_vendored_graph_library():
    """The frontend draws its own SVG; a graph library would be a second
    place for layout to happen and a megabyte of bundle to carry."""
    static = ROOT / "neurodesk_astra_view/static"

    assert not (static / "vendor").exists()
    assert sorted(path.name for path in static.iterdir()) == [
        "index.js",
        "style.css",
    ]
    for path in (ROOT / "neurodesk_astra_view").rglob("*.py"):
        assert "cytoscape" not in path.read_text(encoding="utf-8").lower(), path
    javascript = (static / "index.js").read_text(encoding="utf-8")
    assert "cytoscape" not in javascript.lower()
    assert "createElementNS" in javascript


def test_viewer_is_installed_without_resolving_its_own_dependencies():
    """`--no-deps` is what keeps the wheel from re-resolving the pinned stack.

    The pinned versions themselves are asserted against the installed packages
    in ``tests/container/test_astra_view_image.py``; repeating them as
    Dockerfile substrings here would only add a second place to edit.
    """
    assert "source=extensions/astra-viewer" in DOCKERFILE
    assert "pip install --no-deps /tmp/astra-viewer" in DOCKERFILE


def test_worked_example_is_shipped_from_tests_fixtures():
    assert (EXAMPLE / "astra.yaml").is_file()
    assert (
        "source=tests/fixtures/astra-bet,target=/tmp/tests/fixtures/astra-bet,ro"
        in DOCKERFILE
    )
    assert (
        "cp -a /tmp/tests/fixtures/astra-bet /opt/neurodesktop/examples/"
        in DOCKERFILE
    )
    assert "source=examples" not in DOCKERFILE


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


def test_unit_workflow_resolves_viewer_dependencies_from_package_metadata():
    assert "./extensions/astra-viewer" in UNIT_TEST_WORKFLOW
    for package in ("astra-spec", "astra-tools", "anywidget"):
        assert not re.search(rf"\b{re.escape(package)}==", UNIT_TEST_WORKFLOW)


def test_adapter_is_the_only_schema_aware_viewer_module():
    package = ROOT / "neurodesk_astra_view"
    for path in package.glob("*.py"):
        if path.name == "adapter.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "astra.validation" not in text
        assert "from astra" not in text
