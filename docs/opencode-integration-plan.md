# Plan: OpenCode web interface with one-click launch from the start page

Status: implemented (first iteration) — see
[architecture.md](architecture.md#opencode-web-interface) for the shipped
design. Two deviations from the plan below, both simplifications:

- The first-run key dialog is served by the launch wrapper itself
  (`config/agents/opencode_web.py`) instead of a JupyterLab extension
  dialog + server endpoints. It works identically in every deployment
  (Docker, HPC, JupyterHub) and needs no frontend build.
- The prefix problem is handled by that same wrapper's rewriting reverse
  proxy rather than a third-party UI (pk-opencode-webui et al.), keeping the
  official upstream web UI and zero extra runtimes. Contributing base-path
  support upstream remains the long-term fix. The rewrite rules are
  regex-based and validated in `tests/test_opencode_web.py`; they should be
  re-verified against the real bundle when bumping the pinned
  `OPENCODE_VERSION`.

## Goal

Give Neurodesktop users a first-class, browser-based OpenCode experience:

1. An **"OpenCode" tile on the JupyterLab start page** (the launcher) that opens
   a nice web interface for OpenCode in a new browser tab.
2. **Guided, one-time setup of the `llm.neurodesk.org` API key** in the browser
   (no terminal prompts), reusing the key handling that the terminal wrapper
   already has.
3. Take advantage of the **OpenCode "v2" features** (the redesigned interface
   rolled out through opencode 1.17/1.18): `opencode web`, the headless
   `opencode serve` API, tabbed sessions, the review/diff panel, session
   snapshots and revert, per-prompt model selection, and mobile/PWA support.

## Where we are today

- OpenCode is installed at image build time via `https://opencode.ai/install`
  and moved to `/usr/bin/opencode` ([Dockerfile](../Dockerfile), "Install
  OpenCode CLI" layer). The `OPENCODE_VERSION` build argument pins the
  installed release (default 1.18.1); overriding it bumps the pin, and an
  empty value installs the latest release.
- Users reach it only through a **terminal**: `/usr/local/sbin/opencode`
  ([config/agents/opencode](../config/agents/opencode)) is a ~1600-line bash
  wrapper that probes the three providers (Jetstream, local Ollama,
  llm.neurodesk.org), interactively prompts for `NEURODESK_API_KEY`, persists
  it to `~/.bashrc`, rewrites `~/.config/opencode/opencode.json`, optionally
  sets up the Brain Researcher MCP token, mirrors everything into Notebook
  Intelligence via [nbi_setup.sh](../config/agents/nbi_setup.sh), then execs
  the real binary as a TUI.
- The start page is the JupyterLab launcher: tiles come from
  `c.ServerProxy.servers` entries in
  [jupyter_notebook_config.py.template](../config/jupyter/jupyter_notebook_config.py.template)
  plus the [neurodesk-launcher](../extensions/neurodesk-launcher/src/index.ts)
  labextension, which renders them with icons and category ordering.
- There is a proven pattern for **authenticated proxied services**: the
  Guacamole entries generate per-user credentials and inject them with
  `request_headers_override: {'Authorization': 'Basic ...'}` so the browser
  never sees a login prompt.
- There is a proven pattern for **wrapping webapps** with splash pages, unix
  sockets, idle shutdown and path rewrites:
  [webapp_wrapper.py](../config/jupyter/webapp_wrapper/webapp_wrapper.py).

## What OpenCode 2 gives us

Relevant facts from the opencode docs/changelog (verified July 2026):

- **`opencode web`** starts a local server and serves the full v2 browser app:
  tabbed sessions (`mod+1..9`), the redesigned review panel with persistent
  file browsing and diffs, session snapshots and roll-back (including file
  changes), per-prompt model selection, and a mobile-friendly PWA layout.
- **`opencode serve`** is the headless backend: OpenAPI 3.1 spec at `/doc`,
  REST endpoints for sessions/messages/files/config, and an SSE event stream
  at `/event`. Multiple clients (web UI, TUI, IDE plugins) can drive one
  server.
- **Auth**: HTTP Basic via `OPENCODE_SERVER_PASSWORD` (username `opencode`,
  overridable with `OPENCODE_SERVER_USERNAME`). Unset means unauthenticated.
- Flags: `--port`, `--hostname` (default 127.0.0.1), `--cors`, `--mdns`.
- **Known limitation**: the official web UI assumes it is served from the root
  path `/`. Behind prefixing proxies (jupyter-server-proxy serves apps under
  `/opencode/`, JupyterHub under `/user/<name>/...`) fonts and JS-loaded
  assets break because they request hard-coded `/assets/...` paths. Community
  projects exist specifically to fix this (e.g. `prokube/pk-opencode-webui`, a
  prefix-aware SolidJS UI that proxies to an unmodified `opencode serve`).

## Plan

### Phase 1 - shared setup library + headless launch script

Refactor the reusable parts of [config/agents/opencode](../config/agents/opencode)
into a sourceable library, e.g. `config/agents/opencode_common.sh` installed to
`/opt/neurodesktop/`:

- key handling: `sanitize_neurodesk_api_key`, `load_neurodesk_api_key_from_bashrc`,
  `persist_neurodesk_api_key_to_bashrc`, `validate_neurodesk_api_key_candidate`
- provider probes: `check_jetstream_model_api`, `check_local_ollama_models`,
  `check_neurodesk_server`
- config writing: `update_opencode_config`, `ensure_opencode_config_file`,
  Brain Researcher MCP helpers
- NBI sync hook (`sync_notebook_intelligence_config`)

The terminal wrapper keeps its interactive UX; the shipped launcher
**`config/agents/opencode_web.py`** (installed to `/opt/neurodesktop/`, run as
`python3 /opt/neurodesktop/opencode_web.py --port {port}`) reuses the terminal
wrapper non-interactively (it already has non-tty fallbacks: first working
model wins, `OPENCODE_MODEL_PROFILE` honored) to start the server:

```sh
opencode web --hostname 127.0.0.1 --port "${PORT}"    # serves UI + API
```

Security setup in the same script:

- Generate a persistent per-user password into
  `~/.neurodesk/secrets/opencode_server_password` (mode 0600, same location and
  lifecycle as the Guacamole web credentials) and export it as
  `OPENCODE_SERVER_PASSWORD`. Always set it: on shared HPC nodes other users
  can reach 127.0.0.1 ports (though they cannot authenticate without it).
- Never write the password into `opencode.json`.

### Phase 2 - launcher tile via jupyter-server-proxy

Add an `opencode` entry to `c.ServerProxy.servers` in
[jupyter_notebook_config.py.template](../config/jupyter/jupyter_notebook_config.py.template):

```python
'opencode': {
  'command': ['python3', '/opt/neurodesktop/opencode_web.py',
              '--port', '{port}'],
  'timeout': 60,
  'new_browser_tab': True,
  'request_headers_override': {'Authorization': f'Basic {_opencode_basic}'},
  'launcher_entry': {
    # Fail closed: the tile only shows when the credential exists.
    'enabled': bool(_opencode_pass),
    'path_info': 'opencode',
    'title': 'OpenCode AI',
    'icon_path': '/opt/neurodesk/icons/opencode.svg',
    'category': 'Neurodesk',
  },
}
```

`_opencode_basic` reads the secrets file the same way the template already
reads the Guacamole credentials, so the proxy injects auth and the user never
sees a Basic-auth prompt. The neurodesk-launcher extension picks the tile up
automatically from `/server-proxy/servers-info` - no frontend change needed for
the tile itself. Add an `opencode.svg` icon under
[config/jupyter/webapp_icons/](../config/jupyter/webapp_icons/).

### Phase 3 - solve the path-prefix problem (the actual "nice web interface")

The official UI breaking under `/opencode/` is the one real technical risk.

**Shipped:** `opencode_web.py`'s reverse proxy rewrites static root-absolute
URLs in HTML, CSS, and JavaScript responses against the validated
`X-Forwarded-Prefix`. Before the upstream module bundle runs, a generated
same-origin bootstrap also sets OpenCode's native default-server URL to that
full prefix. This is what keeps non-`/api` routes such as `/provider`,
`/global/config`, `/session`, and `/event` inside the Jupyter proxy and makes
the native per-prompt model picker work. The proxy also supplies the prefix as
the official SPA's Solid router base; otherwise `/opencode/` is parsed as the
base64 directory route and prompts fail before model inference. The behavior
and the pinned real OpenCode bundle contract are validated in
`tests/test_opencode_web.py` and must be re-verified when bumping
`OPENCODE_VERSION`.

The web child also runs with `OPENCODE_DISABLE_FFF=1`: OpenCode 1.18.1's FFF
indexer rejects a home-directory workspace and otherwise leaves Add Project
with an empty directory search service. OpenCode's ripgrep backend supports
the home-rooted project picker used by Neurodesktop.

Alternatives that were considered, kept here for context:

1. **Contribute base-path support upstream** (parallel track, best
   long-term). opencode is open source and takes PRs; a `--base-path` flag or
   relative asset URLs in the web bundle would let us drop the rewriting
   layer and serve the official app directly.
2. **Ship a prefix-aware third-party UI in front of `opencode serve`**, e.g.
   `prokube/pk-opencode-webui` (purpose-built for Kubeflow/JupyterHub-style
   prefixes), `openchamber`, or `kcrommett/oc-web`. Rejected for now: extra
   runtime and a fast-moving server API to track.

**Zero-prefix escape hatch that can ship immediately:** inside the Neurodesktop
VNC/RDP desktop there is no prefix - Firefox can open
`http://127.0.0.1:<port>/` at root. Add an "OpenCode Web" desktop entry
(pattern: [config/checkversion/CheckVersion.desktop](../config/checkversion/CheckVersion.desktop))
that runs the Phase-1 script and opens the official web UI in
`neurodesktop-firefox`. Local Docker users can alternatively publish the port
(`-p 4096:4096`) - document both in the user docs.

### Phase 4 - browser-based llm.neurodesk.org key setup

Move the first-run key setup out of the terminal and into JupyterLab:

- Add a small **Jupyter server extension** (natural home: the existing
  `neurodesk_launcher` Python package in
  [extensions/neurodesk-launcher/](../extensions/neurodesk-launcher/), which is
  currently frontend-only) with two endpoints:
  - `GET /neurodesk-ai/key-status` -> `{configured, valid, models[]}` - checks
    `~/.bashrc`/env for `NEURODESK_API_KEY` and validates it against
    `https://llm.neurodesk.org/openai/models` (server-side, so no CORS and the
    key never round-trips through page JS beyond the initial POST).
  - `POST /neurodesk-ai/key` `{key}` -> validates the candidate key, persists
    it (same `~/.bashrc` block the terminal wrapper writes), refreshes
    `opencode.json`, and runs `nbi_setup.sh` so Notebook Intelligence follows.
    Implementation shells out to the Phase-1 shared library (or a thin Python
    port with the shell behavior as the reference tests).
- Extend the **neurodesk-launcher frontend**: intercept the OpenCode tile's
  command; if `key-status` says unconfigured, show a JupyterLab dialog with
  the exact instructions the terminal wrapper prints today ("open
  https://llm.neurodesk.org -> avatar -> Settings -> Account -> API Keys ->
  Create new secret key"), a password-type input, inline validation feedback,
  and a "continue without key" path (Jetstream/Ollama fallback, mirroring the
  wrapper's behavior). On success, `window.open` the proxied web UI.
- Optionally include a second, collapsible field for the Brain Researcher MCP
  token (`BR_MCP_TOKEN`) so both secrets are handled in one dialog; a decline
  records the same `# BR_MCP_DECLINED` marker the wrapper uses.

The terminal flow keeps working unchanged; both flows write the same state
(`~/.bashrc`, `opencode.json`, NBI config), so they cannot drift.

### Phase 5 - exploit OpenCode 2 features

- **One backend, many clients.** Because `opencode web`/`serve` expose the
  same API, investigate pointing the terminal TUI at the already-running
  session server so web tab and terminal share sessions and history
  (verify current CLI support for attaching to an existing server; the
  changelog's IDE plugins do exactly this via `/tui`).
- **Config-level features** in
  [config/agents/opencode_config.json](../config/agents/opencode_config.json):
  - `"share": "disabled"` - opencode's session-sharing uploads conversation
    data to opencode's share service; that is the wrong default for a research
    environment. Make it opt-in.
  - **Agents**: ship Neurodesk agent presets, e.g. a `neuroimaging` build agent
    whose prompt embeds `/opt/AGENTS.md` guidance (module system, containers,
    lmod), and a read-only `explain` agent (tools restricted to read/grep) for
    teaching contexts.
  - **Custom commands**: `/neurodesk-modules` (list available lmod tools),
    `/neurodesk-example <tool>` etc., defined in the `command` section.
  - **Permissions**: for the web UI default, set `permission.bash` to `ask`
    for destructive patterns so a browser user gets explicit confirmation.
  - The existing Brain Researcher **MCP** wiring carries over automatically -
    same `opencode.json`.
- **Session snapshots / revert and the review panel** need no work from us but
  become the headline user-facing benefits - cover them in the user docs with
  screenshots.
- **SSE `/event` + OpenAPI** (future work, out of scope here): a JupyterLab
  side panel listing running OpenCode sessions, or programmatic pipeline use
  from notebooks via the generated SDK.
- **Pin the opencode version** in the Dockerfile (`OPENCODE_VERSION` build
  arg passed to the install script) instead of installing latest: the web UI,
  the prefix-workaround UI, and our config all need to be tested as a set,
  and unpinned installs make image builds non-reproducible.

### Phase 6 - tests and docs

Per [docs/testing.md](testing.md) and AGENTS.md expectations:

- `tests/test_opencode_web.py`: proxy entry present in the Jupyter config
  template with auth header override and `new_browser_tab`; the Python
  launcher is exercised end-to-end against fake opencode/LLM backends (key
  setup, auth, rewriting); the desktop shell script passes `bash -n`;
  password file created 0600; `opencode.json` remains valid after the
  non-interactive path runs (fixture-based, no network).
- Extend `tests/test_coding_agents.py` for the shared-library refactor (both
  wrappers source it; behavior parity for key sanitize/persist/load).
- Extend `tests/test_nbi_opencode_sync.py`: key set via the new endpoint is
  picked up by `nbi_setup.sh` the same as a terminal-set key.
- New endpoint tests for `key-status`/`key` handlers (mock the models call).
- Docs: new "AI coding agents" subsection in
  [architecture.md](architecture.md) describing the server/UI processes and
  auth; [environment-variables.md](environment-variables.md) additions
  (`OPENCODE_SERVER_PASSWORD` handling, any `OPENCODE_WEB_*` toggles, existing
  `OPENCODE_MODEL_PROFILE`/`OPENCODE_STARTUP_VERBOSE` cross-references); user
  docs for the launcher tile and first-run dialog.

## Suggested milestones

| # | Deliverable | Depends on | Size |
|---|-------------|-----------|------|
| 1 | Shared setup library + non-interactive `opencode_web` script | - | M |
| 2 | Desktop entry (zero-prefix official UI in VNC/RDP Firefox) | 1 | S |
| 3 | Launcher tile + proxy entry + per-user auth | 1 | S |
| 4 | Prefix-aware web UI evaluation + integration | 3 | M/L |
| 5 | Key-setup dialog (server endpoints + launcher dialog) | 1 | M |
| 6 | opencode.json v2 features (share off, agents, commands, permissions) + version pin | - | S |
| 7 | Tests + docs | 1-6 | M |

Milestones 2 and 6 are quick wins that can land independently while the
prefix-aware UI (4) - the only genuinely uncertain piece - is evaluated.

## Open questions to resolve during implementation

1. Minimum opencode version for `opencode web` and the v2 interface; exact
   pin to use (>= 1.18.x as of writing).
2. Can the TUI attach to an already-running `opencode serve` (shared sessions
   between web tab and terminal), and with which flag?
3. Which prefix-aware UI to adopt (pk-opencode-webui vs openchamber vs oc-web)
   - or whether upstream base-path support lands first and makes the question
   moot.
4. Does `opencode web` behave correctly with `Authorization` injected by the
   proxy for the SSE `/event` stream (long-lived connections through
   jupyter-server-proxy)?
5. JupyterHub deployments (play.neurodesk.org): confirm the double prefix
   (`/user/<name>/opencode/`) works with the chosen UI.
