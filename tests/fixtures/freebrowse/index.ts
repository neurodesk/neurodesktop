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

function freebrowseUrl(filePath: string): string {
  const baseUrl = PageConfig.getBaseUrl();
  const fileUrl = `${baseUrl}files/${filePath}`;
  if (filePath.toLowerCase().endsWith(".nvd")) {
    return `${baseUrl}freebrowse/?nvd=${fileUrl}`;
  }
  return `${baseUrl}freebrowse/?vol=${fileUrl}`;
}

/**
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

        window.open(freebrowseUrl(item.value.path), "_blank");
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
    });
    app.docRegistry.addWidgetFactory(factory);

    console.log("jupyterlab-freebrowse extension activated");
  },
};

export default plugin;
