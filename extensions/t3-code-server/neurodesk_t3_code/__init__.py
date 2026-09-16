"""Jupyter Server extension that owns the T3 Code sidecar."""


def _jupyter_server_extension_points():
    from .serverext import NeurodeskT3CodeApp

    return [{"module": "neurodesk_t3_code", "app": NeurodeskT3CodeApp}]
