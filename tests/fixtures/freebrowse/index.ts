import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin,
} from "@jupyterlab/application";
import {
  ABCWidgetFactory,
  DocumentModel,
  DocumentRegistry,
  DocumentWidget,
} from "@jupyterlab/docregistry";
import { IFileBrowserFactory } from "@jupyterlab/filebrowser";
import { PageConfig } from "@jupyterlab/coreutils";
import { Widget } from "@lumino/widgets";
import { Contents, ServerConnection } from "@jupyterlab/services";
import type { FreeBrowseHandle } from "../embed/freebrowse-embed.js";

const COMMAND_ID = "freebrowse:open";
const FACTORY_NAME = "FreeBrowse";
const MODEL_NAME = "freebrowse";

type Services = JupyterFrontEnd["serviceManager"];
type EmbedModule = typeof import("../embed/freebrowse-embed.js");

let embedModulePromise: Promise<EmbedModule> | null = null;

function loadFreeBrowseEmbed(): Promise<EmbedModule> {
  if (!embedModulePromise) {
    embedModulePromise = Promise.all([
      import(
        /* webpackChunkName: "freebrowse-viewer" */ "../embed/freebrowse-embed.js"
      ),
      import(
        /* webpackChunkName: "freebrowse-viewer-style" */ "../embed/freebrowse-embed.css"
      ),
    ])
      .then(([mod]) => mod)
      .catch((err) => {
        embedModulePromise = null;
        throw err;
      });
  }
  return embedModulePromise;
}

// ---------------------------------------------------------------------------
// Deployment settings (server-side, see jupyterlab_freebrowse/config.py)
// ---------------------------------------------------------------------------

/** Where a file opens: a FreeBrowse document tab inside JupyterLab, or a new browser tab. */
export type OpenIn = "document" | "browser";

/**
 * Who fetches the file: "blob" = this page downloads it through Jupyter's
 * authenticated connection and hands the viewer an in-memory copy;
 * "url" = the viewer fetches it from Jupyter's /files/ endpoint itself.
 */
export type FileAccess = "blob" | "url";

export interface ISettings {
  openIn: OpenIn;
  fileAccess: FileAccess;
}

/**
 * Read the settings the server extension published through page config.
 * Unknown or missing values fall back to the defaults. Notebook 7 opens
 * every document in its own browser tab already, so it is always "document".
 */
export function readSettings(): ISettings {
  const notebookPage = PageConfig.getOption("notebookPage");
  const openIn: OpenIn =
    !notebookPage && PageConfig.getOption("freebrowseOpenIn") === "browser"
      ? "browser"
      : "document";
  const fileAccess: FileAccess =
    PageConfig.getOption("freebrowseFileAccess") === "url" ? "url" : "blob";
  return { openIn, fileAccess };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isNiftiFile(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".nii") || lower.endsWith(".nii.gz");
}

function isMghFile(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".mgh") || lower.endsWith(".mgz");
}

function isNvdFile(name: string): boolean {
  return name.toLowerCase().endsWith(".nvd");
}

function isFreeBrowseFile(name: string): boolean {
  return isNiftiFile(name) || isMghFile(name) || isNvdFile(name);
}

function baseName(filePath: string): string {
  return filePath.split("/").pop() || filePath;
}

function freebrowseUrl(filePath: string, fileUrl: string): string {
  const baseUrl = PageConfig.getBaseUrl();
  const parameter = isNvdFile(filePath) ? "nvd" : "vol";
  return `${baseUrl}freebrowse/?${parameter}=${encodeURIComponent(fileUrl)}&filename=${encodeURIComponent(baseName(filePath))}`;
}

function abortError(): Error {
  return Object.assign(new Error("The FreeBrowse request was aborted"), {
    name: "AbortError",
  });
}

/**
 * Remove the `_xsrf` query parameter from a `/files/` download URL before
 * passing it to `ServerConnection.makeRequest`, which already attaches
 * `X-XSRFToken` as an HTTP header (and `Authorization` when token-authenticated).
 * This keeps `_xsrf` out of server/proxy access logs for document-mode reads.
 */
function stripXsrfQueryParam(rawUrl: string): string {
  try {
    const parsed = new URL(rawUrl, "https://invalid.local");
    if (!parsed.searchParams.has("_xsrf")) return rawUrl;
    parsed.searchParams.delete("_xsrf");
    if (/^https?:\/\//i.test(rawUrl)) {
      return parsed.toString();
    }
    return parsed.pathname + parsed.search + parsed.hash;
  } catch {
    return rawUrl;
  }
}

