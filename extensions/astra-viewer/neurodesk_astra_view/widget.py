"""Thin anywidget renderer over the pure ASTRA graph model."""

from __future__ import annotations

from pathlib import Path

import anywidget
import traitlets

from .graph import build_graph


_STATIC = Path(__file__).with_name("static")
_ESM = (_STATIC / "index.js").read_text(encoding="utf-8")
_CSS = (_STATIC / "style.css").read_text(encoding="utf-8")


class AstraView(anywidget.AnyWidget):
    """Interactive, read-only ASTRA provenance graph."""

    _esm = _ESM
    _css = _CSS

    graph = traitlets.Dict().tag(sync=True)
    mode = traitlets.Enum(("flow", "decisions", "evidence"), default_value="flow").tag(
        sync=True
    )
    selected_node = traitlets.Unicode(allow_none=True, default_value=None).tag(sync=True)
    #: Decision clusters the reader has expanded; everything starts collapsed.
    expanded = traitlets.List(traitlets.Unicode(), default_value=[]).tag(sync=True)

    def __init__(
        self,
        spec,
        *,
        universe=None,
        run=None,
        mode="flow",
        **kwargs,
    ):
        graph = build_graph(spec, universe, run)
        super().__init__(graph=graph, mode=mode, **kwargs)

