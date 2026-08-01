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
the interactive terminal wrapper so their output remains protocol-safe; the
`acp` branch still silently loads `NEURODESK_API_KEY` and `BR_MCP_TOKEN` from
`~/.bashrc` before exec, because the Jupyter server that spawns it never
sources that file and the `{env:...}` references in `opencode.json` would
otherwise resolve empty and fail auth in the chat. The
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
