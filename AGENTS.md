# AGENTS.md

## Agent Guidelines

- Keep changes scoped to the requested work and avoid unrelated refactors.
- When fixing a bug, test the fix before reporting that the problem is fixed.
- When implementing a new feature, add appropriate tests under tests/ and keep the docs/ and AGENTS.md up to date.
- Tests live in two tiers: `tests/unit/` runs on a checkout with `pytest
  tests/unit` and is the default home for new tests; `tests/container/` runs
  inside the built image with `pytest /opt/tests/` and is only for assertions
  that need a running container. Only `tests/container/` is copied into the
  image. Resolve a test's subject through the helpers in `tests/testlib.py`.
- Follow the project testing and container validation expectations in
  [`docs/testing.md`](docs/testing.md).
- Use [`docs/architecture.md`](docs/architecture.md) for project architecture,
  startup flow, build-time behavior, and directory layout.
- Use [`docs/environment-variables.md`](docs/environment-variables.md) for
  supported runtime and build environment variables.
- When changing the pilot execution receipt schema, CLI, fixtures, hashing
  rules, path-confinement contract, or trust conditionals, keep
  [`docs/pilot-execution-receipt.md`](docs/pilot-execution-receipt.md) aligned
  and run `pytest tests/unit/test_pilot_execution_receipt_schema.py
  tests/unit/test_pilot_execution_receipt_cli.py` from a checkout and `pytest
  /opt/tests/test_pilot_execution_receipt_image.py` in the built image.
- When changing `print_access_url.sh` (the end-of-startup access-link banner)
  or how `before_notebook.sh` launches it, run `pytest
  tests/unit/test_print_access_url.py` from a checkout.
- When changing the bounded ASTRA/Lightcone BET pilot, run `pytest
  tests/unit/test_astra_lightcone_bet_pilot_cli.py
  tests/unit/test_pilot_execution_receipt_cli.py` from a checkout and `pytest
  /opt/tests/test_astra_lightcone_bet_pilot_image.py` in the built image. Run
  its opt-in real module/Slurm acceptance only in a privileged native-amd64
  container with CVMFS and local Slurm ready; set
  `NEURODESKTOP_RUN_ASTRA_LIGHTCONE_PILOT=1` for that test.
- When changing the ASTRA viewer adapter, graph/gap model, previews, widget,
  vendored Cytoscape.js, manifest/receipt ingestion, or package pins, run
  `pytest tests/unit/test_astra_view_graph.py
  tests/unit/test_astra_view_packaging.py` from a checkout and `pytest
  /opt/tests/test_astra_view_image.py` in the built image. Keep every read path
  confined, keep `adapter.py` as the only released-schema-aware viewer module,
  and never promote the module-pilot receipt above `executed-unverified`.
- When changing OpenCode, its Web proxy/session-workspace behavior, its file
  previewer, or its pinned version, run `pytest tests/unit/test_opencode_web.py`
  from a checkout and `pytest /opt/tests/test_opencode_web_image.py` in the
  built image; the latter's real-bundle contract protects distinct durable
  project identities for per-session directories, Jupyter prefix routing, the
  native model picker, and the confinement of the preview file endpoint.
- When changing Notebook Intelligence or MyST pins or their frontend rebuilds,
  run `pytest tests/unit/test_nbi_settings_patch.py
  tests/unit/test_myst_build_workaround.py` from a checkout and `pytest
  /opt/tests/test_nbi_labextension_patch.py` in the built image, and verify both
  extensions are compatible in `jupyter labextension list --verbose`.
- When changing the Neurodesk launcher extension or how agent-authored
  absolute paths are routed into the JupyterLab main panel, run `pytest
  tests/unit/test_workspace_link_routing.py` from a checkout and `pytest
  /opt/tests/test_workspace_link_routing_image.py` in the built image. Keep
  the click interception scoped to same-origin, unmodified clicks resolving
  inside `PageConfig` `serverRoot`, and keep both plugins in the extension's
  default export. Rendered formats open in a viewer rather than the editor;
  keep that an extension-to-factory map that falls back to the default factory
  when the named viewer is not registered.
- When changing the `astra`/`lc` installs, `AGENT_SKILLS_REF`, or how the ASTRA
  skill reaches Codex, Claude, or OpenCode, run `pytest
  tests/unit/test_astra_jupyter_ai_tooling.py` from a checkout and `pytest
  /opt/tests/test_astra_agent_skills_image.py` in the built image. Keep
  `astra-tools` installed exactly once so the CLI cannot drift from the schema
  the viewer imports, keep `jq` installed for the plugin hooks, and bump
  `AGENT_SKILLS_REF` together with the ASTRA pins so the skill teaches the
  schema `astra validate` speaks.
- When changing Jupyter AI, Jupyter Collaboration, its ACP personas, or
  the Jupyter Server Documents workaround,
  run `pytest tests/unit/test_jupyter_ai_workspace.py
  tests/unit/test_astra_jupyter_ai_tooling.py
  tests/unit/test_jupyter_server_documents_patch.py` from a checkout and `pytest
  /opt/tests/test_astra_jupyter_ai_image.py` in the built image, then verify
  `pip check`, `jupyter server extension list`, and `jupyter labextension list
  --verbose` in that image. Keep Jupyter AI chat workspace seeding scoped to
  `.chat` saves, never overwrite an existing `AGENTS.md`, and never make a seed
  failure block the chat save. Keep the ACP adapters' vendored agent binaries
  deleted after install (npm ignores omit-optional for global installs) and
  keep the adapters driving the image's own agent CLIs via `CODEX_PATH` and
  `CLAUDE_CODE_EXECUTABLE`; the vendored copies would otherwise add ~500 MB
  of duplicates, and `CODEX_CLI_VERSION` must stay inside the `@openai/codex`
  range the pinned codex-acp declares.
- When changing an agentic workflow under `.github/workflows/*.md`, regenerate
  its `.lock.yml` with `gh aw compile`, then run
  `pytest tests/unit/test_report_job_failure_action.py`.
- Weekly code-maintenance workflows use the shared contract in
  `.github/workflows/shared/maintenance-base.md` and the CodeRabbit loop in
  `.github/workflows/maintenance-review.md`. Keep their `[maintenance] ` title
  prefix and `agentic-workflow` label aligned, compile every affected workflow,
  and run `pytest tests/unit/test_agentic_maintenance_workflows.py`.
- `.github/workflows/package-update-radar.md` is the read-only weekly package
  survey. It must keep its `[package-updates] ` title prefix, stay free of any
  `create-pull-request` safe output, and keep a weekly cron distinct from the
  maintenance workflows. `maintenance-updates` remains the only workflow that
  applies an upgrade. Compile it with `gh aw compile` and run
  `pytest tests/unit/test_agentic_maintenance_workflows.py`.
- Codex agentic workflows import the ordered model fallback in
  `.github/workflows/shared/agentic-models.md`. Keep GLM 5.2 ahead of Kimi 2.7,
  compile every affected workflow, and verify the generated model map in the
  focused agentic workflow tests.
