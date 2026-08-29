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
- The docs are a hierarchical wiki rooted at [`docs/index.md`](docs/index.md):
  every page carries YAML frontmatter (`title`, `description`, `parent`,
  `status`, `last-reviewed`) and cross-references relatives with markdown
  links. Historical assessments, plans, and audits live under
  [`docs/designs/`](docs/designs/index.md) as records; keep current behavior
  in the reference pages, not in the records.
- Follow the project testing and container validation expectations in
  [`docs/testing.md`](docs/testing.md).
- Use [`docs/architecture.md`](docs/architecture.md) for project architecture,
  startup flow, and directory layout; it links to one page per subsystem
  under [`docs/architecture/`](docs/architecture/), including
  [build-time behavior](docs/architecture/build.md).
- Use [`docs/environment-variables.md`](docs/environment-variables.md) for
  supported runtime and build environment variables.
- `docs/architecture.md`, `docs/testing.md`, and
  `docs/environment-variables.md` are referenced by path from tests and the
  compiled agentic workflows; do not move or rename them.
- When changing the automatic `APPTAINER_NV` setup in
  `environment_variables.sh`, run `pytest tests/unit/test_apptainer_nv.py`
  from a checkout. Gate the default on the loaded proprietary driver at
  `/proc/driver/nvidia/version`, and preserve every explicit value so
  `APPTAINER_NV=0` remains the per-command opt-out for incompatible host
  libraries.
- When changing `print_access_url.sh` (the end-of-startup access-link banner)
  or how `before_notebook.sh` launches it, run `pytest
  tests/unit/test_print_access_url.py` from a checkout.
- When changing `scripts/connectSherlock.sh`, run `pytest
  tests/unit/test_connect_sherlock.py` from a checkout. Keep its self-update URL
  on the copy in this repository and let the Neurodesktop image own
  `ServerApp.jpserver_extensions`; the Sherlock launcher must not disable
  `jupyter_server_fileid`, which `jupyter-server-documents` requires.
- When changing Jupyter Server Proxy response buffering or the Tornado HTTP
  client limits in `jupyter_server_config_extra.py` or
  `patch_jupyter_server_proxy.py`, run `pytest
  tests/unit/test_jupyter_server_proxy_limits.py` from a checkout, `pytest
  /opt/tests/test_jupyter_server_proxy_limits.py` in the built image, and proxy
  a response larger than 100 MiB through that image. Keep
  `max_buffer_size` and `max_body_size` aligned at the bounded 1024 MiB limit;
  ordinary proxy responses are buffered in the single-user Jupyter process.
  Pass both limits directly to the TCP and Unix-socket client constructors so
  JupyterHub's later `AsyncHTTPClient.configure()` call cannot reset them.
  Unix-socket webapps must still construct their client through the configured
  `AsyncHTTPClient` factory while preserving `UnixResolver`.
- When changing the ASTRA viewer adapter, graph/gap model, presentation
  projection, layout ranks, previews, widget, SVG renderer, run-evidence
  ingestion, or package pins, run `pytest tests/unit/test_astra_view_graph.py
  tests/unit/test_astra_view_packaging.py` from a checkout and `pytest
  /opt/tests/test_astra_view_image.py` in the built image. Keep every read path
  confined and keep `adapter.py` as the only released-schema-aware viewer
  module. Schema drift the viewer can read unambiguously — a retired
  `narrative`/`authors`, an undefined top-level key, an option insight naming
  an ancestor's insight — is adopted with a warning rather than refused, and
  every adoption must surface in `graph["warnings"]`; keep that confined to
  top-level keys and to references with no other possible target, so an
  authoring mistake inside a decision or output stays an error.
  The drawn graph is the presentation projection `projection.py` derives from
  the semantic graph (stages, grouped inputs/outputs, per-stage decision
  clusters, folded evidence, a synthetic result node); keep the frontend a
  self-contained SVG renderer with no vendored graph library, and keep all
  layout arithmetic in Python: node positions come from the per-view
  `rank`/`order` pairs `projection.py` computes through `layout.py`, never
  from the frontend. The renderer must re-lay out on every filter change, and
  each viewer mode must actually filter — a mode that shows everything is
  indistinguishable from the one before it. `tests/fixtures/astra-bet` is the single canonical worked ASTRA spec:
  the unit tests read it from the checkout, the image tests validate its
  installed copy at `/opt/neurodesktop/examples/astra-bet`, and users copy that
  installed copy as a starting point — do not fork a second source copy.
