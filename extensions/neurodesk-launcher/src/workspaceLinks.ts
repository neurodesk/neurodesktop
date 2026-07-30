/**
 * Open in-workspace absolute paths in the JupyterLab main panel.
 *
 * Coding agents describe their work with absolute filesystem paths, so a chat
 * reply routinely contains markdown like
 *
 *     [ASTRA specification](/home/jovyan/lightcone/brainextraction/astra.yaml)
 *
 * The browser resolves that against the page origin, so clicking it navigates
 * away from JupyterLab to `http://<host>/home/jovyan/...`, which the Jupyter
 * server does not serve -- the user loses their session and gets a 404.
 *
 * JupyterLab's own rendermime link handling cannot fix this: its resolver only
 * rewrites *relative* URLs, and treats a leading-slash path as an absolute URL
 * to leave alone. So this intercepts the click instead, which also covers every
 * chat surface in the image (Jupyter AI, Notebook Intelligence, and anything
 * else rendering agent markdown) rather than one extension's renderer.
 *
 * Only paths that resolve inside the Jupyter server's root are claimed.
 * Anything else -- external links, `/lab/...` routes JupyterLab already
 * handles, paths outside the root -- is left to the browser.
 */

import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { showErrorMessage } from '@jupyterlab/apputils';

import { PageConfig } from '@jupyterlab/coreutils';

import { IDocumentManager } from '@jupyterlab/docmanager';

/**
 * Map an absolute filesystem path to a path relative to the server root.
 *
 * Returns `null` when the path is not inside the root, so the caller can leave
 * the link to the browser. Exported for testing.
 */
export function toWorkspaceRelativePath(
  pathname: string,
  serverRoot: string
): string | null {
  if (!serverRoot || !pathname.startsWith('/')) {
    return null;
  }

  // Either side may carry a trailing slash; a link to a directory commonly
  // does, and serverRoot is not guaranteed to be normalized.
  const root = serverRoot.replace(/\/+$/, '');
  const target = pathname.replace(/\/+$/, '');
  if (!root) {
    return null;
  }

  if (target === root) {
    return '';
  }
  if (!target.startsWith(root + '/')) {
    return null;
  }

  // Reject traversal rather than guessing what the author meant.
  const relative = target.slice(root.length + 1);
  if (!relative || relative.split('/').includes('..')) {
    return null;
  }
  return relative;
}

/**
 * Document factories that render a file rather than showing its source.
 *
 * An agent linking a report means the report, not its markup. Opening it with
 * the default factory gives the text editor, so a MySTRA report scaffolded by
 * `neurodesktop-astra-report` arrives as raw MyST and a built site arrives as
 * raw HTML. Both formats already have a rendering viewer in the image; this
 * just picks it.
 */
const RENDERED_FACTORIES = new Map<string, string>([
  ['.md', 'Markdown Preview'],
  ['.markdown', 'Markdown Preview'],
  ['.html', 'HTML Viewer'],
  ['.htm', 'HTML Viewer']
]);

/**
 * The rendering factory for *path*, or `null` to use the default.
 *
 * Exported for testing.
 */
export function renderedFactoryFor(path: string): string | null {
  const name = path.slice(path.lastIndexOf('/') + 1).toLowerCase();
  const dot = name.lastIndexOf('.');
  // `dot <= 0` also rejects a dotfile that is all extension, such as `.md`.
  if (dot <= 0) {
    return null;
  }
  return RENDERED_FACTORIES.get(name.slice(dot)) ?? null;
}

/**
 * Strip a trailing `:line` or `:line:column` reference from a path.
 *
 * Agents are trained to cite code as `path/to/file.py:42`, and that convention
 * follows them into chat links: an ASTRA run typically emits every file link
 * as `[Analysis report](/home/jovyan/project/results/report.md:1)`. The suffix
 * is a reference, not part of the filename, so the contents API answers 404
 * and the click reports a file that plainly exists as missing.
 *
 * Returns `null` when there is no such suffix, so the caller can tell "nothing
 * to retry" from "retry this".
 *
 * Exported for testing.
 */
export function stripLineReference(path: string): string | null {
  const match = /^(.*?):\d+(?::\d+)?$/.exec(path);
  return match && match[1] ? match[1] : null;
}

/**
 * Decide whether this click should be handled by JupyterLab at all.
 *
 * Exported for testing; keeps the DOM-dependent parts of `activate` thin.
 */
export function shouldClaimClick(event: MouseEvent): boolean {
  // Leave modified clicks alone: the user explicitly asked for a new tab or
  // window, and a download should stay a download.
  return !(
    event.button !== 0 ||
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey ||
    event.altKey ||
    event.defaultPrevented
  );
}

const plugin: JupyterFrontEndPlugin<void> = {
  id: 'neurodesk-launcher:workspace-links',
  description:
    'Open agent-authored absolute file paths in the JupyterLab main panel',
  autoStart: true,
  requires: [IDocumentManager],
  activate: (app: JupyterFrontEnd, docManager: IDocumentManager) => {
    const stat = (candidate: string) =>
      docManager.services.contents.get(candidate, { content: false });

    /**
     * Locate what the link actually refers to.
     *
     * The literal path is tried first, so a filename that really does contain
     * a colon keeps working; only a path the server does not have is
     * reinterpreted as a `file:line` reference.
     */
    const resolveTarget = async (path: string) => {
      try {
        return { path, model: await stat(path) };
      } catch (reason) {
        const withoutReference = stripLineReference(path);
        if (!withoutReference) {
          throw reason;
        }
        return { path: withoutReference, model: await stat(withoutReference) };
      }
    };

    const openWorkspacePath = async (requested: string): Promise<void> => {
      try {
        const { path, model } = await resolveTarget(requested);

        // A directory cannot be opened as a document; reveal it instead.
        if (model.type === 'directory') {
          await app.commands.execute('filebrowser:go-to-path', { path });
          return;
        }

        // Fall back to the default factory when the viewer is not registered,
        // so a disabled or missing extension degrades to the editor instead of
        // opening nothing at all.
        const factory = renderedFactoryFor(path);
        const widgetName =
          factory && docManager.registry.getWidgetFactory(factory)
            ? factory
            : 'default';
        await docManager.openOrReveal(path, widgetName);
      } catch (reason) {
        // Report what was clicked, not the rewritten candidate.
        void showErrorMessage(
          'Cannot open file',
          `${requested} could not be opened: ${reason}`
        );
      }
    };

    document.addEventListener(
      'click',
      (event: MouseEvent) => {
        if (!shouldClaimClick(event)) {
          return;
        }

        const target = event.target as HTMLElement | null;
        const anchor = target?.closest?.('a[href]') as HTMLAnchorElement | null;
        if (!anchor || anchor.hasAttribute('download')) {
          return;
        }

        let url: URL;
        try {
          url = new URL(anchor.href, document.baseURI);
        } catch {
          return;
        }
        if (url.origin !== window.location.origin) {
          return;
        }

        let pathname: string;
        try {
          pathname = decodeURIComponent(url.pathname);
        } catch {
          return;
        }

        const path = toWorkspaceRelativePath(
          pathname,
          PageConfig.getOption('serverRoot')
        );
        if (path === null) {
          return;
        }

        event.preventDefault();
        event.stopPropagation();
        void openWorkspacePath(path);
      },
      // Capture, so the link is claimed before a chat widget's own handler
      // navigates or the anchor's default action fires.
      true
    );
  }
};

export default plugin;
