---
title: Environment variables
description: Reference for runtime environment variables and Dockerfile build
  arguments supported by Neurodesktop
parent: index.md
status: current
last-reviewed: "2026-09-20"
---

# Environment Variables

Runtime variables are grouped by subsystem; [build arguments](#build-arguments)
are listed at the end. The subsystems themselves are described in
[Architecture](architecture.md).

## CVMFS and modules

- `LMOD_AVAIL_EXTENSIONS`: defaults to `no`, hiding the extension inventory
  from `ml av` and `module avail`. Set to `yes` to show it again. Module
  loading and `module spider` searches are unchanged.
- `CVMFS_DISABLE`: set to `true` to disable CVMFS mounting
- `CVMFS_MODULES`: CVMFS module catalogue path used when refreshing
  `MODULEPATH`. Fixed to `/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/` by
  `environment_variables.sh`; not a user override
- `NEURODESKTOP_CVMFS_SELECTION_TTL_SECONDS`: lifetime of the cached CVMFS
  server ranking produced by `cvmfs_server_select.sh`; defaults to `604800`
  (7 days). Set to `0` to re-probe on every startup
- `NEURODESKTOP_CVMFS_HOST_POOL`: whitespace-separated `http://host[:port]`
  list overriding the built-in pool of CVMFS servers that
  `cvmfs_server_select.sh` probes (mainly for testing)
- `NEURODESKTOP_CVMFS_TARGET_CONFIG`: file that `cvmfs_server_select.sh`
  writes the generated repository config to; defaults to
  `/etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf` (mainly for testing)
- `NEURODESKTOP_CVMFS_CACHE_FILE`: location of the CVMFS server selection
  cache; defaults to `~/.cache/neurodesktop/cvmfs-selection.env` (mainly for
  testing)
- `NEURODESKTOP_LOCAL_CONTAINERS`: local container root used to derive
  `OFFLINE_MODULES`; defaults to `/neurodesktop-storage/containers`
- `OFFLINE_MODULES`: local Lmod module path derived from
  `NEURODESKTOP_LOCAL_CONTAINERS`

## Startup privileges

- `GRANT_SUDO`: defaults to `packages` when the container starts as root.
  The notebook user can run `apt update` and `apt install PACKAGE...`, with or
  without `sudo`. The root-owned `/usr/local/bin/apt` and `apt-get` helpers accept
  repository package names, optional architecture qualifiers, `-y`, `--yes`,
  `--assume-yes`, and `--no-install-recommends`. Installs are noninteractive,
  retain existing configuration files, and reject package removals. Other apt
  commands, local `.deb` files, version or release selectors, and caller-supplied
  apt configuration are rejected. Read-only queries remain available through
  `/usr/bin/apt`, without sudo. `no` removes managed sudo grants. An explicit
  `yes` restores unrestricted passwordless sudo for deployments that require it.
  Root startup removes legacy grants from both Neurodesktop and the base image.

The package-only policy reduces accidental privileged operations. Installed
packages still execute maintainer scripts as root, so it is not a boundary
against a determined user or a compromised repository. Container capabilities,
mounts, and host isolation still determine the consequences of root access.
Unprivileged Apptainer startup cannot grant sudo rights or change host passwords.

See [desktop credentials and service access](architecture/desktop.md#credentials-and-service-access)
for RDP initialization and VS Code isolation.

## Apptainer

- `APPTAINER_HOME`: home directory passed to Apptainer. When this variable is
  unset or empty and `HOME` names an existing directory,
  `environment_variables.sh` defaults it to `HOME`; an explicit override is
  preserved. This avoids a failed password-database lookup on the mapped host
  uid in the rootless Podman setup reported in issue #804.
- `APPTAINER_NV`: enables Apptainer's NVIDIA device and host-library binds.
  `environment_variables.sh` defaults it to `1` when
  `/proc/driver/nvidia/version` shows that the proprietary driver is loaded.
  An explicit value is preserved. Set `APPTAINER_NV=0` for a command if a host
  NVIDIA library fails with a `GLIBC_* not found` error in an older tool
  container.

  NVIDIA binds are also required for OpenGL applications when the NVIDIA GPU
  drives the display. The X server selects `libGLX_nvidia.so.0`, but Neurodesk
  tool containers ship Mesa's GLX vendor library. Without the bind, programs
  such as FSLeyes, ITK-SNAP, MRView, and Slicer can fail to open even when they
  do not use CUDA.

## Startup

- `NB_UID`, `NB_GID`: user and group IDs for permission matching
- `NEURODESKTOP_CVMFS_STARTUP_MODE`, `NEURODESKTOP_SLURM_STARTUP_MODE`: set
  either service to `eager` for disposable runtime-acceptance containers that
  must wait for CVMFS/module and local scheduler readiness before testing
- `NEURODESKTOP_PRINT_ACCESS_URL`: set to `0` to disable the end-of-startup
  access-link banner that `print_access_url.sh` reprints once the Jupyter
  server answers HTTP (the ServerApp's own token banner scrolls away behind
  extension startup logs). The banner reads only numeric
  `jpserver-<pid>.json` files, excluding the MCP server's runtime file
- `NEURODESKTOP_ACCESS_URL_MAX_WAIT`: seconds `print_access_url.sh` waits for
  the Jupyter server to answer before giving up; defaults to `180`
- `NEURODESKTOP_ACCESS_URL_SETTLE`: seconds `print_access_url.sh` waits after
  the server answers so the banner lands after the startup log burst;
  defaults to `3`
- `NEURODESKTOP_VERSION`: version tag set by CI

## Slurm

- `NEURODESKTOP_SLURM_MODE`: selects the scheduler contract. `local` starts and
  uses the integrated single-node `neurodesktop` partition; `host` skips
  in-container Slurm startup and uses the host cluster. See
  [`config/slurm/README.md`](../config/slurm/README.md)
- Further Slurm tuning variables (`NEURODESKTOP_SLURM_ENABLE`,
  `NEURODESKTOP_SLURM_PARTITION`, `NEURODESKTOP_SLURM_MEMORY_RESERVE_MB`,
  the cgroup plugin/mountpoint overrides, and
  `NEURODESKTOP_MUNGE_NUM_THREADS`) are documented in
  [`config/slurm/README.md`](../config/slurm/README.md)

## Desktop (VNC/RDP, Guacamole, Firefox)

- `NEURODESKTOP_DESKTOP_BACKEND`: desktop backend started by `guacamole.sh`;
  supported values are `rdp`, `vnc`, and `both`. The Jupyter launcher sets this
  automatically for the separate RDP and VNC desktop entries
- `NEURODESKTOP_RDP_PORT`: root-startup xrdp listener port, default `3389`.
  It binds only loopback and must be between 1024 and 65535. Changing it requires
  a container restart; notebook sessions reuse the root-provisioned port
- `NEURODESKTOP_TOMCAT_PORT`, `NEURODESKTOP_GUACD_PORT`,
  `NEURODESKTOP_VNC_PORT`, `NEURODESKTOP_SFTP_PORT`:
  port overrides for the desktop stack; by default each is allocated
  automatically starting from its conventional port
- `NEURODESKTOP_RUNTIME_DIR`: base directory for per-backend Guacamole,
  Tomcat, and runtime state; defaults under `~/.neurodesk`
- `NEURODESKTOP_GUACAMOLE_USER`, `NEURODESKTOP_GUACAMOLE_PASSWORD`,
  `NEURODESKTOP_VNC_PASSWORD`: override the per-user desktop credentials
  generated by `init_secrets.sh` (mainly for testing)
- `NEURODESKTOP_REAL_FIREFOX`: real Firefox binary the
  `neurodesktop-firefox` wrapper launches; defaults to `/usr/bin/firefox`
- `NEURODESKTOP_FIREFOX_PROFILE_ROOT`: directory where the Neurodesktop Firefox
  wrapper stores display-specific profiles when an explicit profile root is
  needed. By default, the wrapper lets Firefox create and register profiles in
  its standard `~/.mozilla/firefox` profile store using names like
  `neurodesktop-display-1`
- `NEURODESKTOP_FIREFOX_PROFILE_DIR`: absolute Firefox profile directory override
  for the Neurodesktop Firefox wrapper; when unset, the wrapper derives a
  profile from `NEURODESKTOP_FIREFOX_PROFILE_ROOT` and the current `DISPLAY`

## Container-backed webapps

- `NEURODESK_WEBAPP_IDLE_TIMEOUT`: seconds without traffic before the webapp
  wrapper stops an idle backend; defaults to `90`
- `NEURODESK_WEBAPP_IDLE_CHECK_INTERVAL`, `NEURODESK_WEBAPP_HEARTBEAT_INTERVAL`,
  `NEURODESK_WEBAPP_STOP_TIMEOUT`: idle-check cadence (`5`), client heartbeat
  interval (`60`), and backend stop grace period (`10`) for the same wrapper
- `NEURODESK_WEBAPP_PORT`: fixed port override for a wrapped webapp backend
  (mainly for testing; by default a Unix socket is used)

## AI tooling (providers, agents, Notebook Intelligence)

### T3 Code server

T3 starts automatically with Jupyter as the notebook user.

- `NEURODESKTOP_T3_CODE_HOST`: interface passed to T3; defaults to
  `127.0.0.1`. Use `0.0.0.0` inside Docker when publishing container port
  `3773`; the launcher maps it to host loopback port `3774`
- `NEURODESKTOP_T3_CODE_PORT`: fixed server port; defaults to `3773`
- `NEURODESKTOP_T3_CODE_HOME`: persistent T3 data directory; defaults to
  `~/.t3`
- `NEURODESKTOP_T3_CODE_WORKDIR`: initial project directory; defaults to the
  notebook user's home
- `NEURODESKTOP_T3_CODE_EXECUTABLE`,
  `NEURODESKTOP_T3_CODE_PROVIDER_BIN`: installed server and quiet provider
  directory overrides. These are intended for image tests and development.
  The defaults are `/opt/t3-code/node_modules/.bin/t3` and
  `/opt/neurodesktop/t3-provider-bin`.

See [T3 Code remote access](architecture/t3-code.md) for the Docker and T3
Connect procedures.

- `NEURODESK_API_KEY`: API key for `https://llm.neurodesk.org`. Shared by
  OpenCode and by the Notebook Intelligence JupyterLab plugin. OpenCode
  persists it to `~/.bashrc` on first setup, and `nbi_setup.sh` injects it
  into `~/.jupyter/nbi/config.json` on each JupyterLab startup and after
  each OpenCode run. `nbi_setup.sh` also mirrors the model selected in
  OpenCode (the top-level `model` in `~/.config/opencode/opencode.json`)
  into Notebook Intelligence, so picking a model in the OpenCode startup
  menu updates both tools; Notebook Intelligence sections pointed at a
  custom endpoint via its Settings UI are left alone. After writing the
  files, `nbi_setup.sh` asks every running Jupyter server (discovered via
  `jpserver-*.json` under the Jupyter runtime directory) to re-read the
  config so the change applies without a JupyterLab restart. An NBI
  Settings tab that was already open in the browser still shows the old
  values until the page is reloaded, and saving from such a stale tab
  writes the old values back. Automatic key injection requires HTTPS, exactly
  `llm.neurodesk.org`, and the default HTTPS port. URLs with user-info or
  fragments are rejected. Changing the model endpoint does not carry the old
  provider's key to the new endpoint
- `NEURODESK_BASE_URL`, `JETSTREAM_BASE_URL`: provider endpoints probed by the
  OpenCode wrapper; default to `https://llm.neurodesk.org/openai` and
  `https://llm.jetstream-cloud.org/v1`
- `BR_MCP_TOKEN`: Brain Researcher MCP token consumed by the Claude wrapper
  and mirrored into Notebook Intelligence by `nbi_setup.sh`
- `CODEX_PATH`, `CLAUDE_CODE_EXECUTABLE`: executable paths used by the Jupyter
  AI ACP adapters. They default to quiet selectors under `/opt/neurodesktop/`.
  Each selector prefers the user-managed executable in `~/.local/bin` and
  falls back to the version installed in the image. An explicit value replaces
  the selector
- `OLLAMA_HOST`: Ollama endpoint used by the AI tools; defaults to
  `http://host.docker.internal:11434` (an Ollama server on the Docker host —
  the image does not bundle Ollama itself). At container startup,
  `before_notebook.sh` probes the endpoint (1s connect timeout) and repoints
  the Jupyter server process at `http://127.0.0.1:11434` when it is
  unreachable, so a black-holed host cannot block server startup while
  Notebook Intelligence enumerates Ollama models
- `OLLAMA_DEFAULT_CONTEXT_LIMIT`, `OLLAMA_DEFAULT_OUTPUT_LIMIT`: context and
  output token limits the OpenCode wrapper assumes for Ollama models that do
  not declare an explicit `limit`; default to `32768` and `8192`
- `OPENCODE_MODEL_PROFILE`: set to `ollama`, `neurodesk`, `jetstream`, or
  `provider/model` to skip the interactive OpenCode model picker. The
  `neurodesk` profile prefers llm.neurodesk.org's curated `neurodesk` alias
  model when it is available and otherwise uses the first listed model
- `OPENCODE_STARTUP_VERBOSE`: set to `1` to show detailed OpenCode provider
  probe output during startup
- `INITIAL_AGENT_MODE`: initial approval/sandbox mode of the Jupyter AI Codex
  ACP persona (`read-only`, `agent`, or `agent-full-access`); defaults to
  `agent-full-access` in `environment_variables.sh` so chats start as
  "Agent (full access)", matching the image's no-approval Codex defaults.
  Only the codex-acp adapter reads it; an unrecognised value falls back to
  the adapter's own sandboxed `agent` default, and each chat's mode selector
  can still switch modes per session
- `NBI_TOUR_CONFIG_PATH`: Notebook Intelligence tour override file; defaults to
  `/opt/jovyan_defaults/.jupyter/nbi/tour_config.json`, which disables the
  first-run tour in Neurodesktop

## OpenCode

- `NEURODESKTOP_OPENCODE_PRUNE_SESSIONS`: set to `0` (or `false`/`no`/`off`)
  to disable an explicit invocation of the manual recovery tool
  `/opt/neurodesktop/opencode_prune_sessions.py`. Container startup never runs
  the tool automatically. Without `--apply` it reports sessions whose working
  directory has been deleted; with `--apply` it drops them from
  `~/.local/share/opencode/opencode.db`, writes a single rolling
  `opencode.db.prune-backup`, and runs `VACUUM`. Sessions whose whole parent
  tree is missing are left alone, so an unmounted volume is not mistaken for a
  deleted directory. Because backup and vacuum work scales with database size,
  applying the cleanup is an explicit operator action

## Build arguments

Exact pins for the image build. The default values below are the validated
pins in the [`Dockerfile`](../Dockerfile) at the time this page was last
reviewed; the Dockerfile itself is authoritative.

- `OPENCODE_VERSION`: the OpenCode release installed into
  the image; defaults to the validated pin in the Dockerfile (currently
  `1.18.30`). Override to bump the pin, or set it to an empty value to
  install the latest release
- `CLAUDE_CODE_VERSION`: exact Claude Code native release installed as the
  image fallback; defaults to `2.1.278`. The direct-version audit compares the
  pin with the official `@anthropic-ai/claude-code` release stream
- `CVMFS_VERSION`: exact Ubuntu CVMFS client package version;
  defaults to `2.14.1+ubuntu24.04`
- `CVMFS_RELEASE_VERSION`, `CVMFS_RELEASE_SHA256`: expected
  version and SHA-256 digest of the CVMFS apt repository bootstrap package;
  defaults to release `4.9` and its validated digest
- `NBI_JUPYTERLAB_BUILDER_VERSION`: JupyterLab builder used
  to reconstruct Notebook Intelligence's JupyterLab 4.6-compatible frontend;
  defaults to
  `4.5.10`
- `UV_VERSION`, `ASTRA_TOOLS_VERSION`, `ASTRA_SPEC_VERSION`,
  `ANYWIDGET_VERSION`, `LIGHTCONE_CLI_VERSION`, `LIGHTCONE_CLI_SHA256`: exact `uv`,
  ASTRA CLI/schema, viewer runtime, and isolated Lightcone CLI releases
  installed in the image; defaults to `0.12.12`, `0.2.17`, `0.0.14`, `0.11.0`,
  `0.4.2`, and the verified SHA-256 of that Lightcone source archive
- `SNAKEMAKE_VERSION`: user-facing Snakemake workflow release; defaults to
  `9.26.1` in the main and isolated Lightcone environments. Its current
  metadata requires `packaging<26`, so the image holds that infrastructure
  library at the newest compatible release, `25.0`
- `AGENT_SKILLS_REF`: exact commit of
  `LightconeResearch/agent-skills` used for the Codex and Claude reproduction
  plugin and OpenCode's copied skills and hook adapter;
  defaults to `4ded682be8487d8aa05831678ef84ef12068d50d`, the last reviewed
  commit containing the stable reproduction plugin; the build updates that
  plugin's ASTRA pins with exact anchors
- `JUPYTER_AI_VERSION`: Jupyter AI metapackage release;
  defaults to `3.2.0`, with its direct pre-1.0 extensions and Jupyter
  Collaboration pinned alongside it
- `CODEX_ACP_VERSION`, `CLAUDE_AGENT_ACP_VERSION`: pinned
  ACP adapters that expose the Codex and Claude personas in Jupyter AI;
  defaults to `1.11.0` and `0.76.0`. They install without their vendored agent
  binaries and drive the selected user or image CLIs through `CODEX_PATH` and
  `CLAUDE_CODE_EXECUTABLE` (runtime variables exported by
  `environment_variables.sh`)
- `CODEX_CLI_VERSION`: the `@openai/codex` CLI release
  installed globally; defaults to `0.155.1`. Upgrades must pass the real T3 and
  ACP initialization probes. This release exceeds the pinned ACP adapter's
  declared dependency range, so that combination needs explicit validation.
  A user can install a newer release with `codex update`
- `T3_CLOUDFLARED_VERSION`: the relay client release bundled for guided T3 Connect
  linking; defaults to `2026.9.1` and uses T3's supported executable override. Update
  both architecture SHA-256 values in `Dockerfile` whenever this version changes.
- `T3_CODE_VERSION`: the headless T3 Code server release installed from the
  checked manifest; defaults to `0.0.42`. The release ships one self-contained
  executable per platform, so the layer installs no lockfile and compiles
  nothing
- `TAILSCALE_VERSION`: static Tailscale CLI and daemon release; defaults to
  `1.102.4`. Update `TAILSCALE_AMD64_SHA256` and `TAILSCALE_ARM64_SHA256`
  together with the version, using the official archive checksums. Installation
  does not start or authenticate the daemon; see
  [manual userspace setup](architecture/t3-code.md#connect-through-tailscale-inside-the-container).
- `JUPYTER_COLLABORATION_VERSION`, `JUPYTER_COLLABORATION_REF`: release and
  exact source commit used to rebuild Jupyter AI's collaboration frontends for
  JupyterLab 4.6's YDoc 4 contract; defaults to `4.4.2` and
  `3bf11cb7b271b554998105a11e6c9b8c3e376615`
- `MYST_PNPM_VERSION`, `MYST_YDOC_VERSION`: pnpm and Jupyter
  YDoc releases used for the MyST/RISE compatibility rebuild; defaults to
  `11.26.0` and `4.1.1`
- `APPTAINER_VERSION`, `APPTAINER_GO_VERSION`, `APPTAINER_GRPC_VERSION`,
  `APPTAINER_CRYPTO_VERSION`: Apptainer source release and the Go
  toolchain/grpc/crypto module versions used in its dedicated build stage;
  defaults to `1.5.3`, `1.27.1`, `1.83.2`, and `0.57.0`. The crypto override
  also updates the separately vendored gocryptfs build.
- `BASE_IMAGE_TAG`: tag of the upstream Jupyter Docker base image; defaults to
  the multi-architecture `2026-09-07` release
- `NPM_VERSION`: npm release installed with the runtime Node.js distribution;
  defaults to `12.0.2`
- `JUPYTER_BUILDER_VERSION`, `JUPYTERLAB_SLURM_REF`: current Jupyter Builder
  release and exact `jupyterlab-slurm` source revision used to build its
  JupyterLab 4 extension; defaults to `1.2.3` and
  `c34354f0aaa1b12f6243224bed631cf07c858409`
- `GUACAMOLE_VERSION`, `TOMCAT_REL`, `TOMCAT_VERSION`,
  `TOMCAT_MIGRATION_VERSION`: Guacamole release (`1.6.0`) and the Tomcat
  major/exact/migration-tool versions serving it (`11`, `11.0.25`, `1.0.12`)
- `CODE_SERVER_VERSION`: code-server release; defaults to `4.136.2`
- `NEUROCOMMAND_REF`: neurocommand git ref cloned during the build; CI passes
  a resolved `main` SHA so neurocommand changes invalidate the install layer
- `NODE_TAR_VERSION`: patched `node-tar` version applied to every bundled
  Node.js `tar` copy; defaults to `7.5.22`
