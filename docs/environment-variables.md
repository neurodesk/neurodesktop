# Environment Variables

- `CVMFS_DISABLE`: set to `true` to disable CVMFS mounting
- `CVMFS_MODULES`: CVMFS module catalogue path used when refreshing
  `MODULEPATH`; defaults to `/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/`
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
- `NB_UID`, `NB_GID`: user and group IDs for permission matching
- `START_LOCAL_LLMS`: set to `1` to enable Ollama with the Neurodesk model
- `OLLAMA_HOST`: Ollama endpoint used by the AI tools; defaults to
  `http://host.docker.internal:11434`. At container startup,
  `before_notebook.sh` probes the endpoint (1s connect timeout) and repoints
  the Jupyter server process at `http://127.0.0.1:11434` when it is
  unreachable, so a black-holed host cannot block server startup while
  Notebook Intelligence enumerates Ollama models
- `NEURODESKTOP_DESKTOP_BACKEND`: desktop backend started by `guacamole.sh`;
  supported values are `rdp`, `vnc`, and `both`. The Jupyter launcher sets this
  automatically for the separate RDP and VNC desktop entries
- `NEURODESKTOP_VERSION`: version tag set by CI
- `OPENCODE_MODEL_PROFILE`: set to `ollama`, `neurodesk`, `jetstream`, or
  `provider/model` to skip the interactive OpenCode model picker. The
  `neurodesk` profile prefers llm.neurodesk.org's curated `neurodesk` alias
  model when it is available and otherwise uses the first listed model
- `OPENCODE_STARTUP_VERBOSE`: set to `1` to show detailed OpenCode provider
  probe output during startup
- `OPENCODE_WEB_STARTUP_TIMEOUT`: seconds `opencode_web.py` (the "Scigent.ai"
  launcher tile) waits for the `opencode web` backend to become ready;
  defaults to `180`
- `OPENCODE_WEB_BASH_ENV`: non-interactive Bash initializer used by OpenCode
  Web tool commands; defaults to
  `/opt/neurodesktop/opencode_bash_env.sh`, which refreshes the lazy-CVMFS
  `MODULEPATH` and initializes Lmod (mainly overridable for testing)
- `OPENCODE_DISABLE_FFF`: forced to `1` for the OpenCode Web child process so
  its Add Project dialog can search below the `/home/jovyan` startup directory.
  The terminal OpenCode workflow is unaffected
- `OPENCODE_WEB_DESKTOP_STATE`: state file where the desktop "OpenCode Web"
  shortcut records its launcher's PID and dynamically allocated port;
  defaults to `~/.neurodesk/run/opencode_web_desktop.state`
- `NEURODESKTOP_OPENCODE_PRUNE_SESSIONS`: set to `0` (or `false`/`no`/`off`)
  to keep OpenCode sessions whose working directory has been deleted. By
  default `jupyterlab_startup.sh` runs
  `/opt/neurodesktop/opencode_prune_sessions.py --apply` once per container
  start, which drops those sessions from
  `~/.local/share/opencode/opencode.db` (OpenCode itself never prunes them, so
  they otherwise stay on its Home page pointing at paths that no longer
  exist). Sessions whose whole parent tree is missing are left alone, so a
  volume that is not mounted yet is never mistaken for a deleted directory.
  The previous database is kept as a single rolling
  `opencode.db.prune-backup`
- `OPENCODE_WEB_NIIVUE_BUNDLE`: NiiVue bundle the OpenCode Web file previewer
  loads for NIfTI/MGZ volumes; defaults to
  `/opt/neurodesktop/vendor/niivue.js` (vendored at build time by
  `NIIVUE_VERSION`). When the file is missing, image previews still work and
  volume previews report that the viewer is unavailable
- `OPENCODE_WEB_PREVIEW_MAX_BYTES`: largest file the OpenCode Web preview
  endpoint will stream to the browser; defaults to `536870912` (512 MiB)
- `OPENCODE_WEB_WRAPPER_BIN`, `OPENCODE_WEB_SECRET_FILE`,
  `OPENCODE_WEB_LOGIN_TOKEN_FILE`, `OPENCODE_WEB_AGENTS_FILE`,
  `NEURODESK_LLM_BASE_URL`: test overrides for `opencode_web.py` (backend
  command, credential file, single-use login token file, per-session
  `AGENTS.md` seed, and key-validation endpoint)
- `OPENCODE_VERSION` (build argument): the OpenCode release installed into
  the image; defaults to the validated pin in the Dockerfile (currently
  `1.18.7`). Override to bump the pin, or set it to an empty value to
  install the latest release
- `CVMFS_VERSION` (build argument): exact Ubuntu CVMFS client package version;
  defaults to `2.13.3+ubuntu24.04`
- `CVMFS_RELEASE_VERSION`, `CVMFS_RELEASE_SHA256` (build arguments): expected
  version and SHA-256 digest of the CVMFS apt repository bootstrap package;
  defaults to release `4.9` and its validated digest
- `NBI_JUPYTERLAB_BUILDER_VERSION` (build argument): JupyterLab builder used
  to reconstruct Notebook Intelligence's missing frontend; defaults to
  `4.5.10`
- `UV_VERSION`, `ASTRA_TOOLS_VERSION`, `ASTRA_SPEC_VERSION`,
  `LIGHTCONE_CLI_VERSION` (build arguments): exact `uv`, ASTRA CLI/schema,
  and isolated Lightcone CLI releases installed in the image; defaults to
  `0.11.8`, `0.2.11`, `0.0.12`, and `0.4.0`
- `AGENT_SKILLS_REF` (build argument): exact commit of
  `LightconeResearch/agent-skills` used for the Codex and Claude ASTRA plugin;
  defaults to `4ded682be8487d8aa05831678ef84ef12068d50d`
- `MYST_PNPM_VERSION`, `MYST_YDOC_VERSION` (build arguments): pnpm and Jupyter
  YDoc releases used for the MyST/RISE compatibility rebuild; defaults to
  `11.17.0` and `4.1.1`
- `NIIVUE_VERSION` (build argument): the `@niivue/niivue` release vendored to
  `/opt/neurodesktop/vendor/niivue.js` for the OpenCode Web volume
  previews; defaults to the pin in the Dockerfile (currently `0.69.0`)
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
  writes the old values back
- `NBI_TOUR_CONFIG_PATH`: Notebook Intelligence tour override file; defaults to
  `/opt/jovyan_defaults/.jupyter/nbi/tour_config.json`, which disables the
  first-run tour in Neurodesktop
- `NEURODESKTOP_FIREFOX_PROFILE_ROOT`: directory where the Neurodesktop Firefox
  wrapper stores display-specific profiles when an explicit profile root is
  needed. By default, the wrapper lets Firefox create and register profiles in
  its standard `~/.mozilla/firefox` profile store using names like
  `neurodesktop-display-1`
- `NEURODESKTOP_FIREFOX_PROFILE_DIR`: absolute Firefox profile directory override
  for the Neurodesktop Firefox wrapper; when unset, the wrapper derives a
  profile from `NEURODESKTOP_FIREFOX_PROFILE_ROOT` and the current `DISPLAY`
