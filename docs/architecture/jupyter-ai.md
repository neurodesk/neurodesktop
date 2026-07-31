---
title: Jupyter AI
description: The ACP-native Jupyter AI chat surface, its Claude/Codex/OpenCode
  personas, workspace seeding, and the collaboration-stack workarounds
parent: ../architecture.md
status: current
last-reviewed: "2026-07-31"
---

# Jupyter AI

Part of [Architecture](../architecture.md). Focused tests are listed in
[Testing](../testing.md#jupyter-ai-and-acp-personas).

Jupyter AI provides a second, ACP-native JupyterLab chat surface alongside
Notebook Intelligence. Its ACP client discovers the existing Claude, Codex,
and OpenCode commands and registers all three personas. Neurodesktop installs
pinned Codex and Claude ACP adapters; OpenCode uses its native `opencode acp`
transport. Machine-facing `opencode --version` and `opencode acp` calls bypass
the interactive terminal wrapper so their output remains protocol-safe. The
personas reuse each user's agent credentials and configuration; they do not replace
OpenCode Web, Notebook Intelligence, or Neurodesktop's existing model/API-key
configuration. The optional Jupyter AI magic and Jupyternaut extras are not
installed because their LiteLLM/prerelease constraints conflict with the
validated Neurodesktop stack.

## Chat workspace seeding

Jupyter AI creates a chat as a ``.chat`` file and passes the file's parent
directory to each ACP persona as its working directory. A Jupyter contents
post-save hook seeds that directory with an editable copy of ``/opt/AGENTS.md``
when the first chat is created there. It never overwrites an existing
``AGENTS.md`` and fails open so a missing seed or unwritable directory cannot
prevent the chat itself from being saved. This hook is necessary because the
ACP transports do not run the interactive Codex and OpenCode wrappers that
normally seed the file.

## Collaboration-stack workarounds

Jupyter AI 3.1.1 resolves Jupyter Collaboration 4.4.1 bundles whose published
metadata supports `@jupyter/ydoc` only through version 3. Neurodesktop rebuilds
the collaboration and document-provider frontends against its JupyterLab 4.6
YDoc 4.1.1 contract, then replaces only those two incompatible wheel artifacts.
The image test requires both to report `OK` in `jupyter labextension list
--verbose`.

`jupyter-server-documents` 0.3.2 has an upstream stale-client race in which one
queued update can terminate a chat room's background message processor.
Neurodesktop applies an exact-source, build-time workaround for upstream issue
271: missing-client lookup fails cleanly, and one rejected frame cannot stop the
rest of the room queue. The anchored patch intentionally fails the image build
if a future package release changes either source seam, forcing the workaround
to be reassessed rather than silently carried forward.