- When changing the file-browser ASTRA viewer — the
  `neurodesk_astra_view.serverext` server extension or the
  `neurodesk-launcher:astra-viewer` file type/factory plugin — run `pytest
  tests/unit/test_astra_view_filebrowser.py` from a checkout and `pytest
  /opt/tests/test_astra_view_image.py` in the built image. Keep request paths
  confined to the Jupyter server root before anything is read, keep the
  frontend single-sourced from the anywidget's `static/` assets over the
  asset endpoint (never a second bundled copy), and keep the factory a
  pattern-file-type default so ordinary `.yaml` files stay in the editor.
  The plugin discovers run evidence beside the spec and fills in `run=`
  itself, because nothing else can: a name it recognises that `manifest.py`
  does not is a manifest that never loads, so keep
  `ASTRA_RUN_EVIDENCE_NAMES` in step with `_directory_run_file`. Ambiguity
  stays Python's call — two or more candidates send the directory rather
  than a guess — and run evidence appears without touching the spec, so the
  viewer needs its `Refresh` to see a job that finished.
- When changing Notebook Intelligence, MyST/RISE pins or frontend rebuilds, or
  the standalone RISE page-config patch, run `pytest
  tests/unit/test_nbi_settings_patch.py
  tests/unit/test_myst_build_workaround.py
  tests/unit/test_jupyterlab_rise_patch.py` from a checkout and `pytest
  /opt/tests/test_nbi_labextension_patch.py
  /opt/tests/test_rise_slides_image.py` in the built image, and verify both
  extensions are compatible in `jupyter labextension list --verbose`. Keep
  the standalone app confined to `jupyterlab-rise` and the MyST bundle built
  against it; full JupyterLab extensions can require services RISE does not
  provide or disable the notebook cell executor it needs.
- When changing the Neurodesk launcher extension or how agent-authored
  absolute paths are routed into the JupyterLab main panel, run `pytest
  tests/unit/test_workspace_link_routing.py` from a checkout and `pytest
  /opt/tests/test_workspace_link_routing_image.py` in the built image. Keep
  the click interception scoped to same-origin, unmodified clicks resolving
  inside `PageConfig` `serverRoot`, and keep all of the extension's plugins in
  its default export. Rendered formats open in a viewer rather than the editor;
  keep that an extension-to-factory map that falls back to the default factory
  when the named viewer is not registered.
- When changing `config/slurm/astra_lc_run.sbatch` — the optional `lc`
  execution path — run `pytest tests/unit/test_astra_lc_run_sbatch.py` from a
  checkout and `pytest /opt/tests/test_astra_lc_run_image.py` in the built
  image. `lc run` never submits to Slurm; it dispatches through Dask and
  launches workers with `srun` once inside an allocation, so the template must
  stay something `sbatch` submits rather than something that wraps `sbatch`.
  Keep `/opt/uv/tools/lightcone-cli/bin` on its `PATH` (`lc` shells out to the
  `dask` CLI and neither is on the default `PATH`), keep it writing exactly one
  recognised manifest beside the spec — `status.json`, renamed into place —
  and keep it refusing a spec that declares a `container:`, since Apptainer is
  not an `lc` runtime and the image would be recorded as used without ever
  running. This path is amber by construction; do not add anything that
  synthesizes the verification record `lc verify` does not write.
- When changing the `astra`/`lc` installs, `AGENT_SKILLS_REF`, or how the ASTRA
  skill reaches Codex, Claude, or OpenCode, run `pytest
  tests/unit/test_astra_jupyter_ai_tooling.py` from a checkout and `pytest
  /opt/tests/test_astra_agent_skills_image.py` in the built image. Keep
  `astra-tools` installed exactly once so the CLI cannot drift from the schema
  the viewer imports, keep `jq` installed for the plugin hooks, and bump
  `AGENT_SKILLS_REF` together with the ASTRA pins so the skill teaches the
  schema `astra validate` speaks. The Lightcone `reproduction` plugin already
  bundles ASTRA, so install it instead of installing both plugins. OpenCode
  receives the plugin's complete skill closure and adapts the same pinned hook
  scripts through `config/agents/opencode_lightcone_hooks.js`; keep hook
  failures non-blocking and return their context through the system prompt or
  tool output so the model actually sees it.
