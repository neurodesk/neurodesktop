"""Contract for routing agent-authored absolute paths into the main panel.

Agents describe their work with absolute filesystem paths, so chat replies
contain markdown like `[spec](/home/jovyan/project/astra.yaml)`. The browser
resolves that against the page origin and navigates away from JupyterLab to a
URL the Jupyter server does not serve, losing the session to a 404.

These assertions cover the guards that keep the click interception safe. The
path-mapping cases themselves are exercised against the built bundle in
`tests/container/test_workspace_link_routing_image.py`.
"""

from testlib import repo_path


EXTENSION = repo_path("extensions/neurodesk-launcher")
SOURCE = (EXTENSION / "src/workspaceLinks.ts").read_text(encoding="utf-8")


def test_plugin_is_registered_alongside_the_launcher():
    index = (EXTENSION / "src/index.ts").read_text(encoding="utf-8")

    assert "import workspaceLinksPlugin from './workspaceLinks';" in index
    assert (
        "export default [plugin, workspaceLinksPlugin, astraViewerPlugin];"
        in index
    )
    assert "id: 'neurodesk-launcher:workspace-links'" in SOURCE


def test_plugin_requires_the_document_manager():
    package = (EXTENSION / "package.json").read_text(encoding="utf-8")

    assert '"@jupyterlab/docmanager"' in package
    assert "requires: [IDocumentManager]" in SOURCE


def test_only_paths_inside_the_server_root_are_claimed():
    """Anything else must stay with the browser, including /lab routes."""
    assert "PageConfig.getOption('serverRoot')" in SOURCE
    assert "if (!target.startsWith(root + '/'))" in SOURCE
    assert "return null;" in SOURCE


def test_path_traversal_is_rejected_rather_than_normalized():
    assert "relative.split('/').includes('..')" in SOURCE


def test_cross_origin_links_are_left_alone():
    assert "if (url.origin !== window.location.origin)" in SOURCE


def test_modified_clicks_and_downloads_are_left_alone():
    """A ctrl/cmd click is an explicit request for a new tab."""
    for guard in (
        "event.button !== 0",
        "event.ctrlKey",
        "event.metaKey",
        "event.shiftKey",
        "event.altKey",
        "event.defaultPrevented",
    ):
        assert guard in SOURCE, guard
    assert "anchor.hasAttribute('download')" in SOURCE


def test_click_is_intercepted_in_the_capture_phase():
    """A chat widget's own handler must not navigate first."""
    listener = SOURCE[SOURCE.index("document.addEventListener("):]
    assert "'click'" in listener
    assert listener.index("true") < listener.index("};")


def test_directories_reveal_instead_of_failing_to_open_as_a_document():
    assert "model.type === 'directory'" in SOURCE
    assert "'filebrowser:go-to-path'" in SOURCE
    assert "docManager.openOrReveal(path, widgetName)" in SOURCE


def test_a_file_line_reference_is_not_treated_as_part_of_the_filename():
    """Agents cite code as `file.py:42`, and that follows them into chat links.

    An ASTRA run emits every file link that way, so without this the contents
    API answers 404 and a file that plainly exists is reported as missing.
    """
    assert "export function stripLineReference(" in SOURCE
    assert r"/^(.*?):\d+(?::\d+)?$/" in SOURCE
    # A bare `:1` has no path left to open.
    assert "match && match[1] ? match[1] : null" in SOURCE


def test_the_literal_path_is_tried_before_it_is_reinterpreted():
    """A filename may legally contain a colon; only a 404 earns a retry."""
    resolver = SOURCE[SOURCE.index("const resolveTarget ="):]
    assert resolver.index("await stat(path)") < resolver.index(
        "stripLineReference(path)"
    )
    assert "throw reason;" in resolver


def test_a_failure_reports_the_path_that_was_clicked():
    assert "`${requested} could not be opened: ${reason}`" in SOURCE


def test_an_unopenable_path_reports_instead_of_silently_doing_nothing():
    """The click is already cancelled, so a failure has to surface."""
    assert "showErrorMessage(" in SOURCE


def test_rendered_formats_open_in_a_viewer_rather_than_the_text_editor():
    """A linked report means the report, not its markup.

    The default factory for both formats is the editor, so a linked markdown
    report would arrive as raw markup and a built site as raw HTML.
    """
    for extension, factory in (
        ("'.md'", "'Markdown Preview'"),
        ("'.markdown'", "'Markdown Preview'"),
        ("'.html'", "'HTML Viewer'"),
        ("'.htm'", "'HTML Viewer'"),
    ):
        assert f"[{extension}, {factory}]" in SOURCE, extension


def test_an_unregistered_viewer_falls_back_to_the_default_factory():
    """A disabled extension must degrade to the editor, not open nothing."""
    assert "docManager.registry.getWidgetFactory(factory)" in SOURCE
    assert "'default'" in SOURCE


def test_the_factory_choice_is_extracted_for_testing():
    assert "export function renderedFactoryFor(" in SOURCE
    # A dotfile that is all extension has no extension to match on.
    assert "if (dot <= 0)" in SOURCE
