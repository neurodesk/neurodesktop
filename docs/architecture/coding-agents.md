---
title: Coding agents
description: Claude Code installation, the OpenCode terminal wrapper, and
  OpenCode session pruning
parent: ../architecture.md
status: current
last-reviewed: "2026-08-04"
---

# Coding agents

Part of [Architecture](../architecture.md). Related environment variables are
listed in [Environment variables](../environment-variables.md); focused tests
in [Testing](../testing.md#focused-tests-by-area). The ASTRA skill all
three agents share is described in [ASTRA integration](astra.md).

## Analysis workspace contract

The image installs [`config/agents/AGENTS.md`](../../config/agents/AGENTS.md)
as `/opt/AGENTS.md` and seeds it into agent-authored workspaces. It keeps
substantive data retrieval and neuroimaging computation in retained Slurm
scripts while allowing lightweight module, command-interface, dataset-metadata,
ASTRA, and file-format discovery in the active shell.

The contract has a bounded fast path: an explicit user tool choice is not
reopened, a conventional demonstration default is recorded in ASTRA without an
automatic blocking question, a matching worked project is reused, and short
same-resource universes may share one script or Slurm array. Jobs publish
validated temporary outputs atomically, and completion requires Slurm
accounting (`COMPLETED`, exit code `0:0`), inspected logs, and fresh expected
artifacts. The final report distinguishes a valid ASTRA specification, actual
script execution, and recognised run provenance; plain `sbatch` execution does
not turn the viewer's `spec-only` badge into run evidence.

Alongside the rules the contract carries the environment facts and schema
gotchas an agent cannot rediscover from the worked example: Lmod and notebook
module loading, the Miniconda/`pip`/`uv` install paths, retrying a missed
`module spider` with alternative spellings, and that `findings:` entries are
`Insight` objects, so the schema term to read is `astra spec insight` and
never `astra spec finding`. It also keeps data acquisition visible in the
graph — a download that exists only as a script leaves its input arriving from
nowhere, so the step is declared as an output with a recipe or named in the
input's `description`.

A browser-based "OpenCode Web" launcher tile — a rewriting reverse proxy
around `opencode web` — shipped until July 2026 and was removed. Upstream
OpenCode does not support base-path/reverse-proxy deployments
(anomalyco/opencode issue #7624; PR #28326 unmerged), so the tile depended on
regex-patching OpenCode's minified web bundle per pinned release, which was
not sustainable. The original design is recorded in
[OpenCode web interface plan](../designs/opencode-integration-plan.md);
reintroducing a web interface waits on an upstream solution.

## Claude Code

Claude Code is installed into `/opt/jovyan_defaults/.local/bin/claude` when the
image is built and is launched through `/usr/local/sbin/claude`. On each launch,
the wrapper replaces `~/.local/bin/claude` with a symlink to that image-owned
binary. Persistent homes therefore pick up the Claude version in a newly
deployed image without retaining a stale per-user binary or duplicating the
large executable. Claude's in-process auto-updater remains disabled because
version updates are managed by the container image.

The image seeds a user-level `~/.claude/settings.json` (from
[`config/agents/claude_settings.json`](../../config/agents/claude_settings.json))
that defaults the permission mode to `auto` and the effort level to `high` for
both the CLI and the Jupyter AI Claude persona; per-project
`.claude/settings.local.json` files seeded by the wrapper still layer the
Neurodesktop tool allowlist on top. Codex likewise defaults to
`model_reasoning_effort = "high"` in its seeded `~/.codex/config.toml`.

## OpenCode terminal wrapper

OpenCode is installed at image build time (pinned by the `OPENCODE_VERSION`
build argument) and launched through `/usr/local/sbin/opencode`
([config/agents/opencode](../../config/agents/opencode)). The wrapper probes
the available model providers (JetStream, local Ollama, llm.neurodesk.org),
interactively prompts for `NEURODESK_API_KEY` and persists it to `~/.bashrc`,
rewrites `~/.config/opencode/opencode.json`, optionally sets up the Brain
Researcher MCP token, and mirrors provider settings into Notebook
Intelligence via [nbi_setup.sh](../../config/agents/nbi_setup.sh) before
exec-ing the real binary as a TUI. Its Lightcone reproduction skills and the
native adapter for their ASTRA hooks are described in
[ASTRA integration](astra.md#lightcone-agent-skills).

Session sharing is disabled by default in
[`config/agents/opencode_config.json`](../../config/agents/opencode_config.json)
(`"share": "disabled"`) so research conversations are not uploaded to the
OpenCode share service unless a user opts in.

The wrapper also seeds `~/.local/state/opencode/kv.json` with
`"sidebar": "hide"` so the TUI's right-hand session sidebar (context usage,
LSP status) starts hidden and the full width goes to the conversation.
OpenCode persists the `ctrl+x b` toggle under the same key, so the wrapper
only writes it when absent and a user who re-enables the sidebar keeps that
choice.

## OpenCode session pruning

OpenCode keeps session history in `~/.local/share/opencode/opencode.db`, not in
the working directory, and never prunes it. Deleting a project directory
therefore leaves its sessions in OpenCode's session index forever, pointing at
paths that are gone.

[`config/agents/opencode_prune_sessions.py`](../../config/agents/opencode_prune_sessions.py)
(installed as `/opt/neurodesktop/opencode_prune_sessions.py`) removes sessions
whose working directory no longer exists.
[`jupyterlab_startup.sh`](../../config/jupyter/jupyterlab_startup.sh) runs it with
`--apply` once per container start; run it by hand without `--apply` for a dry
run. `NEURODESKTOP_OPENCODE_PRUNE_SESSIONS=0` disables it.

Three details make the deletion safe and complete:

- **A missing directory is not enough.** The parent must still exist, which
  proves the filesystem is mounted and the directory really was removed. A
  session under a volume that is not mounted yet keeps its whole subtree
  missing and is left alone — startup ordering must never be able to destroy
  live history.
- **`PRAGMA foreign_keys` must be on.** SQLite leaves it off by default, so a
  plain `DELETE FROM session` orphans every cascading table (`message`,
  `todo`, `session_share`, `session_message`, `session_input`,
  `session_context_epoch`, and `part` via `message`).
- **`event` and `event_sequence` never cascade.** They key off the session id
  but declare no foreign key, so they are swept explicitly.

The pre-prune database is kept as a single rolling `opencode.db.prune-backup`
so an unattended startup cleanup cannot grow the home directory without bound.