- When changing Jupyter AI, Jupyter Collaboration, its ACP personas,
  the Jupyter Server Documents workaround, the widget pins used with
  server-side notebook execution, or the jupyter-server-mcp banner patch,
  run `pytest tests/unit/test_jupyter_ai_workspace.py
  tests/unit/test_astra_jupyter_ai_tooling.py
  tests/unit/test_jupyter_server_documents_patch.py
  tests/unit/test_neurodesktop_stream_output.py
  tests/unit/test_ipyniivue_patch.py
  tests/unit/test_widget_browser_diagnostics.py
  tests/unit/test_ipywidgets_control_comm_patch.py
  tests/unit/test_jupyterlab_widgets_patch.py
  tests/unit/test_jupyter_ai_acp_client_patch.py
  tests/unit/test_jupyter_server_mcp_patch.py` from a checkout and `pytest
  /opt/tests/test_astra_jupyter_ai_image.py
  /opt/tests/test_widget_compatibility_image.py` in the built image, then verify
  `pip check`, `jupyter server extension list`, and `jupyter labextension list
  --verbose` in that image. Keep user-initiated server-side code execution
  marking the cell trusted before its outputs arrive; otherwise unsafe rich
  renderers fall back to `text/plain`. The widget image test must execute and
  re-execute a delayed nested widget through JupyterLab after repeated stream
  output, then replay the populated room in a second client; it must not only
  inspect installed bundles. Keep every widget control-state reply on the comm
  that requested it; the backend's shared latest comm cannot safely route two
  clients restoring the same kernel. Keep notebook-room outputs as CRDT maps,
  coalesce each contiguous stream, and keep every stream output's `text` as a
  CRDT text value; separate maps duplicate replayed fragments, while plain
  dictionaries or strings break JupyterLab's next `appendStreamOutput()`
  update. The
  coalescing rules live in `config/jupyter/neurodesktop_stream_output.py`, a
  port of JupyterLab's `Private.processText`/`addText` that the patcher
  installs into the package; keep the port and its unit-test parity vectors
  in step with JupyterLab, keep its per-cell state to length, cursor, and a
  running hash — never the text — so a long stream is neither retained in
  memory nor rebuilt on end-of-text appends, and keep every pycrdt `Text`
  index in UTF-8 bytes.
  Wait for the status bar to name the selected kernel and report `Idle` before
  clicking Run; the notebook execution indicator can report `idle` before a
  kernel exists. Give the replay client an explicit separate JupyterLab
  workspace; automatic relocation away from an already-open workspace aborts
  in-flight plugin asset requests. Let that workspace finish activating plugins
  before opening the replay notebook, so document restoration is not part of
  application bootstrap. Keep the disposable browser profile allowing Firefox's
  software WebGL fallback without forcing Mesa's driver mode, require a WebGL2
  capability probe before opening JupyterLab, and retry only a fresh Firefox
  process that fails that startup probe. If every probe fails, omit only the
  NiiVue cells and canvas assertions; the stream, delayed widget, re-execution,
  and second-client replay must still run, and the test must warn with the
  Firefox/WebGL diagnostics. Never retry the notebook or replay assertions;
  their failures must include the WebGL renderer and context state plus bounded
  Firefox and Jupyter Server log tails. When WebGL2 is available, interact with
  all nine NiiVue canvases and require scene-model traffic to stop at idle;
  instrument intervals created by the shared asset so a permanent frontend
  polling loop cannot pass merely because it emits no model delta.
  Keep reconnect repair safe on both sides: a SyncStep2 arriving after the
  handshake timeout must still be applied without disconnecting its client,
  pending replies stay keyed per client, and the frontend divergent-history
  repair deletes only Yjs ordered items not covered by the server state vector.
  A repeated repair must never delete server-owned notebook cells.
  Keep `ipykernel` on the stable 6.x line until its experimental comm subshells
  can create a per-target subshell for delayed server-executed widgets without
  canceling the control future.
  Keep the late-model wait bounded at ten seconds and the bulk control-state
  wait bounded at thirty seconds. The upstream two- and four-second waits are
  too short for complex output over the independent WebSockets, and the
  per-model fallback can race a late bulk response. Install the manager-backed
  renderer factory before walking existing renderers; collaboration output
  created between the upstream operations otherwise stays at `Loading widget...`
  without requesting kernel state. Publish patched federated assets under new
  content-derived names; Jupyter serves
  their original hashed URLs as immutable for one year. Keep ipyniivue's large
  ESM in one content-hashed JupyterLab asset rather than syncing it with every
  model, but create each widget definition from a fresh factory so NiiVue and
  scene synchronization state are never shared between models. Send scene
  changes once from each focused `NiiVue.sync()` event; never restore the
  upstream 30 ms polling interval. Model destruction must run NiiVue cleanup
  and relinquish its WebGL context; view removal alone must keep supporting
  redisplay. Keep
  Jupyter AI chat workspace seeding scoped to `.chat` saves, never overwrite
  an existing `AGENTS.md`, and never make a seed failure block the chat save.
  Keep the ACP adapters' vendored agent binaries
  deleted after install (npm ignores omit-optional for global installs) and
  keep the adapters driving the image's own agent CLIs via `CODEX_PATH` and
  `CLAUDE_CODE_EXECUTABLE`; the vendored copies would otherwise add ~500 MB
  of duplicates, and `CODEX_CLI_VERSION` must stay inside the `@openai/codex`
  range the pinned codex-acp declares. The same vendored-duplicate policy
  covers `claude-agent-sdk/_bundled` (a second ~260 MB Claude CLI pulled in
  via notebook_intelligence): it is deleted in the pip layer and guarded by
  `pytest /opt/tests/test_image_size_hygiene.py`.
