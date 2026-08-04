"""Public API for the Neurodesktop ASTRA viewer."""

from .graph import build_graph

__all__ = ["AstraView", "build_graph"]
__version__ = "0.1.0"


def __getattr__(name):
    if name == "AstraView":
        from .widget import AstraView

        return AstraView
    raise AttributeError(name)

