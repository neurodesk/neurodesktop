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
    replace_once(source, 'import { Widget } from "@lumino/widgets";',
                 'import { Widget } from "@lumino/widgets";\nimport { ServerConnection } from "@jupyterlab/services";')
    replace_once(root / "package.json", '    "@jupyterlab/filebrowser": "^4.0.0",',
                 '    "@jupyterlab/filebrowser": "^4.0.0",\n    "@jupyterlab/services": "^7.0.0",')
    replace_once(source, 'function freebrowseUrl(filePath: string): string {',
                 'function freebrowseUrl(filePath: string, fileUrl: string): string {')
    replace_once(source, '      readOnly: true,\n    });',
                 '      readOnly: true,\n    }, app.serviceManager);')
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
  private request = new AbortController();
  private objectUrl: string | undefined;

  constructor(
    private readonly context: DocumentRegistry.IContext<DocumentRegistry.IModel>,
    private readonly services: JupyterFrontEnd["serviceManager"]
  ) {
    super();
    this.frame = document.createElement("iframe");
    this.frame.title = "FreeBrowse";
    this.frame.style.cssText = "width:100%;height:100%;border:0;display:block";
    this.node.appendChild(this.frame);
    void this.updateUrl();
    context.pathChanged.connect(this.updateUrl, this);
  }

  private async updateUrl(): Promise<void> {
    this.request.abort();
    const request = this.request = new AbortController();
    const path = this.context.path;
    try {
      const url = await this.services.contents.getDownloadUrl(path);
      if (request.signal.aborted) return;
      const response = await ServerConnection.makeRequest(
        url, { method: "GET", signal: request.signal }, this.services.serverSettings
      );
      if (!response.ok || response.headers.get("content-type")?.includes("text/html")) {
        throw new Error("File download failed");
      }
      const blob = await response.blob();
      if (request.signal.aborted) return;
      const previous = this.objectUrl;
      this.objectUrl = URL.createObjectURL(blob);
      this.node.replaceChildren(this.frame);
      this.frame.src = freebrowseUrl(path, this.objectUrl);
      if (previous) URL.revokeObjectURL(previous);
    } catch {
      if (request.signal.aborted) return;
      this.frame.src = "about:blank";
      if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
      this.objectUrl = undefined;
      this.node.textContent = "FreeBrowse could not load this file through Jupyter.";
    }
  }

  dispose(): void {
    if (this.isDisposed) return;
    this.context.pathChanged.disconnect(this.updateUrl, this);
    this.request.abort();
    this.frame.src = "about:blank";
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    super.dispose();
  }
}

class FreeBrowseFactory extends ABCWidgetFactory<
  DocumentWidget<FreeBrowseWidget>,
  DocumentRegistry.IModel
> {
  constructor(
    options: DocumentRegistry.IWidgetFactoryOptions<DocumentWidget<FreeBrowseWidget>>,
    private readonly services: JupyterFrontEnd["serviceManager"]
  ) {
    super(options);
  }

  protected createNewWidget(
    context: DocumentRegistry.IContext<DocumentRegistry.IModel>
  ): DocumentWidget<FreeBrowseWidget> {
    return new DocumentWidget({ content: new FreeBrowseWidget(context, this.services), context });
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
        '''  const parameter = filePath.toLowerCase().endsWith(".nvd") ? "nvd" : "vol";
  const filename = filePath.split("/").pop() || filePath;
  return `${baseUrl}freebrowse/?${parameter}=${encodeURIComponent(fileUrl)}&filename=${encodeURIComponent(filename)}`;''',
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


def patch_frontend(root: Path) -> None:
    loader = root / "src/hooks/use-file-loading.ts"
    replace_once(loader,
                 'filename: nvdParam.split("/").pop() || nvdParam,',
                 'filename: urlParams.get("filename") || nvdParam.split("/").pop() || nvdParam,')
    replace_once(loader,
                 'const filename = volParam.split("/").pop() || volParam;',
                 'const filename = urlParams.get("filename") || volParam.split("/").pop() || volParam;')


if __name__ == "__main__":
    root = Path(sys.argv[1])
    patch(root)
    patch_frontend(root.parent / "frontend")
