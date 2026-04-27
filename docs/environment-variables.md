# Environment Variables

- `CVMFS_DISABLE`: set to `true` to disable CVMFS mounting
- `CVMFS_MODULES`: CVMFS module catalogue path used when refreshing
  `MODULEPATH`; defaults to `/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/`
- `NEURODESKTOP_LOCAL_CONTAINERS`: local container root used to derive
  `OFFLINE_MODULES`; defaults to `/neurodesktop-storage/containers`
- `OFFLINE_MODULES`: local Lmod module path derived from
  `NEURODESKTOP_LOCAL_CONTAINERS`
- `NB_UID`, `NB_GID`: user and group IDs for permission matching
- `START_LOCAL_LLMS`: set to `1` to enable Ollama with the Neurodesk model
- `NEURODESKTOP_VERSION`: version tag set by CI
- `OPENCODE_MODEL_PROFILE`: set to `ollama`, `neurodesk`, `jetstream`, or
  `provider/model` to skip the interactive OpenCode model picker
- `OPENCODE_STARTUP_VERBOSE`: set to `1` to show detailed OpenCode provider
  probe output during startup
- `NEURODESK_API_KEY`: API key for `https://llm.neurodesk.org`. Shared by
  OpenCode and by the Notebook Intelligence JupyterLab plugin. OpenCode
  persists it to `~/.bashrc` on first setup, and `nbi_setup.sh` injects it
  into `~/.jupyter/nbi/config.json` on each JupyterLab startup
