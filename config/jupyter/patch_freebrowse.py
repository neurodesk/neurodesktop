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
    source = root / "src/index.ts"
    replace_once(
        source,
        '''/**
 * Minimal content widget used for double-click handling.
 * Opens FreeBrowse in a new browser tab, then auto-closes the JupyterLab tab.
 */
class FreeBrowseRedirect extends Widget {
  constructor(context: DocumentRegistry.IContext<DocumentRegistry.IModel>) {
    super();
    window.open(freebrowseUrl(context.path), "_blank");
  }
}

class FreeBrowseFactory extends ABCWidgetFactory<
  DocumentWidget<FreeBrowseRedirect>,
  DocumentRegistry.IModel
> {
  protected createNewWidget(
    context: DocumentRegistry.IContext<DocumentRegistry.IModel>
  ): DocumentWidget<FreeBrowseRedirect> {
    const content = new FreeBrowseRedirect(context);
    const widget = new DocumentWidget({ content, context });
    // Auto-close the JupyterLab tab since the viewer opened in a new browser tab
    setTimeout(() => widget.close(), 500);
    return widget;
  }
}
''',
        '''class FreeBrowseWidget extends Widget {
  private readonly frame: HTMLIFrameElement;

  constructor(private readonly context: DocumentRegistry.IContext<DocumentRegistry.IModel>) {
    super();
    this.frame = document.createElement("iframe");
    this.frame.title = "FreeBrowse";
    this.frame.style.cssText = "width:100%;height:100%;border:0;display:block";
    this.node.appendChild(this.frame);
    this.updateUrl();
    context.pathChanged.connect(this.updateUrl, this);
  }

  private updateUrl(): void {
    this.frame.src = freebrowseUrl(this.context.path);
  }

  dispose(): void {
    if (this.isDisposed) return;
    this.context.pathChanged.disconnect(this.updateUrl, this);
    this.frame.src = "about:blank";
    super.dispose();
  }
}

class FreeBrowseFactory extends ABCWidgetFactory<
  DocumentWidget<FreeBrowseWidget>,
  DocumentRegistry.IModel
> {
  protected createNewWidget(
    context: DocumentRegistry.IContext<DocumentRegistry.IModel>
  ): DocumentWidget<FreeBrowseWidget> {
    return new DocumentWidget({ content: new FreeBrowseWidget(context), context });
  }
}
''',
    )
    replace_once(
        source,
        '        window.open(freebrowseUrl(item.value.path), "_blank");',
        '''        return app.commands.execute("docmanager:open", {
          path: item.value.path,
          factory: "FreeBrowse",
        });''',
    )
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
