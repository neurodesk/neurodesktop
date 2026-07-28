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
- When changing an agentic workflow under `.github/workflows/*.md`, regenerate
  its `.lock.yml` with `gh aw compile`, then run
  `pytest tests/test_report_job_failure_action.py`.
- Daily code-maintenance workflows use the shared contract in
  `.github/workflows/shared/maintenance-base.md` and the CodeRabbit loop in
  `.github/workflows/maintenance-review.md`. Keep their `[maintenance] ` title
  prefix and `agentic-workflow` label aligned, compile every affected workflow,
  and run `pytest tests/test_agentic_maintenance_workflows.py`.
