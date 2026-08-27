---
title: Jupyter AI
description: The ACP-native Jupyter AI chat surface, its Claude/Codex/OpenCode
  personas, workspace seeding, and collaboration-stack workarounds
parent: ../architecture.md
status: current
last-reviewed: "2026-08-26"
---

# Jupyter AI

Part of [Architecture](../architecture.md). Focused tests are listed in
[Testing](../testing.md#jupyter-ai-and-acp-personas).

Jupyter AI provides a second, ACP-native JupyterLab chat surface alongside
Notebook Intelligence. Its ACP client discovers the existing Claude, Codex,
and OpenCode commands and registers all three personas. Neurodesktop installs
pinned Codex and Claude ACP adapters; OpenCode uses its native `opencode acp`
transport. Machine-facing `opencode --version` and `opencode acp` calls bypass
the interactive terminal wrapper so their output remains protocol-safe; the
`acp` branch still silently loads `NEURODESK_API_KEY` and `BR_MCP_TOKEN` from
`~/.bashrc` before exec, because the Jupyter server that spawns it never
sources that file and the `{env:...}` references in `opencode.json` would
otherwise resolve empty and fail auth in the chat. It also silently sources
`/usr/share/module.sh` before exec. Lmod exports its `module` and `ml`
functions, so OpenCode's non-interactive child Bash shells inherit the module
system even though they do not read `~/.bashrc`; all initialization output is
discarded so the ACP JSON-RPC stream stays clean. The
Codex persona starts sessions in "Agent (full access)" via the
`INITIAL_AGENT_MODE` variable exported in `environment_variables.sh`, because
codex-acp otherwise hardcodes its sandboxed "Agent" preset and ignores the
image's no-approval `~/.codex/config.toml` defaults; users can still switch
modes per chat. Its "Reasoning effort" selector starts on High because the
Codex app-server reports the `model_reasoning_effort = "high"` default seeded
in that same `config.toml`. The Claude persona starts in "Auto" permission
mode with Effort "High": claude-agent-acp resolves both from Claude Code's
own settings files, and the image seeds `permissions.defaultMode` and
`effortLevel` in the user-level `~/.claude/settings.json`
(from `config/agents/claude_settings.json`). Effort falls back to the model's
default when the selected model does not support a High level, and every
default remains a per-chat selection in each session's config options. The
personas reuse each user's agent credentials and configuration; they do not replace
Notebook Intelligence or Neurodesktop's existing model/API-key
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
normally seed the file. It is registered in
``config/jupyter/jupyter_server_config_extra.py``, appended to
``/etc/jupyter/jupyter_server_config.py`` at build time. Jupyter's config
system applies the trait more than once per startup (the shimmed extension
apps re-apply config), and jupyter_server warns on every ``post_save_hook``
reassignment even when the hook is unchanged, so the snippet suppresses
exactly that duplicate self-registration warning next to the single
registration site; a different hook overriding ours still warns.

## Collaboration stack and server workarounds

Jupyter AI 3.1.2 is installed with Jupyter Collaboration 4.4.2. The release
targets JupyterLab 4, but its published collaboration and document-provider
frontends support `@jupyter/ydoc` only through version 3. Neurodesktop rebuilds
those two bundles against the image's JupyterLab 4.6 YDoc 4.1.1 contract. The
image test requires both rebuilt extensions to report `OK` in `jupyter
labextension list --verbose`.

Jupyter AI 3.1.x requires `jupyter-server-documents`; removing the package or
disabling its server extension is not a supported fallback for this release.
Its server-side notebook execution sends cell outputs over the Yjs WebSocket,
while widget model comms still use the kernel WebSocket. Those independent
channels can deliver a widget view before its model, which `ipywidgets` 8.1.8
made permanent as `Error displaying widget: model not found` or `Loading
widget...`. Neurodesktop pins `ipykernel` 6.31.0 so widget comms stay on the
main shell instead of using JupyterLab's experimental per-target comm
subshells. Neurodesktop also pins `ipywidgets` 8.1.9 and
`jupyterlab_widgets` 3.0.17, whose widget manager waits briefly for late model
registration. Its upstream two-second bound is still too short for complex
layouts such as a ``VBox`` containing delayed ``HBox`` models. Neurodesktop
extends that bound to ten seconds and publishes the changed widget-manager
chunk and remote entry under new content-derived names. The image regression
emits fragmented carriage-return stream updates, delays a real ``HBox`` comm
for three seconds, and requires both the stream text and widget to render
without a YDoc output exception. Jupyter
AI 3.2 plans to make RTC optional, but Neurodesktop will not remove the stable
3.1 dependency by adopting an alpha release.

Notebook rooms disable the separate outputs service. In that path,
`jupyter-server-documents` 0.3.3 converts kernel messages to plain Python
dictionaries before appending them to the cell's CRDT output array. JupyterLab
expects each entry to remain a Y.Map and each stream output's ``text`` value to
remain a Y.Text. A plain dictionary fails when `appendStreamOutput()` calls the
missing map `get()` method; a Y.Map containing a plain string fails on the next
call to the missing text `insert()` method. Consecutive stream messages must
also update one CRDT output. Separate maps make JupyterLab merge the fragments
locally and write the same fragment back into the room, which duplicates text
when another client joins and can apply a stream delta before its output row.
The backend patch keeps the outputs-service representation unchanged, requests
CRDT maps, constructs CRDT stream text, and processes carriage returns while
coalescing each contiguous stdout or stderr run into one map.

The same server-side executor bypasses JupyterLab's normal code-cell execution
path, which marks a cell trusted before its outputs arrive. Without that state,
JupyterLab refuses unsafe rich renderers and displays an ipywidget's
``text/plain`` representation instead. The anchored server-documents patch
marks user-executed code cells trusted, and the image regression executes a
real ``IntSlider`` through JupyterLab to distinguish a rendered widget from its
plain Python representation. Because Jupyter serves federated extension assets
with a one-year immutable cache, the patch leaves the upstream bundle intact,
publishes the changed chunk and remote entry under new content-derived names,
and updates the extension manifest. Reusing the original hashed filename would
leave browsers that opened JupyterLab before the update on the broken code.

`jupyter-server-documents` 0.3.3 still has an upstream stale-client race in which one
queued update can terminate a chat room's background message processor.
Neurodesktop applies an exact-source, build-time workaround for upstream issue
271: missing-client lookup fails cleanly, and one rejected frame cannot stop the
rest of the room queue. The anchored patch intentionally fails the image build
if a future package release changes any backend or frontend seam, forcing the
workarounds to be reassessed rather than silently carried forward.

`jupyter-ai-acp-client` 0.2.1 logs every streamed message chunk at INFO — two
lines per chunk, where a chunk is often a few characters — plus one line per
tool-call start and per once-a-second progress tick, so a single persona reply
floods the Jupyter server log with thousands of lines. A second anchored
build-time patch (`patch_jupyter_ai_acp_client.py`) demotes those four
per-event log statements to DEBUG; rarer events such as permission requests
stay at INFO. Like the issue-271 patch, it fails the image build if a future
release changes any source seam.

`jupyter-server-mcp` 0.2.1 starts FastMCP through its own embedded HTTP
runner and prints the FastMCP ASCII banner unconditionally, ignoring
FastMCP's `show_server_banner` setting. The image exports
`FASTMCP_SHOW_SERVER_BANNER=0` and a third anchored build-time patch
(`patch_jupyter_server_mcp.py`) gates the banner call on that setting, so
the box no longer lands in the server log on every boot. It follows the same
fail-loud contract as the other anchored patches.