/** A URL the viewer can load (browser-tab mode), plus how to release it once no longer needed. */
export interface IResolvedFile {
  url: string;
  /** Present for blob: URLs; absent when the viewer fetches the file itself. */
  revoke?: () => void;
}

/**
 * Fetch a Jupyter contents file as a named `File` through Jupyter's
 * authenticated `ServerConnection`, without creating an object URL or
 * placing `_xsrf` in the outgoing request URL.
 */
async function fetchFileFromJupyter(
  path: string,
  services: Services,
  signal: AbortSignal
): Promise<File> {
  const downloadUrl = await services.contents.getDownloadUrl(path);
  if (signal.aborted) throw abortError();
  const requestUrl = stripXsrfQueryParam(downloadUrl);
  const response = await ServerConnection.makeRequest(
    requestUrl,
    { method: "GET", signal },
    services.serverSettings
  );
  if (
    !response.ok ||
    response.headers.get("content-type")?.includes("text/html")
  ) {
    throw new Error("File download failed");
  }
  const blob = await response.blob();
  if (signal.aborted) throw abortError();
  return new File([blob], baseName(path), {
    type: blob.type || "application/octet-stream",
  });
}

/**
 * Turn a Jupyter contents path into a URL for browser-tab mode, according to
 * the `fileAccess` setting. Throws an AbortError if `signal` fires before a
 * blob is created, so an aborted call never leaks an object URL.
 */
export async function resolveFileUrl(
  path: string,
  services: Services,
  fileAccess: FileAccess,
  signal: AbortSignal
): Promise<IResolvedFile> {
  const downloadUrl = await services.contents.getDownloadUrl(path);
  if (signal.aborted) throw abortError();

  if (fileAccess === "url") {
    // Keep the _xsrf query parameter getDownloadUrl appended: jupyter_server
    // runs check_xsrf_cookie on /files/ GETs and otherwise falls back to a
    // Referer check, which the viewer's worker fetch fails ("Blocking request
    // with no referer"). Without the cookie the parameter only earns a login
    // redirect, so it is not a credential (JupyterLab's own Download command
    // puts it in the address bar the same way).
    return { url: downloadUrl };
  }

  const requestUrl = stripXsrfQueryParam(downloadUrl);
  const response = await ServerConnection.makeRequest(
    requestUrl,
    { method: "GET", signal },
    services.serverSettings
  );
  if (
    !response.ok ||
    response.headers.get("content-type")?.includes("text/html")
  ) {
    throw new Error("File download failed");
  }
  const blob = await response.blob();
  if (signal.aborted) throw abortError();
  const url = URL.createObjectURL(blob);
  return { url, revoke: () => URL.revokeObjectURL(url) };
}

/** Release a blob once the browser tab that is showing it has been closed. */
function revokeWhenClosed(tab: Window, revoke: () => void): void {
  // `pagehide` fires when about:blank is replaced and on viewer reloads, so
  // poll instead.
  const timer = window.setInterval(() => {
    if (tab.closed) {
      window.clearInterval(timer);
      revoke();
    }
  }, 1000);
}

// ---------------------------------------------------------------------------
// open_in = document: in-DOM FreeBrowse widget inside a JupyterLab / Notebook 7 tab
// ---------------------------------------------------------------------------

class FreeBrowseWidget extends Widget {
  private readonly host: HTMLDivElement;
  private request = new AbortController();
  private handle: FreeBrowseHandle | null = null;
  private readonly onPointerDown = (event: Event) => {
    const target = event.target as HTMLElement | null;
    if (!target) return;
    const interactive = target.closest?.(
      "input, textarea, select, button, a, [tabindex], [contenteditable]"
    );
    if (!interactive || interactive === this.node) {
      this.node.focus?.({ preventScroll: true });
    }
  };

  constructor(
    private readonly context: DocumentRegistry.IContext<DocumentRegistry.IModel>,
    private readonly services: Services,
    private readonly fileAccess: FileAccess
  ) {
    super();
    this.addClass("jp-FreeBrowseWidget");
    this.node.tabIndex = -1;
    this.node.dataset.lmSuppressShortcuts = "true";
    this.node.addEventListener("pointerdown", this.onPointerDown);
    this.host = document.createElement("div");
    this.host.style.cssText =
      "width:100%;height:100%;position:relative;overflow:hidden";
    this.node.appendChild(this.host);
    void this.updateDocument();
    context.pathChanged.connect(this.updateDocument, this);
  }

