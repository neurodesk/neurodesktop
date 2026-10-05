#!/usr/bin/env python3
"""Patch FreeBrowse's pinned Jupyter integration for authenticated local files."""

from pathlib import Path
import sys


def replace_once(path: Path, before: str, after: str) -> None:
    source = path.read_text()
    if source.count(before) != 1:
        raise ValueError(f"FreeBrowse upstream seam changed: {path.name}")
    path.write_text(source.replace(before, after))


def patch(root: Path) -> None:
    replace_once(
        root / "src/index.ts",
        '''  const fileUrl = `${baseUrl}files/${filePath}`;
  if (filePath.toLowerCase().endsWith(".nvd")) {
    return `${baseUrl}freebrowse/?nvd=${fileUrl}`;
  }
  return `${baseUrl}freebrowse/?vol=${fileUrl}`;''',
        '''  const encodedPath = filePath.split("/").map(encodeURIComponent).join("/");
  const fileUrl = `${baseUrl}files/${encodedPath}`;
  const parameter = filePath.toLowerCase().endsWith(".nvd") ? "nvd" : "vol";
  return `${baseUrl}freebrowse/?${parameter}=${encodeURIComponent(fileUrl)}`;''',
    )
    replace_once(
        root / "jupyterlab_freebrowse/handlers.py",
        "from tornado.web import StaticFileHandler",
        '''from tornado.web import StaticFileHandler, authenticated
from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.utils import url_path_join
import re


class FreeBrowseStaticHandler(JupyterHandler, StaticFileHandler):
    @authenticated
    async def get(self, path, include_body=True):
        await super().get(path, include_body)

    @authenticated
    async def head(self, path):
        await self.get(path, include_body=False)''',
    )
    replace_once(
        root / "jupyterlab_freebrowse/handlers.py",
        'route_pattern = rf"{base_url}freebrowse/(.*)"',
        'route_pattern = re.escape(url_path_join(base_url, "freebrowse")) + r"/(.*)"',
    )
    replace_once(
        root / "jupyterlab_freebrowse/handlers.py",
        "                StaticFileHandler,",
        "                FreeBrowseStaticHandler,",
    )


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
