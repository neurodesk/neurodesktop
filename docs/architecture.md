# Architecture

## Container Initialization Flow

The startup sequence follows this order:

1. [`config/jupyter/start_notebook.sh`](../config/jupyter/start_notebook.sh)
   sets ownership permissions for the home directory.
2. [`config/jupyter/before_notebook.sh`](../config/jupyter/before_notebook.sh)
   mounts CVMFS, ranks the CVMFS servers by measured download throughput via
   [`config/jupyter/cvmfs_server_select.sh`](../config/jupyter/cvmfs_server_select.sh),
   and configures the environment.
3. `jupyter_notebook_config.py` is generated and defines JupyterLab server
   proxies for webapps. It also installs
   [`config/jupyter/jupyterlmod_modulepath.py`](../config/jupyter/jupyterlmod_modulepath.py)
   so the jupyter-lmod side panel refreshes the Jupyter server process
   `MODULEPATH` after lazy CVMFS startup.
4. [`config/jupyter/jupyterlab_startup.sh`](../config/jupyter/jupyterlab_startup.sh)
   starts JupyterLab and associated services.

## Core Components

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
triggers a forced re-probe.

Configuration lives in [`config/cvmfs/`](../config/cvmfs/). CVMFS can be
disabled with `CVMFS_DISABLE=true`.

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

### OpenCode Web Interface

The JupyterLab launcher exposes an "OpenCode AI" tile backed by a Jupyter
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
  the Notebook Intelligence sync stay single-sourced. A model chosen earlier
  is preserved by passing it back as `OPENCODE_MODEL_PROFILE`.
- launches the web backend with OpenCode's ripgrep file search enabled instead
  of its native FFF indexer. OpenCode 1.18.1 cannot initialize FFF when the
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
  URL to the complete `X-Forwarded-Prefix`. The proxy also rewrites the pinned
  web bundle's canonical local-server URL to that bootstrap value, so the
  selected default and OpenCode's server registry use the same key; its
  permission provider rejects a selected server that is absent from that
  registry. The same bootstrap value is supplied as the Solid router's base
  path. Without that third invariant, the SPA treats the first proxy segment
  (`opencode`) as a base64-encoded project directory and creates sessions in
  an invalid path. Together these changes keep provider, model, session,
  event, terminal, browser-history, and future API routes below `/opencode/`.
  Static root-absolute asset URLs in HTML/CSS/JS are rewritten against the
  same validated prefix.
  This is necessary because the upstream UI otherwise uses the site origin and
  escapes the Jupyter proxy.

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

## Directory Structure

- [`config/`](../config/): service configurations
- [`config/jupyter/`](../config/jupyter/): JupyterLab config, startup scripts,
  and webapp infrastructure
- [`config/guacamole/`](../config/guacamole/): remote desktop gateway config
- [`config/cvmfs/`](../config/cvmfs/): CVMFS mount configurations and keys
- [`config/lxde/`](../config/lxde/): desktop environment customization
- [`config/firefox/`](../config/firefox/), [`config/vscode/`](../config/vscode/),
  and [`config/itksnap/`](../config/itksnap/): application-specific configs
- [`scripts/`](../scripts/): build-time utilities
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