  private destroyHandle(): void {
    const current = this.handle;
    this.handle = null;
    if (current) {
      current.destroy();
    }
  }

  private async updateDocument(): Promise<void> {
    this.request.abort();
    const request = (this.request = new AbortController());
    const path = this.context.path;
    const isNvd = isNvdFile(path);

    try {
      // For volumes in `url` mode, hand Jupyter's download URL directly to the
      // mounted viewer so large volumes are not buffered twice. `.nvd` scenes
      // always load as bytes so FreeBrowse can validate that they are
      // self-contained before handing them to NiiVue.
      const useDirectVolumeUrl = this.fileAccess === "url" && !isNvd;

      const [embed, loaded] = await Promise.all([
        loadFreeBrowseEmbed(),
        useDirectVolumeUrl
          ? this.services.contents
              .getDownloadUrl(path)
              .then((url) => ({ kind: "url" as const, url }))
          : fetchFileFromJupyter(path, this.services, request.signal).then(
              (file) => ({ kind: "file" as const, file })
            ),
      ]);
      if (request.signal.aborted || this.isDisposed) return;

      this.destroyHandle();
      this.node.replaceChildren(this.host);
      this.host.replaceChildren();

      const handle = embed.mountFreeBrowse(this.host, {
        backend: null,
        persist: false,
        readUrlParams: false,
        exposeGlobal: false,
        allowExternalDocumentUrls: false,
        dragDrop: false,
      });
      this.handle = handle;

      if (loaded.kind === "url") {
        await handle.loadVolumeUrl(loaded.url, baseName(path), request.signal);
      } else {
        await handle.loadFile(loaded.file, request.signal);
      }
      if (request.signal.aborted || this.isDisposed) {
        if (this.handle === handle) {
          this.destroyHandle();
        } else {
          handle.destroy();
        }
        return;
      }
      handle.nv.resize?.();
    } catch (err) {
      if (request.signal.aborted || this.isDisposed) return;
      console.error("FreeBrowse failed to load file:", err);
      this.destroyHandle();
      const message =
        err instanceof Error &&
        (err.message.includes(".nvd") || err.message.includes("migrate-nvd"))
          ? err.message
          : "FreeBrowse could not load this file through Jupyter.";
      this.node.textContent = message;
    }
  }

  protected onResize(msg: Widget.ResizeMessage): void {
    super.onResize(msg);
    this.handle?.nv.resize?.();
  }

  dispose(): void {
    if (this.isDisposed) return;
    this.node.removeEventListener("pointerdown", this.onPointerDown);
    this.context.pathChanged.disconnect(this.updateDocument, this);
    this.request.abort();
    this.destroyHandle();
    super.dispose();
  }
}

// ---------------------------------------------------------------------------
// open_in = browser: a placeholder tab that hands the file to a new browser tab
// ---------------------------------------------------------------------------

class FreeBrowseBrowserTab extends Widget {
  /** The new tab while it is still blank; null once handed off or closed. */
  private tab: Window | null;
  private readonly request = new AbortController();
  /** Resolves true once the viewer tab has the file; false otherwise. */
  readonly opened: Promise<boolean>;

  constructor(
    context: DocumentRegistry.IContext<DocumentRegistry.IModel>,
    private readonly services: Services,
    private readonly fileAccess: FileAccess
  ) {
    super();
    // Must happen synchronously inside the user's click, or pop-up blockers
    // refuse it (JupyterLab's own filebrowser:open-browser-tab does the same).
    this.tab = window.open("about:blank", "_blank");
    if (this.tab) this.tab.opener = null;
    this.node.textContent = `Opening ${baseName(context.path)} in a new browser tab…`;
    this.opened = this.open(context.path);
  }

  private async open(path: string): Promise<boolean> {
    const { signal } = this.request;
    let file: IResolvedFile;
    try {
      file = await resolveFileUrl(path, this.services, this.fileAccess, signal);
    } catch {
      if (signal.aborted) return false;
      this.closeBlankTab();
      this.node.textContent =
        "FreeBrowse could not load this file through Jupyter.";
      return false;
    }
    if (signal.aborted) {
      file.revoke?.();
      return false;
    }
    const tab = this.tab;
    if (!tab || tab.closed) {
      file.revoke?.();
      this.tab = null;
      this.node.textContent =
        "FreeBrowse could not open a new browser tab. Allow pop-ups for this site and try again.";
      return false;
    }
    // Hand-off: from here on the viewer tab outlives this widget.
    this.tab = null;
    tab.location.replace(freebrowseUrl(path, file.url));
    if (file.revoke) revokeWhenClosed(tab, file.revoke);
    return true;
  }

