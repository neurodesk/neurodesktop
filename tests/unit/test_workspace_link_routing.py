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
    assert "export default [plugin, workspaceLinksPlugin];" in index
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
    assert "docManager.openOrReveal(path)" in SOURCE


def test_an_unopenable_path_reports_instead_of_silently_doing_nothing():
    """The click is already cancelled, so a failure has to surface."""
    assert "showErrorMessage(" in SOURCE
