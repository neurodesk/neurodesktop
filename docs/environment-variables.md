# Environment Variables

- `CVMFS_DISABLE`: set to `true` to disable CVMFS mounting
- `NB_UID`, `NB_GID`: user and group IDs for permission matching
- `START_LOCAL_LLMS`: set to `1` to enable Ollama with the Neurodesk model
- `NEURODESKTOP_VERSION`: version tag set by CI
- `OPENCODE_MODEL_PROFILE`: set to `ollama`, `neurodesk`, `jetstream`, or
  `provider/model` to skip the interactive OpenCode model picker
- `OPENCODE_STARTUP_VERBOSE`: set to `1` to show detailed OpenCode provider
  probe output during startup
