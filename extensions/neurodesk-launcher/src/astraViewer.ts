/**
 * Open `astra.yaml` from the file browser in the ASTRA provenance viewer.
 *
 * The notebook path to the viewer (`from neurodesk_astra_view import
 * AstraView`) requires a running Python kernel, which is the wrong demand for
 * "double-click the spec and look at it" — the same gesture that opens a NIfTI
 * volume in NiiVue. So this plugin registers a pattern file type for
 * `astra.yaml` / `*.astra.yaml` and a read-only document widget factory as its
 * default, which also routes agent-authored chat links to the spec here (the
 * workspace-links plugin opens with the `'default'` factory).
 *
 * The graph itself is still built by the Python package: a widget cannot run
 * the released ASTRA validators in the browser, so it asks the
 * `neurodesk_astra_view.serverext` extension for the JSON model
 * (`/neurodesk-astra-view/graph`) and for the exact frontend the anywidget
 * inlines (`/neurodesk-astra-view/asset/esm|css`). Reusing those assets over
 * HTTP — imported as a blob module and driven through a small stand-in for the
 * anywidget model — is what keeps this viewer and the notebook widget the same
 * code with no copy to drift. If the server extension is missing, the factory
 * is never registered against a working endpoint: the widget reports the
 * failure, and disabling this plugin degrades `astra.yaml` to the text editor.
 *
 * The notebook widget is handed its run evidence (`AstraView(spec, run=…)`);
 * a double-click has nobody to hand it one, so this viewer discovers it from
 * the spec's own directory. Without that the `run=` the server extension
 * accepts is never sent and every spec reads `spec-only` forever, whatever was
 * executed.
 */

import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import {
  ABCWidgetFactory,
  DocumentRegistry,
  DocumentWidget,
  IDocumentWidget
} from '@jupyterlab/docregistry';

import { PathExt, URLExt } from '@jupyterlab/coreutils';

import { Contents, ServerConnection } from '@jupyterlab/services';

import { Widget } from '@lumino/widgets';

export const ASTRA_FILE_TYPE = 'astra-yaml';
export const ASTRA_FACTORY = 'ASTRA Viewer';
/** Matched against the basename; only ASTRA specs, never ordinary YAML. */
export const ASTRA_FILENAME_PATTERN = '^astra\\.ya?ml$|\\.astra\\.ya?ml$';

/**
 * Run-evidence filenames recognised beside a spec, in the order
 * `neurodesk_astra_view.manifest._directory_run_file` lists them.
 *
 * Without this discovery the file browser never sends `run=`, so every spec
 * renders `spec-only` — a permanent grey "Not executed" badge no finished job
 * could ever change. A name only this side knows is a manifest that never
 * loads, so a unit test holds the two lists together.
 */
export const ASTRA_RUN_EVIDENCE_NAMES = [
  'run-manifest.json',
  'manifest.json',
  'status.json',
  'ro-crate-metadata.json'
];

const STYLE_ELEMENT_ID = 'neurodesk-astra-view-style';

/**
 * The subset of the anywidget model contract that `static/index.js` uses.
 *
 * There is no kernel behind the file-browser viewer, so `set` fires the
 * change listeners synchronously (as the anywidget model does client-side)
 * and `save_changes` has nothing left to sync. Exported for testing.
 */
export class RenderModel {
  constructor(state: Record<string, unknown>) {
    this._state = state;
  }

  get(key: string): any {
    return this._state[key];
  }

  set(key: string, value: unknown): void {
    this._state[key] = value;
    const listeners = this._listeners.get(`change:${key}`);
    if (listeners) {
      [...listeners].forEach(listener => listener());
    }
  }

  save_changes(): void {
    // No kernel to sync with; state is already applied.
  }

  on(event: string, listener: () => void): void {
    let listeners = this._listeners.get(event);
    if (!listeners) {
      listeners = new Set();
      this._listeners.set(event, listeners);
    }
    listeners.add(listener);
  }

  off(event: string, listener: () => void): void {
    this._listeners.get(event)?.delete(listener);
  }

  private _state: Record<string, unknown>;
  private _listeners = new Map<string, Set<() => void>>();
}

interface IViewerModule {
  render(options: {
    model: RenderModel;
    el: HTMLElement;
  }): (() => void) | void;
}

async function fetchViewerAsset(
  kind: 'esm' | 'css',
  settings: ServerConnection.ISettings
): Promise<string> {
  const url = URLExt.join(
    settings.baseUrl,
    'neurodesk-astra-view',
    'asset',
    kind
  );
  const response = await ServerConnection.makeRequest(url, {}, settings);
  if (!response.ok) {
    throw new Error(
      `the neurodesk_astra_view server extension did not serve the viewer ` +
        `${kind} (HTTP ${response.status})`
    );
  }
  return response.text();
}

let viewerModulePromise: Promise<IViewerModule> | null = null;

/**
 * Load the anywidget frontend once per page: inject its stylesheet and import
 * the served ESM (vendored Cytoscape + renderer) as a blob module.
 */
