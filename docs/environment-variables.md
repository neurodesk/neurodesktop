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
- `NEURODESKTOP_DESKTOP_BACKEND`: desktop backend started by `guacamole.sh`;
  supported values are `rdp`, `vnc`, and `both`. The Jupyter launcher sets this
  automatically for the separate RDP and VNC desktop entries
- `NEURODESKTOP_VERSION`: version tag set by CI
- `OPENCODE_MODEL_PROFILE`: set to `ollama`, `neurodesk`, `jetstream`, or
  `provider/model` to skip the interactive OpenCode model picker
- `OPENCODE_STARTUP_VERBOSE`: set to `1` to show detailed OpenCode provider
  probe output during startup
- `NEURODESK_API_KEY`: API key for `https://llm.neurodesk.org`. Shared by
  OpenCode and by the Notebook Intelligence JupyterLab plugin. OpenCode
  persists it to `~/.bashrc` on first setup, and `nbi_setup.sh` injects it
  into `~/.jupyter/nbi/config.json` on each JupyterLab startup and after
  each OpenCode run. `nbi_setup.sh` also mirrors the model selected in
  OpenCode (the top-level `model` in `~/.config/opencode/opencode.json`)
  into Notebook Intelligence, so picking a model in the OpenCode startup
  menu updates both tools; Notebook Intelligence sections pointed at a
  custom endpoint via its Settings UI are left alone
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
