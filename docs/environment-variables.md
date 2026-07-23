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
  `provider/model` to skip the interactive OpenCode model picker
- `OPENCODE_STARTUP_VERBOSE`: set to `1` to show detailed OpenCode provider
  probe output during startup
- `OPENCODE_WEB_STARTUP_TIMEOUT`: seconds `opencode_web.py` (the "OpenCode AI"
  launcher tile) waits for the `opencode web` backend to become ready;
  defaults to `180`
- `OPENCODE_DISABLE_FFF`: forced to `1` for the OpenCode Web child process so
  its Add Project dialog can search below the `/home/jovyan` startup directory.
  The terminal OpenCode workflow is unaffected
- `OPENCODE_WEB_DESKTOP_STATE`: state file where the desktop "OpenCode Web"
  shortcut records its launcher's PID and dynamically allocated port;
  defaults to `~/.neurodesk/run/opencode_web_desktop.state`
- `OPENCODE_WEB_WRAPPER_BIN`, `OPENCODE_WEB_SECRET_FILE`,
  `OPENCODE_WEB_LOGIN_TOKEN_FILE`, `NEURODESK_LLM_BASE_URL`: test overrides
  for `opencode_web.py` (backend command, credential file, single-use login
  token file, and key-validation endpoint)
- `OPENCODE_VERSION` (build argument): the OpenCode release installed into
  the image; defaults to the validated pin in the Dockerfile (currently
  `1.18.4`). Override to bump the pin, or set it to an empty value to
  install the latest release
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
