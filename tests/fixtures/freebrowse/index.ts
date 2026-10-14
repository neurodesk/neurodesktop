import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin,
} from "@jupyterlab/application";
import {
  ABCWidgetFactory,
  DocumentRegistry,
  DocumentWidget,
} from "@jupyterlab/docregistry";
import { IFileBrowserFactory } from "@jupyterlab/filebrowser";
import { PageConfig } from "@jupyterlab/coreutils";
import { Widget } from "@lumino/widgets";
import { ServerConnection } from "@jupyterlab/services";

const COMMAND_ID = "freebrowse:open";

function isNiftiFile(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".nii") || lower.endsWith(".nii.gz");
}

function isNvdFile(name: string): boolean {
  return name.toLowerCase().endsWith(".nvd");
}

function isFreeBrowseFile(name: string): boolean {
  return isNiftiFile(name) || isNvdFile(name);
}

function freebrowseUrl(filePath: string, fileUrl: string): string {
  const baseUrl = PageConfig.getBaseUrl();
  const parameter = filePath.toLowerCase().endsWith(".nvd") ? "nvd" : "vol";
  const filename = filePath.split("/").pop() || filePath;
  return `${baseUrl}freebrowse/?${parameter}=${encodeURIComponent(fileUrl)}&filename=${encodeURIComponent(filename)}`;
}

class FreeBrowseWidget extends Widget {
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

const plugin: JupyterFrontEndPlugin<void> = {
  id: "jupyterlab-freebrowse:plugin",
  autoStart: true,
  requires: [IFileBrowserFactory],
  activate: (app: JupyterFrontEnd, fileBrowserFactory: IFileBrowserFactory) => {
    // --- Context menu: right-click "Open in FreeBrowse" ---
    app.commands.addCommand(COMMAND_ID, {
      label: "Open in FreeBrowse",
      execute: () => {
        const browser = fileBrowserFactory.tracker.currentWidget;
        if (!browser) return;

        const item = browser.selectedItems().next();
        if (item.done) return;

        return app.commands.execute("docmanager:open", {
          path: item.value.path,
          factory: "FreeBrowse",
        });
      },
      isVisible: () => {
        const browser = fileBrowserFactory.tracker.currentWidget;
        if (!browser) return false;

        const item = browser.selectedItems().next();
        if (item.done) return false;

        return isFreeBrowseFile(item.value.name);
      },
    });

    app.contextMenu.addItem({
      command: COMMAND_ID,
      selector: ".jp-DirListing-item",
      rank: 1,
    });

    // --- Double-click: register file types + widget factory ---
    app.docRegistry.addFileType({
      name: "nifti",
      displayName: "NIfTI Image",
      extensions: [".nii"],
      fileFormat: "base64",
    });
    app.docRegistry.addFileType({
      name: "nifti-gz",
      displayName: "NIfTI Image (compressed)",
      extensions: [".nii.gz"],
      fileFormat: "base64",
    });
    app.docRegistry.addFileType({
      name: "nvd",
      displayName: "NiiVue Document",
      extensions: [".nvd"],
      fileFormat: "base64",
    });

    const factory = new FreeBrowseFactory({
      name: "FreeBrowse",
      label: "FreeBrowse",
      modelName: "base64",
      fileTypes: ["nifti", "nifti-gz", "nvd"],
      defaultFor: ["nifti", "nifti-gz", "nvd"],
      readOnly: true,
    }, app.serviceManager);
    app.docRegistry.addWidgetFactory(factory);

    console.log("jupyterlab-freebrowse extension activated");
  },
};

export default plugin;