  private closeBlankTab(): void {
    if (this.tab && !this.tab.closed) this.tab.close();
    this.tab = null;
  }

  dispose(): void {
    if (this.isDisposed) return;
    this.request.abort();
    this.closeBlankTab();
    super.dispose();
  }
}

// ---------------------------------------------------------------------------
// Document registry pieces
// ---------------------------------------------------------------------------

/**
 * A model factory with `fileFormat: null`, so the document context fetches the
 * file's metadata only (`api/contents/<path>?content=0`). The extension does
 * the one real download itself in "blob" mode, and none for volumes in "url"
 * mode. With the default base64 factory JupyterLab would first pull the whole
 * file as base64 JSON into the model and then download it again.
 */
class FreeBrowseModelFactory
  implements DocumentRegistry.IModelFactory<DocumentRegistry.IModel>
{
  readonly name = MODEL_NAME;
  readonly contentType: Contents.ContentType = "file";
  readonly fileFormat: Contents.FileFormat = null;
  private _isDisposed = false;

  get isDisposed(): boolean {
    return this._isDisposed;
  }

  dispose(): void {
    this._isDisposed = true;
  }

  createNew(): DocumentRegistry.IModel {
    return new DocumentModel();
  }

  preferredLanguage(): string {
    return "";
  }
}

class FreeBrowseFactory extends ABCWidgetFactory<
  DocumentWidget<Widget>,
  DocumentRegistry.IModel
> {
  constructor(
    options: DocumentRegistry.IWidgetFactoryOptions<DocumentWidget<Widget>>,
    private readonly services: Services,
    private readonly settings: ISettings
  ) {
    super(options);
  }

  protected createNewWidget(
    context: DocumentRegistry.IContext<DocumentRegistry.IModel>
  ): DocumentWidget<Widget> {
    const { openIn, fileAccess } = this.settings;
    if (openIn === "browser") {
      const content = new FreeBrowseBrowserTab(
        context,
        this.services,
        fileAccess
      );
      const widget = new DocumentWidget({ content, context });
      // The placeholder tab goes away only once the viewer tab has the file;
      // otherwise it stays open to show what went wrong.
      void content.opened.then((ok) => {
        if (ok && !widget.isDisposed) widget.close();
      });
      return widget;
    }
    return new DocumentWidget({
      content: new FreeBrowseWidget(context, this.services, fileAccess),
      context,
    });
  }
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

const plugin: JupyterFrontEndPlugin<void> = {
  id: "jupyterlab-freebrowse:plugin",
  autoStart: true,
  requires: [IFileBrowserFactory],
  activate: (app: JupyterFrontEnd, fileBrowserFactory: IFileBrowserFactory) => {
    const settings = readSettings();

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
          factory: FACTORY_NAME,
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

    // --- Double-click: register file types, model factory + widget factory ---
    app.docRegistry.addFileType({
      name: "nifti",
      displayName: "NIfTI Image",
      extensions: [".nii"],
      contentType: "file",
    });
    app.docRegistry.addFileType({
      name: "nifti-gz",
      displayName: "NIfTI Image (compressed)",
      extensions: [".nii.gz"],
      contentType: "file",
    });
    app.docRegistry.addFileType({
      name: "mgh",
      displayName: "FreeSurfer MGH Image",
      extensions: [".mgh"],
      contentType: "file",
    });
    app.docRegistry.addFileType({
      name: "mgz",
      displayName: "FreeSurfer MGH Image (compressed)",
      extensions: [".mgz"],
      contentType: "file",
    });
    app.docRegistry.addFileType({
      name: "nvd",
      displayName: "NiiVue Document",
      extensions: [".nvd"],
      contentType: "file",
    });

    app.docRegistry.addModelFactory(new FreeBrowseModelFactory());

    const factory = new FreeBrowseFactory(
      {
        name: FACTORY_NAME,
        label: FACTORY_NAME,
        modelName: MODEL_NAME,
        fileTypes: ["nifti", "nifti-gz", "mgh", "mgz", "nvd"],
        defaultFor: ["nifti", "nifti-gz", "mgh", "mgz", "nvd"],
        readOnly: true,
      },
      app.serviceManager,
      settings
    );
    app.docRegistry.addWidgetFactory(factory);

    console.log(
      `jupyterlab-freebrowse extension activated (open_in=${settings.openIn}, file_access=${settings.fileAccess})`
    );
  },
};

export default plugin;
