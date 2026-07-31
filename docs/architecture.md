# Architecture

## Container Initialization Flow

The startup sequence follows this order:

1. [`config/jupyter/start_notebook.sh`](../config/jupyter/start_notebook.sh)
   sets ownership permissions for the home directory.
2. [`config/jupyter/before_notebook.sh`](../config/jupyter/before_notebook.sh)
   mounts CVMFS, ranks the CVMFS servers by measured download throughput via
   [`config/jupyter/cvmfs_server_select.sh`](../config/jupyter/cvmfs_server_select.sh),
   and configures the environment. It also launches
   [`config/jupyter/print_access_url.sh`](../config/jupyter/print_access_url.sh)
   in the background, which waits until the Jupyter server answers HTTP and
   then reprints the access URL (read from the server's `jpserver-<pid>.json`
   runtime info file) at the end of the startup log, where the ServerApp's own
   token banner has already scrolled out of view.
3. `jupyter_notebook_config.py` is generated and defines JupyterLab server
   proxies for webapps. It also installs
   [`config/jupyter/jupyterlmod_modulepath.py`](../config/jupyter/jupyterlmod_modulepath.py)
   so the jupyter-lmod side panel refreshes the Jupyter server process
   `MODULEPATH` after lazy CVMFS startup.
4. [`config/jupyter/jupyterlab_startup.sh`](../config/jupyter/jupyterlab_startup.sh)
   starts JupyterLab and associated services. It also runs
   [`opencode_prune_sessions.py`](../config/agents/opencode_prune_sessions.py)
   once per container start (see
   [OpenCode session pruning](#opencode-session-pruning)).

## Core Components

### Agentic issue investigation

The source workflow in
[`issue-investigator.md`](../.github/workflows/issue-investigator.md) investigates
new issues and may open a draft pull request labeled `agentic-workflow`. The
generated `issue-investigator.lock.yml` is the executable GitHub Actions
workflow and must be regenerated with `gh aw compile` whenever the Markdown
source changes.

CodeRabbit reviews those pull requests while they are still drafts. Its summary
comment updates trigger the companion
[`issue-investigator-review.md`](../.github/workflows/issue-investigator-review.md)
workflow. That workflow reads the complete current review, validates all active
findings against the latest PR head, batches valid fixes into one tested commit,
pushes it to the existing PR branch, and explicitly requests the next
incremental CodeRabbit review. The loop stops without changing or merging the PR
when no actionable findings remain; marking the draft ready and merging remain
human decisions.

Every Codex agentic workflow imports
[`agentic-models.md`](../.github/workflows/shared/agentic-models.md). Its
`neurodesk` model alias lists GLM 5.2 first and Kimi 2.7 second, giving the
workflow firewall an ordered secondary candidate when resolving the model from
the available-model catalog.

### Weekly agentic maintenance

Seven independently scattered weekly workflows inspect test redundancy, missing
coverage, available updates, duplicate abstractions, dead code, documentation
drift, and recurring test flakes. They share the bounded pull-request contract
in
[`maintenance-base.md`](../.github/workflows/shared/maintenance-base.md): each
category allows one open draft PR, one evidence-backed change per run, and no PR
when the candidate cannot be validated.

All maintenance PRs use the `[maintenance]` title prefix and
`agentic-workflow` label. CodeRabbit reviews them as drafts, then
[`maintenance-review.md`](../.github/workflows/maintenance-review.md) validates
and batches actionable feedback, pushes once to the existing branch, and asks
CodeRabbit for another incremental review. See
[`agentic-maintenance.md`](agentic-maintenance.md) for the workflow catalog,
guardrails, and operating contract.

### CVMFS

CVMFS, the CernVM File System, distributes neuroimaging software containers
without local storage. Server selection is handled by
[`config/jupyter/cvmfs_server_select.sh`](../config/jupyter/cvmfs_server_select.sh):
it probes a pool of direct Stratum-1 servers and Cloudflare-fronted CDN
endpoints in parallel for reachability, measures cold-cache download
throughput on the lowest-latency finalists, and writes `CVMFS_SERVER_URL` with
the fastest server first and the runners-up as fallbacks (plus a non-CDN host
if the top picks are all on the same CDN). Every probe carries a unique
cache-busting query string so CDN edge caches cannot inflate the measurement —
real workloads fetch long-tail objects that are cold at the edge. The CVMFS client walks the list in order and
abandons a degraded server at runtime via the failover settings
(`CVMFS_LOW_SPEED_LIMIT`, `CVMFS_TIMEOUT`, `CVMFS_MAX_RETRIES`,
`CVMFS_HOST_RESET_AFTER`) in
[`config/cvmfs/default.local`](../config/cvmfs/default.local). A successful
ranking is cached in `~/.cache/neurodesktop/cvmfs-selection.env` for seven days
and reused while its primary server passes a health check; a failed mount
triggers a forced re-probe. Eager Docker startup runs the selector as root, so
after writing this cache it restores ownership of the cache path to the
remapped notebook UID/GID; otherwise Jupyter cannot create its own sibling
cache directories.

Configuration lives in [`config/cvmfs/`](../config/cvmfs/). CVMFS can be
disabled with `CVMFS_DISABLE=true`. The Dockerfile pins both the CVMFS client
package and the repository bootstrap package; the bootstrap download is also
verified by SHA-256 so the `latest` URL cannot silently change a reproducible
build.

### Neurocommand

Neurocommand is cloned from
[`neurodesk/neurocommand`](https://github.com/neurodesk/neurocommand) during the
build. It provides the CLI and module system for neuroimaging tools, uses Lmod
for module management, and stores containers in
`/neurodesktop-storage/containers`.

### Webapp System

Container-backed webapps are defined in `webapps.json`, which is fetched from
the neurocommand repository. Hosted webapp links and local overrides are defined
in [`config/jupyter/webapp_links.json`](../config/jupyter/webapp_links.json) and
applied by [`scripts/generate_jupyter_config.py`](../scripts/generate_jupyter_config.py)
when generating Jupyter Server Proxy entries. The same merged webapp config is
written to `/opt/neurodesktop/webapps.json` so runtime wrapper settings such as
path rewrites use the local overrides too. The wrapper streams fixed-length
request bodies to the backend in bounded chunks, so large uploads are not
duplicated in wrapper memory; Jupyter Server and the hosting proxy still apply
their own request-size and multipart limits before the wrapper receives a
request. Container-backed webapps launch through
[`config/jupyter/webapp_launcher.sh`](../config/jupyter/webapp_launcher.sh) and
use Unix sockets such as `/tmp/neurodesk_webapp_{name}.sock` to avoid port
conflicts. Entries with `direct_url` open the hosted application directly from
the Neurodesk launcher. Launcher tile icons for those entries are checked-in
SVG or PNG files in
[`config/jupyter/webapp_icons/`](../config/jupyter/webapp_icons/) referenced from
`webapp_links.json` with `/opt/neurodesk/icons/*` paths; the Dockerfile copies
them into the image before Jupyter config generation. The custom Neurodesk
launcher reads icons through the server-proxy icon endpoint and wraps raster
images as SVGs for JupyterLab `LabIcon` support.

### Desktop Environment

The desktop environment uses LXDE with TigerVNC for VNC access and xrdp for RDP
access. Apache Guacamole provides browser-based remote desktop access. JupyterLab
exposes separate `Neurodesktop RDP` and `Neurodesktop VNC` launcher entries so
opening one backend does not start the other. In unprivileged Apptainer or
Singularity sessions, the RDP launcher entry is hidden because starting or
reconfiguring xrdp requires root/sudo permissions; the VNC launcher remains
available. Configuration lives in
[`config/lxde/`](../config/lxde/) and [`config/guacamole/`](../config/guacamole/).
The RDP and VNC proxy entries use backend-specific Guacamole state directories
under `~/.neurodesk` (`guacamole-*`, `tomcat-*`, and `runtime-*`) so one backend
does not reuse the other backend's cached connection mapping. Firefox launches
through `/usr/local/bin/neurodesktop-firefox`, which assigns a Firefox profile
for each X display and lets Firefox register that profile in its standard
profile store. If Firefox's profile-creation command does not write the profile
metadata, the wrapper creates the profile directory and `profiles.ini` entry
itself. Simultaneous VNC and RDP desktops therefore do not contend for the same
default Firefox profile.

Clipboard sync between the browser and the remote desktop uses Guacamole's
stock focus-driven `navigator.clipboard` integration in Chrome-family browsers.
Safari and Firefox restrict clipboard reads outside an explicit paste gesture
(Safari has no persistable clipboard-read permission at all), and no browser
makes Cmd+V paste into the remote session, so the Dockerfile injects
[`config/guacamole/mac-clipboard-shim.js`](../config/guacamole/mac-clipboard-shim.js)
into the Guacamole webapp's `index.html`. On macOS (any browser) the shim
intercepts Cmd+V, lets the browser's paste command land in a hidden textarea
and reads the text from the paste event's `clipboardData` (prompt-free in
every engine, unlike `navigator.clipboard.readText()`), streams it to the
remote clipboard through Guacamole's `clipboardService`, and synthesizes
Shift+Insert in the remote session (pastes in both terminals and GUI apps);
text copied in the remote session is cached and flushed to the local clipboard
on the next user gesture (Cmd+C or a mouse click). The shim is a no-op on
non-macOS platforms, and its `index.html` script tag carries a content-hash
query so browser caches cannot serve a stale shim after an image upgrade. Because Guacamole's RDP clipboard channel only
populates the X11 CLIPBOARD selection while VTE terminals paste PRIMARY on
Shift+Insert, xrdp sessions also start `autocutsel` (via
[`config/lxde/75neurodesk-clipboard-sync`](../config/lxde/75neurodesk-clipboard-sync)
in `/etc/X11/Xsession.d/`) to bridge the two selections; VNC sessions already
get this from TigerVNC's `vncconfig`.

Double-clicking a file in the desktop resolves its MIME type through the
default-user [`config/lxde/mimeapps.list`](../config/lxde/mimeapps.list).
Office documents (.odt, .docx, .xlsx, .pptx, ...) open in the Neurodesk
LibreOffice container apps: at image build time,
[`config/lxde/update_office_mimeapps.py`](../config/lxde/update_office_mimeapps.py)
reads the `MimeType=` declarations from the neurocommand-generated LibreOffice
`.desktop` entries, registers the newest version as the default handler for
each declared type, and removes xarchiver's claim on them (ODF/OOXML documents
are zip containers, so the archive manager would otherwise win). The build
fails if the neurocommand revision in the image does not declare MIME types in
its menu entries yet.

### Workspace link routing

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
site as raw HTML. The factory names are upstream strings, so the plugin falls back to the
default factory when the viewer is not registered; a clicked link then still
opens something rather than nothing.

### Services

- JupyterLab: main interface on port 8888
- code-server: VS Code in JupyterLab, with default extensions installed from
  [`config/jupyter/jupyterlab_startup.sh`](../config/jupyter/jupyterlab_startup.sh),
  including Python, Jupyter notebook, CSV table editing, NIfTI viewing, GitHub,
  Slurm, and assistant tooling
- Apache Tomcat: serves the Guacamole web application
- RDP and VNC: desktop access through Guacamole, started on demand by the
  selected launcher entry
- SSH: optional SSH server proxy
- Ollama: optional local LLM service when `START_LOCAL_LLMS=1`

### ASTRA and Lightcone command-line tools

`astra` comes from the single `astra-tools` install in the conda environment —
the same one the viewer imports — so the CLI and the schema the viewer
validates against can never drift apart. A second isolated copy is deliberately
not installed; the build asserts that exactly `/opt/conda/bin/astra` answers on
`PATH`.

`lc` (`lightcone-cli`) is installed as an isolated `uv` tool under
`/opt/uv/tools/lightcone-cli` and linked onto `PATH`, so its Dask and Snakemake
dependency graph cannot perturb JupyterLab. `uv` itself is on `PATH` for that
reason; ordinary `uv tool` operations stay user-local at runtime.

### ASTRA agent skill

A commit-pinned checkout of the Lightcone Research agent marketplace is stored
at `/opt/neurodesktop/agent-skills`. All three bundled coding agents get the
same ASTRA skill from it, without a first-run marketplace download:

| Agent | Mechanism | Hooks |
| --- | --- | --- |
| Codex | `codex plugin add astra@lightcone-research` | yes |
| Claude Code | `claude plugin install astra@lightcone-research` | yes |
| OpenCode | `SKILL.md` copied to `~/.config/opencode/skills/astra` | no |

OpenCode has no marketplace client, but it discovers Claude-format skills from
`~/.config/opencode/skills`, `~/.claude/skills`, and `~/.agents/skills`. The
skill is copied out of the same pinned checkout, so all three agents read
identical guidance from one source of truth. The plugin's hooks are a
Claude/Codex mechanism and are not copied — OpenCode gets the skill, not the
on-save validation hook.

Those hooks parse their payloads with `jq`, which the image installs for that
purpose. Without it every hook exits non-zero and silently contributes no
validation context, so `tests/container/test_astra_agent_skills_image.py`
drives the real hook scripts end to end rather than only checking that the
plugin is listed. That test also asserts that the pinned marketplace commit's
`astra-pins.sh` matches the installed `astra-tools` and `astra-spec`: the skill
must teach the schema version that `astra validate` actually speaks.

Only the `astra` plugin is enabled. The marketplace also ships `reproduction`
(`assess-reproducibility`, `reproduce`, `figure-comparison`), which is
deliberately left out because its workflows drive long autonomous replication
loops that should not be on by default in a shared scientific image. Users can
add it themselves from the same local marketplace with no network access.

### ASTRA provenance viewer

The image installs the in-repo `extensions/astra-viewer` wheel as
`neurodesk_astra_view`. Its public seam is deliberately small:

```python
from neurodesk_astra_view import AstraView, build_graph

AstraView(
    "astra.yaml",
    universe="universes/bet-f-0-5.yaml",
    run="run-manifest.json",        # optional
    mode="flow",                    # flow, decisions, or evidence
)
```

`adapter.py` is the only viewer module coupled to `astra-spec==0.0.12`. It runs
the public schema and semantic validators over raw YAML, resolves external
analysis and child-universe references recursively, and checks every resolved
real path against the ASTRA project root before it is opened. A spec that
declares a different ASTRA version renders with a warning banner while being
validated against the installed release — the same stance `astra validate`
takes — and the warning survives into the error view when that validation
fails; only an unsupported installed `astra-spec` package hard-fails. The rest
of the package consumes qualified, schema-independent entity records.
`build_graph()` is pure and JSON-serializable; the anywidget is only a renderer
over that result.

The frontend concatenates the checked-in Cytoscape.js 3.34.0 distribution with
the widget renderer at import time. It has no npm build, CDN import, fetch, or
other runtime network path. Flow, Decisions, and Evidence modes filter the same
Cytoscape instance, preserving positions and selection. Prior Insights,
findings, and their Evidence sources remain distinct nodes and only
schema-authoritative links are drawn.

Without a run, the graph is grey `spec-only`. Lightcone manifests, `lc status`
output, and Workflow Run RO-Crates are amber unless passing verification is
explicit. An explicit declared-container plus `runtime: none` mismatch is red
and non-dismissible. Every recorded artifact is rehashed before it is trusted,
so a stale hash or size fails the whole graph closed rather than rendering a
partial one. Previews stay under the directory containing `astra.yaml`.

`examples/astra-bet` is the shipped worked example, installed read-only at
`/opt/neurodesktop/examples/astra-bet`. It is specification-only, so it renders
grey; users copy it out as a starting point for their own analysis.

Double-clicking an `astra.yaml` (or `*.astra.yaml`) in the JupyterLab file
browser renders the same viewer without a kernel, the way NIfTI volumes open
in NiiVue. The `neurodesk_astra_view.serverext` Jupyter server extension
answers `GET /neurodesk-astra-view/graph?spec=…[&universe=…][&run=…]` by
running `build_graph()` server-side — request paths are workspace-relative and
rejected with a 404 before any read when absolute, traversing, or resolving
outside the server root — and serves the anywidget's own frontend at
`/neurodesk-astra-view/asset/(esm|css)`, so the file-browser viewer and the
notebook widget are one frontend with no second copy to drift. The
`neurodesk-launcher:astra-viewer` plugin registers the pattern file type and a
read-only default widget factory (`ASTRA Viewer`) over those endpoints, with a
universe picker fed by the spec's sibling `universes/` directory. Because the
factory is the pattern file type's default, agent-authored chat links to an
`astra.yaml` open in the viewer too, and a disabled plugin degrades to the
text editor. Editing stays on `Open With > Editor`; a save from that shared
context re-renders the graph.

### Jupyter AI

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

Jupyter AI creates a chat as a ``.chat`` file and passes the file's parent
directory to each ACP persona as its working directory. A Jupyter contents
post-save hook seeds that directory with an editable copy of ``/opt/AGENTS.md``
when the first chat is created there. It never overwrites an existing
``AGENTS.md`` and fails open so a missing seed or unwritable directory cannot
prevent the chat itself from being saved. This hook is necessary because the
ACP transports do not run the interactive Codex and OpenCode wrappers that
normally seed the file.

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

### Claude Code

Claude Code is installed into `/opt/jovyan_defaults/.local/bin/claude` when the
image is built and is launched through `/usr/local/sbin/claude`. On each launch,
the wrapper replaces `~/.local/bin/claude` with a symlink to that image-owned
binary. Persistent homes therefore pick up the Claude version in a newly
deployed image without retaining a stale per-user binary or duplicating the
large executable. Claude's in-process auto-updater remains disabled because
version updates are managed by the container image.

### OpenCode Web Interface

The JupyterLab launcher exposes a "Scigent.ai" tile backed by a Jupyter
Server Proxy entry that runs
[`config/agents/opencode_web.py`](../config/agents/opencode_web.py)
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
  [`opencode_bash_env.sh`](../config/agents/opencode_bash_env.sh). OpenCode
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
([`config/agents/opencode-web.desktop`](../config/agents/opencode-web.desktop))
runs [`config/agents/opencode_web_desktop.sh`](../config/agents/opencode_web_desktop.sh),
which starts the same launcher on a per-user dynamic port (reusing it only
after verifying the recorded process is owned by the current user) and opens
Firefox with a single-use `?auth=` login token that is swapped for a cookie
and rotated on use. Session sharing is disabled by default in
[`config/agents/opencode_config.json`](../config/agents/opencode_config.json)
(`"share": "disabled"`) so research conversations are not uploaded to the
OpenCode share service unless a user opts in.

The `/usr/local/sbin/opencode` wrapper also seeds
`~/.local/state/opencode/kv.json` with `"sidebar": "hide"` so the TUI's
right-hand session sidebar (context usage, LSP status) starts hidden and the
full width goes to the conversation. OpenCode persists the `ctrl+x b` toggle
under the same key, so the wrapper only writes it when absent and a user who
re-enables the sidebar keeps that choice.

### OpenCode session pruning

OpenCode keeps session history in `~/.local/share/opencode/opencode.db`, not in
the working directory, and never prunes it. Deleting a project directory
therefore leaves its sessions on the Home page forever, pointing at paths that
are gone — and opening one replays that dead directory back into the API,
because the SPA encodes the session's stored directory into its URL and into
the `x-opencode-directory` header.

[`config/agents/opencode_prune_sessions.py`](../config/agents/opencode_prune_sessions.py)
(installed as `/opt/neurodesktop/opencode_prune_sessions.py`) removes sessions
whose working directory no longer exists.
[`jupyterlab_startup.sh`](../config/jupyter/jupyterlab_startup.sh) runs it with
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

## Directory Structure

- [`config/`](../config/): service configurations
- [`config/jupyter/`](../config/jupyter/): JupyterLab config, startup scripts,
  and webapp infrastructure
- [`config/guacamole/`](../config/guacamole/): remote desktop gateway config
- [`config/cvmfs/`](../config/cvmfs/): CVMFS mount configurations and keys
- [`config/lxde/`](../config/lxde/): desktop environment customization
- [`config/firefox/`](../config/firefox/), [`config/vscode/`](../config/vscode/),
  and [`config/itksnap/`](../config/itksnap/): application-specific configs
- [`scripts/`](../scripts/): build-time utilities and installed runtime CLIs
- [`schemas/`](../schemas/): immutable machine-readable integration contracts
- [`.github/workflows/`](../.github/workflows/): CI/CD pipelines
- [`.github/workflows/build-neurodesktop.yml`](../.github/workflows/build-neurodesktop.yml):
  daily automated builds at 17:00 UTC
- [`.github/workflows/test-cvmfs.yml`](../.github/workflows/test-cvmfs.yml):
  CVMFS server health checks

CI includes multi-architecture builds for amd64 and arm64. Registry-sensitive
build paths use local composite actions under
[`.github/actions/`](../.github/actions/) so transient registry transport
failures are retried at login, manifest-check, and registry-copy boundaries
without turning registry timeouts into false cache misses.

## Build-Time Behaviors

### Config Generation

The Dockerfile clones neurocommand, copies its `neurodesk/webapps.json`, applies
[`config/jupyter/webapp_links.json`](../config/jupyter/webapp_links.json), and
generates `jupyter_notebook_config.py` using a template system. It also writes
the merged webapp configuration back to `/opt/neurodesktop/webapps.json`, which
is what the webapp wrapper reads at launch time. To add new container-backed
webapps, update the source `webapps.json`. To add hosted links or make an
existing launcher tile open a hosted app directly, update `webapp_links.json`.
This config generation runs after the neurocommand install layer so local
launcher-link edits do not invalidate the earlier runtime setup layers.
Cached CI builds pass `NEUROCOMMAND_REF` as a resolved neurocommand `main` SHA
so that neurocommand changes invalidate the install layer without requiring
BuildKit to make unauthenticated GitHub API requests from inside the Dockerfile.
The Dockerfile resets the local neurocommand `main` branch to that ref and keeps
it tracking `origin/main` so the runtime Update launcher can use
`git pull --rebase --autostash`.

### Notebook Intelligence Settings Patch

The upstream Notebook Intelligence settings panel auto-saves its client-side
state on open, using the capabilities cache fetched at page load. That
reverts any `~/.jupyter/nbi/config.json` change made behind the server's
back — in particular the OpenCode model selection mirrored by
`nbi_setup.sh`. Until this is fixed upstream, the Dockerfile pins
`notebook_intelligence` and runs
[`config/agents/patch_nbi.py`](../config/agents/patch_nbi.py) to rewrite the
bundled labextension so opening the settings panel first re-fetches
capabilities (the backend reloads the config file from disk to answer) and
rebuilds the panel from that fresh state. The patcher is anchored on the
exact minified code and fails the image build when a `notebook_intelligence`
upgrade changes the bundle, so the workaround cannot silently regress;
re-verify and update (or drop) the patch when bumping the pin.

Notebook Intelligence 5.3.0's published Python wheel omits its compiled
JupyterLab frontend. The Dockerfile therefore rebuilds the matching source tag,
replaces its older dependency graph with the checked-in, JupyterLab
4.6-compatible Yarn lockfile, installs that graph immutably, installs the
resulting federated extension, and only then applies the settings patch. The
build asserts that a `remoteEntry` bundle exists before continuing. Regenerate
`config/jupyter/notebook-intelligence-5.3.0.yarn.lock` when changing the NBI or
JupyterLab builder pins.

### MyST and RISE Extension Build

MyST is rebuilt against RISE's JupyterLab application so its markdown viewer is
available in presentation mode. MyST 2.7.0's published shared-package metadata
requests Jupyter YDoc 3.x, while the base image's JupyterLab 4.6 uses YDoc 4.x;
the source build pins that exact YDoc 4 release in both the package manifest and
the generated lockfile. RISE also retains
a Python dependency on the legacy `jupyterlab-mathjax3` package. Its JupyterLab
3-only frontend is not exposed in the final application; JupyterLab 4.6 and
RISE's standalone application both provide the current built-in MathJax
extension.

### Apptainer

The Dockerfile builds Apptainer from upstream source in a dedicated build stage
and copies `/opt/apptainer` into the runtime image. The build is controlled by
`APPTAINER_VERSION`, `APPTAINER_GO_VERSION`, and `APPTAINER_GRPC_VERSION` so the
image can move to scanner-fixed Go toolchain and module versions before a
matching upstream multi-arch runtime image is published.

macOS Docker/root sessions use `--overlay /tmp/apptainer_overlay` for writable
container sessions. This works around the "FATAL:   image targets 'amd64',
cannot run on 'arm64'" bug on macOS. Other non-Apptainer sessions leave
`neurodesk_singularity_opts` empty because it interferes with VS Code and
Matlab. Non-root Apptainer/HPC sessions use `--writable-tmpfs` because setuid
Apptainer cannot use a directory overlay as an unprivileged user.

### User Permissions

The container runs as the `jovyan` user from the base Jupyter image. The
`NB_UID` and `NB_GID` environment variables allow matching host user
permissions.

### CVMFS Setup

The active repository configuration is generated at startup by
`cvmfs_server_select.sh` (see the CVMFS section above). The image bakes in
[`config/cvmfs/neurodesk.ardc.edu.au.conf`](../config/cvmfs/neurodesk.ardc.edu.au.conf)
as a static default so mounts that happen before the selection ran still work;
CI jobs that configure CVMFS on the build host copy the same file.