function loadViewerModule(
  settings: ServerConnection.ISettings
): Promise<IViewerModule> {
  if (!viewerModulePromise) {
    viewerModulePromise = (async () => {
      const [esm, css] = await Promise.all([
        fetchViewerAsset('esm', settings),
        fetchViewerAsset('css', settings)
      ]);
      if (!document.getElementById(STYLE_ELEMENT_ID)) {
        const style = document.createElement('style');
        style.id = STYLE_ELEMENT_ID;
        style.textContent = css;
        document.head.appendChild(style);
      }
      const blobUrl = URL.createObjectURL(
        new Blob([esm], { type: 'text/javascript' })
      );
      try {
        return (await import(/* webpackIgnore: true */ blobUrl)) as IViewerModule;
      } finally {
        URL.revokeObjectURL(blobUrl);
      }
    })().catch(error => {
      // A transient failure (server still starting) must not poison every
      // later open, so forget the rejected promise before rethrowing.
      viewerModulePromise = null;
      throw error;
    });
  }
  return viewerModulePromise;
}

async function fetchGraph(
  settings: ServerConnection.ISettings,
  spec: string,
  universe: string | null,
  run: string | null
): Promise<Record<string, unknown>> {
  const query: Record<string, string> = { spec };
  if (universe) {
    query.universe = universe;
  }
  if (run) {
    query.run = run;
  }
  const url =
    URLExt.join(settings.baseUrl, 'neurodesk-astra-view', 'graph') +
    URLExt.objectToQueryString(query);
  const response = await ServerConnection.makeRequest(url, {}, settings);
  if (!response.ok) {
    throw new Error(
      `the neurodesk_astra_view server extension did not answer for ` +
        `${spec} (HTTP ${response.status})`
    );
  }
  return response.json();
}

/**
 * The document content: a universe picker and a refresh above the graph.
 */
class AstraDocumentContent extends Widget {
  constructor(
    context: DocumentRegistry.Context,
    contents: Contents.IManager
  ) {
    super();
    this._context = context;
    this._contents = contents;
    this.addClass('nd-astra-document');

    const bar = document.createElement('div');
    bar.className = 'nd-astra-universe-bar';
    const label = document.createElement('label');
    label.textContent = 'Universe';
    this._universeSelect = document.createElement('select');
    this._universeSelect.addEventListener('change', () => {
      void this._reload();
    });
    label.appendChild(this._universeSelect);
    bar.appendChild(label);

    // Run evidence lands beside the spec when a job finishes, long after the
    // spec itself was last saved. `fileChanged` never fires for it, so
    // without this the badge stays grey until the tab is closed and reopened.
    const refresh = document.createElement('button');
    refresh.type = 'button';
    refresh.className = 'nd-astra-refresh';
    refresh.textContent = 'Refresh';
    refresh.title =
      'Re-read the spec directory. Run evidence written after a job finishes ' +
      'is picked up here.';
    refresh.addEventListener('click', () => {
      void this._refresh();
    });
    bar.appendChild(refresh);

    this._host = document.createElement('div');
    this._host.className = 'nd-astra-host';

    this.node.appendChild(bar);
    this.node.appendChild(this._host);

    void context.ready.then(async () => {
      if (this.isDisposed) {
        return;
      }
      await this._populateUniverses();
      await this._reload();
      // The viewer shares its context with the text editor: a save from
      // "Open With > Editor" re-renders the graph here.
      context.fileChanged.connect(this._onFileChanged, this);
    });
  }

  dispose(): void {
    if (this.isDisposed) {
      return;
    }
    this._context.fileChanged.disconnect(this._onFileChanged, this);
    this._disposeRender();
    super.dispose();
  }

  private _onFileChanged(): void {
    void this._reload();
  }

  /** Re-read the directory: new universes, and any run evidence since. */
  private async _refresh(): Promise<void> {
    await this._populateUniverses();
    await this._reload();
  }

  /**
   * Find run evidence beside the spec.
   *
   * Nothing there is the honest `spec-only` reading, so no `run=` is sent.
   * Two or more supported names is a directory nobody can read unambiguously;
   * rather than guess on this side, the directory itself goes to the server so
   * `manifest.py` raises the one ambiguity error both viewers already share.
   */
  private async _discoverRun(): Promise<string | null> {
    const directory = PathExt.dirname(this._context.path);
    let present: string[] = [];
    try {
      const listing = await this._contents.get(directory, { content: true });
      present = (listing.content as Contents.IModel[])
        .filter(
          entry =>
            entry.type === 'file' &&
            ASTRA_RUN_EVIDENCE_NAMES.includes(entry.name)
        )
        .map(entry => entry.name);
    } catch {
      return null;
    }
    if (present.length === 0) {
      return null;
    }
    // `dirname` of a spec at the workspace root is '', which is not a path
    // the server can confine; '.' resolves to the root itself.
    if (present.length > 1) {
      return directory || '.';
    }
    return PathExt.join(directory, present[0]);
  }

