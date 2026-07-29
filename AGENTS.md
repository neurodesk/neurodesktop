# AGENTS.md

## Agent Guidelines

- Keep changes scoped to the requested work and avoid unrelated refactors.
- When fixing a bug, test the fix before reporting that the problem is fixed.
- When implementing a new feature, add appropriate tests under tests/ and keep the docs/ and AGENTS.md up to date.
- Follow the project testing and container validation expectations in
  [`docs/testing.md`](docs/testing.md).
- Use [`docs/architecture.md`](docs/architecture.md) for project architecture,
  startup flow, build-time behavior, and directory layout.
- Use [`docs/environment-variables.md`](docs/environment-variables.md) for
  supported runtime and build environment variables.
- When changing OpenCode, its Web proxy/session-workspace behavior, its file
  previewer, or its pinned version, run
  `pytest tests/test_opencode_web.py` in the built image; its real-bundle
  contract protects distinct durable project identities for per-session
  directories, Jupyter prefix routing, the native model picker, and the
  confinement of the preview file endpoint.
- When changing Notebook Intelligence or MyST pins or their frontend rebuilds,
  run `pytest tests/test_nbi_settings_patch.py tests/test_myst_build_workaround.py
  tests/test_myst_rise_build.py` in the built image and verify both extensions
  are compatible in `jupyter labextension list --verbose`.
- When changing an agentic workflow under `.github/workflows/*.md`, regenerate
  its `.lock.yml` with `gh aw compile`, then run
  `pytest tests/test_report_job_failure_action.py`.
- Weekly code-maintenance workflows use the shared contract in
  `.github/workflows/shared/maintenance-base.md` and the CodeRabbit loop in
  `.github/workflows/maintenance-review.md`. Keep their `[maintenance] ` title
  prefix and `agentic-workflow` label aligned, compile every affected workflow,
  and run `pytest tests/test_agentic_maintenance_workflows.py`.
- `.github/workflows/package-update-radar.md` is the read-only weekly package
  survey. It must keep its `[package-updates] ` title prefix, stay free of any
  `create-pull-request` safe output, and keep a weekly cron distinct from the
  maintenance workflows. `maintenance-updates` remains the only workflow that
  applies an upgrade. Compile it with `gh aw compile` and run
  `pytest tests/test_agentic_maintenance_workflows.py`.
- Codex agentic workflows import the ordered model fallback in
  `.github/workflows/shared/agentic-models.md`. Keep GLM 5.2 ahead of Kimi 2.7,
  compile every affected workflow, and verify the generated model map in the
  focused agentic workflow tests.
