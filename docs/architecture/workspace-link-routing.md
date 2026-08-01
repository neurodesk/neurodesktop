---
title: Workspace link routing
description: How agent-authored absolute file links in chat surfaces are
  intercepted and opened inside JupyterLab instead of 404ing
parent: ../architecture.md
status: current
last-reviewed: "2026-07-31"
---

# Workspace link routing

Part of [Architecture](../architecture.md). Focused tests:
`pytest tests/unit/test_workspace_link_routing.py` on a checkout and
`pytest /opt/tests/test_workspace_link_routing_image.py` in the built image —
see [Testing](../testing.md#focused-tests-by-area).

Coding agents describe their work with absolute filesystem paths, so a chat
reply routinely contains markdown such as
`[spec](/home/jovyan/project/astra.yaml)`. The browser resolves that against
the page origin, navigates away from JupyterLab to
`http://<host>/home/jovyan/project/astra.yaml`, which the Jupyter server does
not serve, and the user loses their session to a 404.

JupyterLab's own rendermime link handling cannot fix this: its resolver only
rewrites *relative* URLs and treats a leading-slash path as an absolute URL to
leave alone. The `neurodesk-launcher:workspace-links` plugin therefore
intercepts the click instead, which covers every chat surface in the image
rather than one extension's renderer.

A click is claimed only when the link is same-origin, unmodified, not a
download, and resolves inside `PageConfig` `serverRoot`. Everything else —
external links, `/lab/...` routes JupyterLab already handles, paths outside
the root, ctrl/cmd-clicks asking for a new tab — is left to the browser. A
claimed path is opened with the document manager, or revealed in the file
browser when it is a directory; `..` segments are rejected rather than
normalized. Because a claimed click is already cancelled, a path that cannot
be opened reports an error instead of silently doing nothing.

Agents are trained to cite code as `path/to/file.py:42`, and that convention
follows them into chat links — an ASTRA run emits essentially every file link
as `[Analysis report](/home/jovyan/project/results/report.md:1)`. That suffix
is a reference, not part of the filename, so the contents API answers 404 and a
file that plainly exists is reported as missing. The literal path is therefore
tried first, and only a path the server does not have is retried with a
trailing `:line` or `:line:column` stripped. Trying the literal path first is
what keeps a filename that really does contain a colon working; a failure
reports the path that was clicked rather than the rewritten candidate.

A claimed file is opened with a rendering viewer when one exists for its
format: `.md` and `.markdown` open in `Markdown Preview`, and `.html` and
`.htm` open in `HTML Viewer`. An agent linking a report means the report,
not its markup, and the default factory for both formats is the text editor —
a linked markdown report would otherwise arrive as raw markup and a built
site as raw HTML. The factory names are upstream strings, so the plugin falls
back to the default factory when the viewer is not registered; a clicked link
then still opens something rather than nothing.
