"""Jupyter server extension behind the file-browser ASTRA viewer.

The JupyterLab widget factory registered by ``neurodesk-launcher`` cannot run
:func:`neurodesk_astra_view.graph.build_graph` in the browser, so this
extension answers two questions for it over authenticated HTTP:

``GET /neurodesk-astra-view/graph?spec=<path>[&universe=<path>][&run=<path>]``
    The complete viewer JSON model for a spec inside the Jupyter server root.
    Paths are workspace-relative; anything absolute, traversing, or resolving
    outside the server root is a 404 before any file is read. Inside the root,
    ``build_graph`` keeps its own confinement (reads stay under the ASTRA
    project root) and turns parse failures into a renderable invalid graph.

``GET /neurodesk-astra-view/asset/(esm|css)``
    The same frontend the anywidget inlines: the vendored Cytoscape bundle
    concatenated with ``static/index.js``, and ``static/style.css``. Serving
    them from this package is what keeps the file-browser viewer and the
    notebook widget pixel-for-pixel the same code with no second copy to
    drift.
"""

from __future__ import annotations

import json
from pathlib import Path

from jupyter_server.base.handlers import APIHandler, JupyterHandler
from jupyter_server.utils import url_path_join
from tornado import web

from .graph import build_graph

_STATIC = Path(__file__).with_name("static")


def _assets() -> dict[str, tuple[str, str]]:
    """The two frontend assets, read fresh so tests see the shipped files."""
    esm = (
        (_STATIC / "vendor" / "cytoscape.min.js").read_text(encoding="utf-8")
        + "\n"
        + (_STATIC / "index.js").read_text(encoding="utf-8")
    )
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    return {
        "esm": ("text/javascript; charset=utf-8", esm),
        "css": ("text/css; charset=utf-8", css),
    }


def resolve_confined(root: Path, raw: str) -> Path:
    """Map a workspace-relative request path to a real path under *root*.

    Raises ``ValueError`` rather than guessing: absolute paths, ``..``
    segments, and symlinks that resolve outside the root are all rejected
    before anything is read. The file itself may not exist yet --
    ``build_graph`` renders that as an invalid graph with the message.
    """
    if not raw:
        raise ValueError("empty path")
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"path escapes the workspace: {raw}")
    root = root.resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"path escapes the workspace: {raw}")
    return resolved


class AstraGraphHandler(APIHandler):
    """Build the viewer JSON model for a confined spec/universe/run triple."""

    @web.authenticated
    def get(self) -> None:
        root = Path(self.settings["server_root_dir"]).expanduser()
        spec = self.get_query_argument("spec")
        universe = self.get_query_argument("universe", default=None)
        run = self.get_query_argument("run", default=None)
        try:
            spec_path = resolve_confined(root, spec)
            universe_path = resolve_confined(root, universe) if universe else None
            run_path = resolve_confined(root, run) if run else None
        except ValueError as error:
            raise web.HTTPError(404, reason=str(error)) from error
        graph = build_graph(spec_path, universe_path, run_path)
        self.finish(json.dumps(graph))


class AstraAssetHandler(JupyterHandler):
    """Serve the packaged viewer frontend to the JupyterLab document widget."""

    @web.authenticated
    def get(self, name: str) -> None:
        content_type, body = _assets()[name]
        self.set_header("Content-Type", content_type)
        self.set_header("Cache-Control", "no-cache")
        self.finish(body)


def _jupyter_server_extension_points() -> list[dict[str, str]]:
    return [{"module": "neurodesk_astra_view.serverext"}]


def _load_jupyter_server_extension(server_app) -> None:
    web_app = server_app.web_app
    base_url = web_app.settings["base_url"]
    web_app.add_handlers(
        ".*$",
        [
            (
                url_path_join(base_url, "neurodesk-astra-view", "graph"),
                AstraGraphHandler,
            ),
            (
                url_path_join(base_url, "neurodesk-astra-view", "asset", "(esm|css)"),
                AstraAssetHandler,
            ),
        ],
    )


# Classic notebook-server style alias, kept for tooling that still looks it up.
load_jupyter_server_extension = _load_jupyter_server_extension