- The image's size-hygiene invariants live in
  [`docs/architecture/build.md`](docs/architecture/build.md#image-size-hygiene)
  and are asserted by `pytest /opt/tests/test_image_size_hygiene.py` in the
  built image: build-only apt packages are installed and purged inside the
  layer that needs them (never purged in a later layer), `chown -R`/
  `chmod -R` happen in the layer that creates a tree, and sourcemaps and
  bundled Python test suites are stripped in the layer that installs them.
- When changing an agentic workflow under `.github/workflows/*.md`, regenerate
  its `.lock.yml` with `gh aw compile`, then run
  `pytest tests/unit/test_report_job_failure_action.py`.
- Weekly code-maintenance workflows use the shared contract in
  `.github/workflows/shared/maintenance-base.md` and the CodeRabbit loop in
  `.github/workflows/maintenance-review.md`. Their schedules are owned by
  `.github/workflows/agentic-maintenance-rotation.yml`, which must dispatch
  exactly one of the seven maintenance workflows or the package radar per
  weekly run. Keep their `[maintenance] ` title prefix and `agentic-workflow`
  label aligned, compile every affected workflow, and run `pytest
  tests/unit/test_agentic_maintenance_workflows.py`.
- `.github/workflows/package-update-radar.md` is the read-only weekly package
  survey. It must keep its `[package-updates] ` title prefix, stay free of any
  `create-pull-request` safe output, and remain one member of the shared weekly
  rotation. `maintenance-updates` remains the only workflow that applies an
  upgrade. Compile it with `gh aw compile` and run
  `pytest tests/unit/test_agentic_maintenance_workflows.py`.
- Issue handling is split between read-only `.github/workflows/issue-investigator.md`
  and manually dispatched `.github/workflows/issue-fixer.md`. Keep diagnosis
  free of repository-write safe outputs, keep the fixer PR title prefix aligned
  with `.github/workflows/issue-investigator-review.md`, compile both affected
  workflows, and run `pytest tests/unit/test_report_job_failure_action.py`.
- Codex agentic workflows import the ordered model fallback in
  `.github/workflows/shared/agentic-models.md`. Keep GLM 5.2 ahead of Kimi 2.7,
  compile every affected workflow, and verify the generated model map in the
  focused agentic workflow tests.
- Every Codex workflow installs
  `.github/scripts/gh_aw_detect_agent_errors_wrapper.cjs` in
  `pre-agent-steps`. When
  changing that wrapper or its hooks, run `pytest
  tests/unit/test_gh_aw_recovered_timeout_filter.py
  tests/unit/test_agentic_maintenance_workflows.py`. Preserve real bare harness
  lifecycle signals, recovered-attempt handling, and byte-for-byte transcript
  restoration; preserve the upstream detector's public module exports because
  the Codex harness imports them. Timeout-shaped repository and tool output is
  untrusted data and must not affect failure classification. Keep a hard
  `max-turns` ceiling on every Codex workflow. Keep the wrapper under `.github/`
  so PR review runs restore the trusted base-branch copy before installing it.
