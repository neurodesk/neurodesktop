---
title: Coding agents
description: Claude Code installation, the OpenCode terminal wrapper and web
  interface, and OpenCode session pruning
parent: ../architecture.md
status: current
last-reviewed: "2026-07-31"
---

# Coding agents

Part of [Architecture](../architecture.md). The original implementation plan
for the web interface is
[OpenCode web interface plan](../designs/opencode-integration-plan.md);
related environment variables are listed in
[Environment variables](../environment-variables.md#opencode-web); focused
tests in [Testing](../testing.md#focused-tests-by-area). The ASTRA skill all
three agents share is described in [ASTRA integration](astra.md).

## Claude Code

Claude Code is installed into `/opt/jovyan_defaults/.local/bin/claude` when the
image is built and is launched through `/usr/local/sbin/claude`. On each launch,
the wrapper replaces `~/.local/bin/claude` with a symlink to that image-owned
binary. Persistent homes therefore pick up the Claude version in a newly
deployed image without retaining a stale per-user binary or duplicating the
large executable. Claude's in-process auto-updater remains disabled because
version updates are managed by the container image.

## OpenCode Web Interface

The JupyterLab launcher exposes a "Scigent.ai" tile backed by a Jupyter
Server Proxy entry that runs
[`config/agents/opencode_web.py`](../../config/agents/opencode_web.py)
(installed to `/opt/neurodesktop/opencode_web.py`). The launcher script:

- requires a persistent per-user credential on every request. The credential
  lives in `~/.neurodesk/secrets/opencode_server_password` (created 0600 and
  atomically by a shared helper, whichever of `jupyter_notebook_config.py`
  or the script runs first); Jupyter Server Proxy injects it via
  `request_headers_override`, so the browser never sees a login prompt.
  Other local users on a shared host can reach the 127.0.0.1 port but
  cannot authenticate without the credential.
- walks first-time users through llm.neurodesk.org API key setup in the
  browser: the pasted key is validated against the LiteLLM `/models`
  endpoint and persisted to `~/.bashrc` in the exact format the terminal
  wrapper writes and `nbi_setup.sh` reads, so the terminal agents and
  Notebook Intelligence pick it up too. A "continue without a key" path
  falls back to the other providers.
- starts `opencode web` through the `/usr/local/sbin/opencode` wrapper
  (non-interactive path), so provider probing, `opencode.json` refresh, and
  the Notebook Intelligence sync stay single-sourced. Web launches default
  `OPENCODE_MODEL_PROFILE` to the Neurodesk provider independently of a model
  selected in terminal OpenCode; an explicit environment override still wins.
  The `neurodesk` profile prefers llm.neurodesk.org's curated `neurodesk`
  alias model and falls back to the provider's first listed model.
- sets the Web backend's `BASH_ENV` to
  [`opencode_bash_env.sh`](../../config/agents/opencode_bash_env.sh). OpenCode
  runs tool commands in non-interactive Bash shells, which do not read
  `~/.bashrc`; in lazy CVMFS mode the parent Jupyter process can also retain
  the local-only `MODULEPATH` it inherited before CVMFS mounted. The hook
  re-sources the current Neurodesktop environment and initializes Lmod for
  every Bash tool command, so `module load <tool>/<version>` works without
  per-command setup. The terminal OpenCode workflow is unaffected.
- runs the long-lived web backend from the stable `~/opencode-work` parent, then
  creates a unique `~/opencode-work/YYYYMMDD_HHMMSS/` project for every
  `POST /session`. The session directory is created before forwarding the
  request, initialized as its own Git worktree, seeded with an editable copy of
  `/opt/AGENTS.md`, and given a unique initial commit; a numeric suffix prevents
  collisions between concurrent or same-second session creations. A separate
  Git root on every dated child is required because OpenCode resolves the
  request directory with `git rev-parse --show-toplevel`. The root commit is
  also required because OpenCode uses it as the durable project identity when
  no remote exists; an empty repository falls back to the shared `global`
  identity, which collapses multiple workspaces in the Home session index.
  Without the nested root, the parent worktree silently pulls every session
  into the shared parent and their artifacts mix.
  The local `AGENTS.md` remains the **only** source of Neurodesk guidance: the
  shipped `opencode.json` does not pin the read-only `/opt/AGENTS.md` into
  `instructions`, and the wrapper strips that legacy entry from configs written
  by earlier releases (user-added instructions survive).
- records the session id returned by each successful creation response and pins
  every later request for that id to its dated project. After a launcher restart,
  a dated directory supplied by the browser is accepted only when it is an
  existing direct Git-project child of `~/opencode-work`, then remembered for
  that session; arbitrary paths fall back to the managed backend root. Both the
  `?directory=` query parameter **and** `x-opencode-directory` header are
  enforced: OpenCode's client mirrors the directory into the query string only
  for GET and HEAD requests, so `POST /session` and
  `POST /session/:id/message` carry it in the header alone. The server resolves
  `?directory=` → `x-opencode-directory` → process cwd. The header is rewritten
  percent-encoded, matching OpenCode's client, and `/api/` routes use the
  `location[directory]` query key, which is pinned the same way.
- launches the web backend with OpenCode's ripgrep file search enabled instead
  of its native FFF indexer. OpenCode 1.18.x cannot initialize FFF when the
  workspace is the user's home directory and otherwise installs an empty
  search service, leaving the Add Project directory list blank.
- keeps OpenCode's native model picker available in the prompt toolbar. The
  automatically selected working model is only the initial default; users can
  choose any model currently advertised by Neurodesk, local Ollama, or
  JetStream and can change it again per prompt.
- reverse-proxies to the backend with HTTP Basic auth injected
  (`OPENCODE_SERVER_PASSWORD`) and streams SSE responses. For prefixed
  Jupyter/JupyterHub launches it inserts a same-origin bootstrap before the
  OpenCode module bundle; the bootstrap sets OpenCode's native default-server
  URL to the complete `X-Forwarded-Prefix`. Before the bundle hydrates, it also
  migrates same-origin server references in OpenCode's server, Home, layout,
  draft-tab, and closed-tab browser state to that prefixed URL. Both the
  current namespaced stores and the legacy `server.v3`, `home.servers.v1`, and
  `layout.v6` stores are handled before OpenCode can hydrate the latter into
  current state. This preserves drafts and sessions across upgrades without
  leaving a second server that sends `/api/*` requests to Jupyter's root;
  unrelated external servers and user-authored history are untouched. The
  pinned 1.18.7 bundle also needs its protocol-probe and v2 SDK URL
  constructors rewritten: their `new URL("/api/...", serverUrl)` form discards
  a path such as `/opencode` from `serverUrl`, misclassifies the backend after
  probing Jupyter's root, and then retries root `/api/event`. Neurodesktop
  makes those SDK paths relative to the configured server base, preserving
  both Jupyter prefixes and the behavior of ordinary root-hosted servers. The
  proxy also rewrites the pinned web
  bundle's canonical local-server URL to that bootstrap value, so the selected
  default and OpenCode's server registry use the same key; its permission
  provider rejects a selected server that is absent from that registry. The
  pinned bundle rewrite also marks its fetch-based SSE requests with `Accept:
  text/event-stream`, which makes Jupyter Server Proxy select progressive
  delivery, while the Python wrapper re-chunks upstream event feeds so Jupyter
  can flush each event instead of buffering indefinitely. The same bootstrap
  value is supplied as the Solid router's base path. Without that routing
  invariant, the SPA treats the first proxy segment
  (`opencode`) as a base64-encoded project directory and creates sessions in
  an invalid path. The router rewrite matches and preserves the bundle's
  minified component identifier because that identifier can change between
  otherwise compatible OpenCode patch releases. Together these changes keep
  provider, model, session,
  event, terminal, browser-history, and future API routes below `/opencode/`.
  The proxied bundle also makes the new-layout Home control perform a full
  navigation to the prefixed root. OpenCode's in-memory tab toggle works at a
  site root but does not reliably leave a server-scoped session when the app is
  mounted below Jupyter's `/opencode` prefix.
  Static root-absolute asset URLs and relative lazy-loaded chunk URLs in
  HTML/CSS/JS are rewritten against the same validated prefix. The relative
  chunk rewrite matters on the Home route because `/opencode` has no trailing
  slash, so an unmodified `assets/*` chunk would otherwise resolve to Jupyter's
  root `/assets/*`. Generated SDK routes such as `/api/session` remain unchanged
  because the SDK resolves them against the already-prefixed server URL;
  rewriting those literals would apply the proxy prefix twice.
  This is necessary because the upstream UI otherwise uses the site origin and
  escapes the Jupyter proxy.
- previews the files an agent produced. OpenCode's changed-files list renders
  every non-text file as an unreadable binary diff, which hides exactly the
  outputs neuroimaging work produces: QC screenshots and NIfTI volumes. A
  second injected script (`/neurodesk-preview.js`) opens an overlay viewer
  when a previewable file name is clicked — `<img>` for
  png/jpg/gif/webp/bmp/svg, NiiVue for nii/nii.gz/mgz/mgh/mif/nrrd/mha/mhd.
  Bytes come from the proxy's own `/neurodesk-file/<path>` route, which sits
  behind the same credential as the UI and resolves the path *inside* one
  validated `~/opencode-work/YYYYMMDD_HHMMSS/` project. Resolution fails
  closed: a request naming a session must resolve to that session (a stale
  or unknown id is a 404, never a widened search), and a request naming none
  is answered only from the directory OpenCode's own client reports or when
  exactly one session exists. The shared `~/opencode-work` parent is never
  searched, so a uniquely named artifact cannot leak between sessions.
  Absolute paths, `..` segments, and symlinks leaving the project are
  refused, only the previewable extensions above are served, an ambiguous
  name match is refused rather than guessed, and files above
  `OPENCODE_WEB_PREVIEW_MAX_BYTES` are rejected. Compressed volumes are sent
  as `application/gzip` with no `Content-Encoding`, because NiiVue inflates
  `.nii.gz` itself. The viewer is the `@niivue/niivue` `dist/index.js`
  ESM bundle vendored into `/opt/neurodesktop/vendor/niivue.js` at build
  time (`NIIVUE_VERSION`) and served from `/neurodesk-niivue.js`, so previews
  work offline and load no CDN; a missing bundle only costs volume previews.
  That bundle is cached `immutable` for a year, so the previewer requests it
  through a `?v=<content hash>` URL: a `NIIVUE_VERSION` bump changes the URL
  and browsers holding the old bundle fetch the new one. Closing a volume
  preview calls NiiVue's `cleanup()`. NiiVue attaches directly to the canvas,
  so removing the overlay does not trigger its own teardown, and without the
  explicit call every preview would retain listeners, observers, and a WebGL
  context — of which browsers grant only a handful.
  The previewer never inserts nodes into OpenCode's DOM — it listens for
  clicks in the capture phase and mounts its overlay under `<body>` — so an
  upstream markup change can cost the preview but never the UI. It recovers
  the file's path from the row's text and falls back to a unique-suffix
  search under the session project when the markup separates the directory
  from the base name.

Inside the VNC/RDP desktop there is no URL prefix, so the "OpenCode Web"
menu entry
([`config/agents/opencode-web.desktop`](../../config/agents/opencode-web.desktop))
runs [`config/agents/opencode_web_desktop.sh`](../../config/agents/opencode_web_desktop.sh),
which starts the same launcher on a per-user dynamic port (reusing it only
after verifying the recorded process is owned by the current user) and opens
Firefox with a single-use `?auth=` login token that is swapped for a cookie
and rotated on use. Session sharing is disabled by default in
[`config/agents/opencode_config.json`](../../config/agents/opencode_config.json)
(`"share": "disabled"`) so research conversations are not uploaded to the
OpenCode share service unless a user opts in.

The `/usr/local/sbin/opencode` wrapper also seeds
`~/.local/state/opencode/kv.json` with `"sidebar": "hide"` so the TUI's
right-hand session sidebar (context usage, LSP status) starts hidden and the
full width goes to the conversation. OpenCode persists the `ctrl+x b` toggle
under the same key, so the wrapper only writes it when absent and a user who
re-enables the sidebar keeps that choice.

## OpenCode session pruning

OpenCode keeps session history in `~/.local/share/opencode/opencode.db`, not in
the working directory, and never prunes it. Deleting a project directory
therefore leaves its sessions on the Home page forever, pointing at paths that
are gone — and opening one replays that dead directory back into the API,
because the SPA encodes the session's stored directory into its URL and into
the `x-opencode-directory` header.

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