  /**
   * Offer the spec's sibling `universes/*.yaml` files, defaulting to the
   * first one: a bare spec renders every decision unresolved, which is the
   * less useful first look at an analysis that ships universes.
   */
  private async _populateUniverses(): Promise<void> {
    // A refresh must not throw the user back to the default universe, so a
    // selection that still exists survives the rebuild. On the first pass
    // there are no options yet and the default below applies.
    const previous =
      this._universeSelect.options.length > 0
        ? this._universeSelect.value
        : null;
    const directory = PathExt.join(
      PathExt.dirname(this._context.path),
      'universes'
    );
    let names: string[] = [];
    try {
      const listing = await this._contents.get(directory, { content: true });
      names = (listing.content as Contents.IModel[])
        .filter(
          entry =>
            entry.type === 'file' &&
            (entry.name.endsWith('.yaml') || entry.name.endsWith('.yml'))
        )
        .map(entry => entry.name)
        .sort();
    } catch {
      // No universes directory; the spec renders on its own.
    }
    if (this.isDisposed) {
      return;
    }
    this._universeSelect.replaceChildren();
    const specOnly = document.createElement('option');
    specOnly.value = '';
    specOnly.textContent = '(spec only)';
    this._universeSelect.appendChild(specOnly);
    for (const name of names) {
      const option = document.createElement('option');
      option.value = PathExt.join(directory, name);
      option.textContent = name;
      this._universeSelect.appendChild(option);
    }
    const restored =
      previous !== null &&
      Array.from(this._universeSelect.options).some(
        option => option.value === previous
      );
    if (restored) {
      this._universeSelect.value = previous as string;
    } else if (names.length > 0) {
      this._universeSelect.selectedIndex = 1;
    }
  }

  private async _reload(): Promise<void> {
    const sequence = ++this._reloadSequence;
    const settings = ServerConnection.makeSettings();
    // Keep the mode the user was looking at across universe switches and
    // on-disk changes.
    const mode = this._model ? this._model.get('mode') : 'flow';
    this._host.textContent = 'Loading ASTRA graph…';
    try {
      const run = await this._discoverRun();
      if (this.isDisposed || sequence !== this._reloadSequence) {
        return;
      }
      const [viewer, graph] = await Promise.all([
        loadViewerModule(settings),
        fetchGraph(
          settings,
          this._context.path,
          this._universeSelect.value || null,
          run
        )
      ]);
      if (this.isDisposed || sequence !== this._reloadSequence) {
        return;
      }
      this._disposeRender();
      this._host.replaceChildren();
      this._model = new RenderModel({
        graph,
        mode,
        selected_node: null,
        expanded: []
      });
      const cleanup = viewer.render({ model: this._model, el: this._host });
      this._cleanup = typeof cleanup === 'function' ? cleanup : null;
    } catch (error) {
      if (!this.isDisposed && sequence === this._reloadSequence) {
        this._disposeRender();
        this._host.textContent = `The ASTRA viewer could not render ${
          this._context.path
        }: ${error instanceof Error ? error.message : error}`;
      }
    }
  }

  private _disposeRender(): void {
    if (this._cleanup) {
      try {
        this._cleanup();
      } catch (error) {
        console.warn('neurodesk-launcher: ASTRA viewer cleanup failed', error);
      }
      this._cleanup = null;
    }
    this._model = null;
  }

  private _context: DocumentRegistry.Context;
  private _contents: Contents.IManager;
  private _universeSelect: HTMLSelectElement;
  private _host: HTMLElement;
  private _model: RenderModel | null = null;
  private _cleanup: (() => void) | null = null;
  private _reloadSequence = 0;
}

class AstraViewerFactory extends ABCWidgetFactory<
  IDocumentWidget<AstraDocumentContent>
> {
  constructor(
    options: DocumentRegistry.IWidgetFactoryOptions<
      IDocumentWidget<AstraDocumentContent>
    >,
    contents: Contents.IManager
  ) {
    super(options);
    this._contents = contents;
  }

  protected createNewWidget(
    context: DocumentRegistry.Context
  ): IDocumentWidget<AstraDocumentContent> {
    const content = new AstraDocumentContent(context, this._contents);
    return new DocumentWidget({ content, context });
  }

  private _contents: Contents.IManager;
}

const astraViewerPlugin: JupyterFrontEndPlugin<void> = {
  id: 'neurodesk-launcher:astra-viewer',
  description:
    'Open astra.yaml files from the file browser in the ASTRA provenance viewer',
  autoStart: true,
  activate: (app: JupyterFrontEnd) => {
    app.docRegistry.addFileType({
      name: ASTRA_FILE_TYPE,
      displayName: 'ASTRA Analysis',
      // Pattern only: an extensions entry would claim every ordinary .yaml.
      extensions: [],
      pattern: ASTRA_FILENAME_PATTERN,
      mimeTypes: ['text/yaml'],
      contentType: 'file',
      fileFormat: 'text'
    });
    const factory = new AstraViewerFactory(
      {
        name: ASTRA_FACTORY,
        modelName: 'text',
        fileTypes: [ASTRA_FILE_TYPE],
        defaultFor: [ASTRA_FILE_TYPE],
        readOnly: true
      },
      app.serviceManager.contents
    );
    app.docRegistry.addWidgetFactory(factory);
  }
};

export default astraViewerPlugin;
