---
title: OpenCode 2 migration, 31 October 2026
description: What OpenCode 2.2.5 changed for the image and how each integration was adapted
parent: index.md
status: implemented
last-reviewed: "2026-10-31"
---

# OpenCode 2 migration, 31 October 2026

The [container upgrade of the same day](container-upgrade-2026-10-31.md) held
OpenCode at 1.19.5. This record covers the move to 2.2.5. Each finding was
observed by running OpenCode 2.2.5 inside a Neurodesktop image, not taken from
release notes alone. Current behavior is described in
[coding agents](../architecture/coding-agents.md),
[ASTRA integration](../architecture/astra.md), and
[Jupyter AI](../architecture/jupyter-ai.md).

## What changed upstream, and the adaptation

| Area | OpenCode 2 behavior | Adaptation |
| --- | --- | --- |
| Installer | `https://opencode.ai/install` refuses every 2.x version | Install from `https://opencode.ai/v2/install`; remove its leftover `~/.opencode` and `opencode2` shim |
| Plugins | 1.x plugin modules do not run at all; plugins are `{ id, setup(ctx) }` default exports | Port both plugins to the 2.x API |
| Lightcone hooks | System prompt edits move to the session `context` hook; tool results to tool `execute.after`; file tools take `path` | Push a system text part, append text to the completed tool result, resolve `path` against the location |
| Ollama overlay | Configured providers are applied after provider transforms, so a plugin cannot change a configured endpoint; `editor.remove` has no effect on configured models | Apply the snapshot in a per-location model transform: add discovered models, put the endpoint on each model, and disable a stale seed. The Python helper now only observes Ollama and passes the managed endpoints and seeds in the snapshot |
| Background server | TUI and one-shot commands attach to a shared server that outlives them and keeps its first environment | The terminal wrapper appends `--standalone` |
| `serve` | Prints `server listening on URL` and requires a password; the 1.x HTTP routes are gone, so the API lives under `/api` | Container tests use `OPENCODE_PASSWORD` and the `/api/info`, `/api/model`, `/api/model/default` and `/api/skill` routes |
| `acp` | Same protocol, but exits on end of input before answering | Tests keep stdin open as real clients do |
| Skills | `debug skill` is gone and skill indexing is asynchronous | The skills test polls `/api/skill` |
| TUI state | `kv.json` and `tui.json` migrate once into `cli.json` (`session.sidebar`) | Seed `kv.json` before the migration and `cli.json` after it |
| Session database | Sessions move to `session_v2`; an upgraded home keeps the 1.x `session` table too; `job` and `event_sequence` have no session foreign key | Prune both session tables and sweep `job` and `event_sequence` rows by session id |
| Configuration | The 1.x `opencode.json` layout is normalized on load and left on disk; `opencode mcp add` writes `mcp.servers` entries beside 1.x ones | No change to the helpers, which keep writing the 1.x layout |

## Clients

T3 Code 0.1.5 already detects OpenCode 2 from `--version` and uses its own
OpenCode 2 client, which supplies the server password and verifies
`/api/info`. No T3 change was needed. A real T3 server completes a turn through
the quiet launcher and a controlled Ollama fixture in the image tests.

`jupyter-ai-acp-client` 0.3.4 refuses OpenCode 2 in its persona version check,
although `opencode acp` still speaks the same protocol. The existing anchored
build-time patch now also widens that check to `<3`, and the image tests run a
full ACP session (initialize, new session, prompt) through the launcher.

## Known limits

- The Ollama overlay no longer keeps a configured default model visible when
  that model is the unchanged image seed and Ollama does not report it. The
  model transform cannot see the configured default.
- The helpers refresh only the 1.x `provider` block. A user who writes a 2.x
  `providers` block by hand keeps it, but the wrapper's model refresh does not
  edit it.
