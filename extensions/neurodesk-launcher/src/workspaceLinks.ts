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
    const openWorkspacePath = async (path: string): Promise<void> => {
      try {
        // A directory cannot be opened as a document; reveal it instead.
        const model = await docManager.services.contents.get(path, {
          content: false
        });
        if (model.type === 'directory') {
          await app.commands.execute('filebrowser:go-to-path', { path });
          return;
        }
        await docManager.openOrReveal(path);
      } catch (reason) {
        void showErrorMessage(
          'Cannot open file',
          `${path} could not be opened: ${reason}`
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
