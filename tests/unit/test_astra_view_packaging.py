"""Offline packaging contracts for the Neurodesktop ASTRA anywidget."""

import re

from testlib import repo_path


ROOT = repo_path("extensions/astra-viewer")
EXAMPLE = repo_path("tests/fixtures/astra-bet")
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


def test_frontend_renders_version_drift_warnings_before_errors():
    """The warning banner is the context for a validation failure, so it must
    render on the invalid path too — before the early return on errors."""
    javascript = (ROOT / "neurodesk_astra_view/static/index.js").read_text(
        encoding="utf-8"
    )
    stylesheet = (ROOT / "neurodesk_astra_view/static/style.css").read_text(
        encoding="utf-8"
    )

    assert javascript.index("graph.warnings") < javascript.index("graph.errors")
    assert "astra-warnings" in javascript
    assert ".astra-warnings" in stylesheet


def test_frontend_lays_out_from_the_model_ranks_on_every_filter_change():
    """The viewer ran one `breadthfirst` pass over the *whole* graph at mount
    and then only toggled visibility, so every mode inherited one compromise
    layout and a mode that hid a rank left the band of canvas behind."""
    javascript = (ROOT / "neurodesk_astra_view/static/index.js").read_text(
        encoding="utf-8"
    )

    assert 'name: "breadthfirst"' not in javascript
    assert 'name: "preset"' in javascript
    assert 'node.data("rank")' in javascript
    assert 'data("order")' in javascript
    # applyMode delegates to applyCollapsed, so laying out there covers a mode
    # switch, a collapse, and a re-render from one place.
    applied = javascript.index("const applyCollapsed")
    assert javascript.index("layoutVisible();", applied) > applied


def test_frontend_evidence_mode_filters_instead_of_showing_everything():
    """`evidence` fell off the end of the mode chain and cleared every filter,
    so the button looked inert on any spec whose claims it was meant to show,
    and identical to `decisions` on a spec with no claims at all."""
    javascript = (ROOT / "neurodesk_astra_view/static/index.js").read_text(
        encoding="utf-8"
    )
    stylesheet = (ROOT / "neurodesk_astra_view/static/style.css").read_text(
        encoding="utf-8"
    )

    assert 'mode === "evidence"' in javascript
    assert "CLAIM_KINDS" in javascript
    # A filter that matches nothing has to say so rather than silently
    # redrawing the previous picture.
    assert "astra-notice" in javascript
    assert ".astra-notice" in stylesheet
    assert "notice.hidden" in javascript


def test_frontend_shows_the_trust_message_rather_than_hiding_it_in_a_tooltip():
    javascript = (ROOT / "neurodesk_astra_view/static/index.js").read_text(
        encoding="utf-8"
    )
    stylesheet = (ROOT / "neurodesk_astra_view/static/style.css").read_text(
        encoding="utf-8"
    )

    assert "astra-trust-message" in javascript
    assert ".astra-trust-message" in stylesheet
    assert "graph.meta.analysis_name" in javascript

    # Trust describes a graph that got drawn. On the validation-failure path
    # there is no graph, so "Nothing here was executed" would be describing an
    # empty canvas — the badge belongs past the early return.
    assert javascript.index("graph.errors") < javascript.index("astra-trust-message")


def test_frontend_collapses_a_long_warning_banner():
    """A spec predating several schema changes warns once per analysis per
    change. Left expanded, that wall of yellow pushes the graph the reader
    came for off the screen, so past a handful they collapse behind a count."""
    javascript = (ROOT / "neurodesk_astra_view/static/index.js").read_text(
        encoding="utf-8"
    )
    stylesheet = (ROOT / "neurodesk_astra_view/static/style.css").read_text(
        encoding="utf-8"
    )

    assert "graph.warnings.length > 3" in javascript
    assert "schema warnings" in javascript
    assert ".astra-warnings summary" in stylesheet


def test_frontend_cleanup_removes_only_its_registered_model_listeners():
    """Each render registers its listeners exactly once, and the teardown
    callback it returns to anywidget deregisters exactly those listeners, so
    repeated mounts cannot accumulate stale handlers."""
    javascript = (ROOT / "neurodesk_astra_view/static/index.js").read_text(
        encoding="utf-8"
    )

    teardown = re.search(r"return \(\) => \{(?P<body>.*?)\n  \};", javascript, re.DOTALL)
    assert teardown, "render() must return a teardown callback"

    for event, callback in (
        ("change:mode", "applyMode"),
        ("change:collapsed", "applyCollapsed"),
        ("change:selected_node", "onSelectedNodeChange"),
    ):
        on_call = f'model.on("{event}", {callback})'
        off_call = f'model.off("{event}", {callback})'
        assert javascript.count(on_call) == 1
        assert javascript.count(off_call) == 1
        assert javascript.index(on_call) < teardown.start(), (
            f"{on_call} must register during render, not in the teardown"
        )
        assert off_call in teardown.group("body"), (
            f"{off_call} must run in the teardown callback"
        )
    assert "cy.destroy()" in teardown.group("body")


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
    assert "source=tests,target=/tmp/tests,ro" in DOCKERFILE
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


def test_adapter_is_the_only_schema_aware_viewer_module():
    package = ROOT / "neurodesk_astra_view"
    for path in package.glob("*.py"):
        if path.name == "adapter.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "astra.validation" not in text
        assert "from astra" not in text
