"""Built-artifact contract for agent-authored workspace link routing.

The unit tier asserts the guards in the TypeScript source; this asserts that
the plugin survived the labextension build, that JupyterLab accepts it, and
that the two runtime facts it depends on actually hold in the image: the
server root it maps paths against, and the document manager it opens them with.
"""

import json
from pathlib import Path

from testlib import run_cmd


LABEXTENSION = Path(
    "/opt/conda/share/jupyter/labextensions/neurodesk-launcher"
)


def bundle_text():
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (LABEXTENSION / "static").glob("*.js")
    )


def test_workspace_link_plugin_survived_the_labextension_build():
    text = bundle_text()

    assert "neurodesk-launcher:workspace-links" in text
    # The launcher plugin must still be there: both are exported as one array,
    # and a mistake there silently drops one of them.
    assert "neurodesk-launcher:plugin" in text


def test_bundle_carries_the_routing_logic_rather_than_a_stub():
    text = bundle_text()

    assert "serverRoot" in text
    assert "openOrReveal" in text
    assert "filebrowser:go-to-path" in text


def test_bundle_resolves_agent_authored_file_line_references():
    """`file.md:1` is what an ASTRA run actually emits for every file link.

    Assert the regex literal, not the function name: the production build
    minifies identifiers away, so a name-based check would pass on any bundle.
    """
    assert r"^(.*?):\d+(?::\d+)?$" in bundle_text()


def test_bundle_carries_the_rendering_factory_names():
    text = bundle_text()

    assert "Markdown Preview" in text
    assert "HTML Viewer" in text
    assert "getWidgetFactory" in text


def test_the_named_viewers_are_registered_by_this_application():
    """The factory names are upstream strings, not names this repo controls.

    A JupyterLab upgrade that renamed either factory would make a clicked
    report fall back to the text editor -- correct, but silently wrong -- so
    pin the seam here rather than discovering it from a bug report.
    """
    package = json.loads(
        Path("/opt/conda/share/jupyter/lab/static/package.json").read_text()
    )
    dependencies = package["dependencies"]
    assert "@jupyterlab/markdownviewer-extension" in dependencies
    assert "@jupyterlab/htmlviewer-extension" in dependencies

    core = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in Path("/opt/conda/share/jupyter/lab/static").glob("*.js")
    )
    assert "Markdown Preview" in core
    assert "HTML Viewer" in core


def test_neither_rendering_viewer_is_disabled_in_page_config():
    disabled = json.loads(
        Path(
            "/opt/jovyan_defaults/.jupyter/labconfig/page_config.json"
        ).read_text()
    ).get("disabledExtensions", {})

    for extension in (
        "@jupyterlab/markdownviewer-extension",
        "@jupyterlab/htmlviewer-extension",
    ):
        assert not any(
            key == extension or key.startswith(f"{extension}:")
            for key in disabled
        ), extension


def test_document_manager_is_consumed_as_a_shared_module():
    """A privately bundled docmanager would open files into a detached shell."""
    package = json.loads((LABEXTENSION / "package.json").read_text())
    assert "@jupyterlab/docmanager" in package["dependencies"]

    # webpack module federation records the singleton consumption in the
    # bundle; a second private copy would not appear as a consumed share.
    text = bundle_text()
    assert "@jupyterlab/docmanager" in text


def test_jupyterlab_accepts_the_rebuilt_extension():
    code, output = run_cmd("jupyter labextension list --verbose", timeout=60)

    assert code == 0, output
    assert "neurodesk-launcher" in output
    neurodesk_lines = [
        line for line in output.splitlines() if "neurodesk-launcher" in line
    ]
    assert neurodesk_lines, output
    for line in neurodesk_lines:
        assert "not compatible" not in line, line


def test_page_config_still_publishes_the_server_root_the_plugin_reads():
    """The whole mapping rests on PageConfig exposing serverRoot.

    If a JupyterLab upgrade stopped publishing it, every workspace link would
    quietly fall through to the browser and 404 again, so pin the upstream
    seam rather than discovering it from a bug report.
    """
    code, output = run_cmd(
        "grep -rn 'serverRoot' "
        "$(python -c 'import jupyterlab_server, pathlib; "
        "print(pathlib.Path(jupyterlab_server.__file__).parent)')/handlers.py",
        timeout=60,
    )

    assert code == 0, output
    assert 'page_config.setdefault("serverRoot"' in output, output
