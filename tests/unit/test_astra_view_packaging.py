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
    """Positions come from the rank/order pairs the Python model computed —
    one pair per view — and the renderer rebuilds the drawing on every mode
    switch, cluster expansion, and selection, so each filter re-lays out what
    it actually draws instead of inheriting one compromise layout."""
    javascript = (ROOT / "neurodesk_astra_view/static/index.js").read_text(
        encoding="utf-8"
    )

    # The rank arithmetic lives in layout.py; the renderer only maps it.
    assert 'mode === "evidence" ? "evidence_rank" : "rank"' in javascript
    assert 'mode === "evidence" ? "evidence_order" : "order"' in javascript
    assert "layoutVisible(" in javascript
    # Every model change redraws through draw(), which re-runs layoutVisible.
    drawn = javascript.index("const draw = ()")
    assert javascript.index("layoutVisible(visible", drawn) > drawn
    for handler in ("applyMode", "applyExpanded", "onSelectedNodeChange"):
        assert f"const {handler} = () => draw();" in javascript


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
        ("change:expanded", "applyExpanded"),
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
    assert "root.remove()" in teardown.group("body")


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


def test_adapter_is_the_only_schema_aware_viewer_module():
    package = ROOT / "neurodesk_astra_view"
    for path in package.glob("*.py"):
        if path.name == "adapter.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "astra.validation" not in text
        assert "from astra" not in text
