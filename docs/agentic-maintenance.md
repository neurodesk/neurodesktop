---
title: Agentic maintenance workflows
description: Weekly agentic maintenance checks, the package-update radar, and
  the CodeRabbit pull-request review loop
parent: index.md
status: current
last-reviewed: "2026-08-10"
---

# Agentic Maintenance Workflows

The architecture context for these workflows (including the issue
investigator they share their review loop with) is in
[Agentic CI workflows](architecture/agentic-workflows.md).

Neurodesktop runs seven agentic maintenance checks and one read-only package
survey once per week. Each source workflow uses gh-aw's fuzzy `weekly`
schedule, which gives it a stable scattered day and run time instead of
launching the whole suite at once. Every workflow also supports manual
dispatch.

All maintenance, package-survey, and issue-investigation workflows import the
shared `.github/workflows/shared/agentic-models.md` alias. The ordered
`neurodesk` candidates prefer GLM 5.2 and use Kimi 2.7 as the secondary model.

| Workflow | Focus | Non-negotiable guardrail |
| --- | --- | --- |
| `maintenance-test-pruning` | Redundant or obsolete tests | A remaining test must protect the same observable contract. |
| `maintenance-test-coverage` | Important untested behavior | Add behavioral, not line-coverage-only, protection. |
| `maintenance-updates` | Dependencies, tools, actions, and base images | One pinned update with upstream compatibility evidence. |
| `maintenance-abstraction-police` | Duplicate domain abstractions | Consolidate semantic duplication, not similar-looking code. |
| `maintenance-dead-code` | Unused code, configuration, assets, or dependencies | Check dynamic, build, workflow, and runtime callers before deletion. |
| `maintenance-docs-drift` | Documentation that disagrees with current behavior | Change documentation only when the implementation is authoritative. |
| `maintenance-flaky-tests` | Recurrent nondeterministic failures | Require repeated evidence and fix the cause without retries or weaker tests. |

## Package Update Radar

`package-update-radar` is the only weekly agentic workflow that produces no
code. It inventories every pinned third-party version — `Dockerfile` build
arguments and base image tag, pip/npm/conda/apt pins, the version-sensitive
JupyterLab extensions, the launcher extension manifests, composite actions, and
pinned tool versions under `scripts/` and `config/` — probes each component's
upstream release once, and classifies the gap as security, ready, needs review,
or blocked.

The report lands in a single tracking issue titled
`[package-updates] Pinned dependency radar` and labeled `agentic-workflow`.
Later runs add a comment with the complete current report and collapse the
previous ones, so the issue never fans out into a new issue per week. The
workflow has no `create-pull-request` safe output at all, so it cannot edit
files even if a run misbehaves.

The radar and `maintenance-updates` are deliberately split: the radar keeps a
ranked candidate list across the whole inventory, and `maintenance-updates`
applies at most one of those candidates per week with its own upstream
verification and container validation. A radar entry is a lead, not an
approval — `maintenance-updates` still has to prove the update independently.

## Pull Request and Review Loop

The seven maintenance workflows import the shared
`.github/workflows/shared/maintenance-base.md` contract
(`package-update-radar` deliberately does not — it has no pull-request
outputs, and its bounds live in its own body;
`tests/unit/test_agentic_maintenance_workflows.py` asserts it stays that
way). A run first checks for
an open pull request in its category, investigates one bounded candidate, and
either opens one narrow draft PR or exits with `noop`. Empty, speculative,
untested, and duplicate PRs are forbidden. The safe-output allowlist excludes
agentic workflow sources and generated lock files so a maintenance run cannot
rewrite its own controls.

Maintenance PR titles start with `[maintenance] <category>:` and carry the
`agentic-workflow` label. `.coderabbit.yaml` enables automatic CodeRabbit review
while the PR is still a draft. A CodeRabbit summary update triggers
`maintenance-review.md`, which reads the complete current review, validates each
finding against the latest head, batches valid fixes into one tested commit,
pushes once to the existing PR branch, and comments exactly
`@coderabbitai review` to start the next incremental review. The loop stops when
no actionable findings remain. It never marks a PR ready or merges it.

## Operational Limits

- Each category permits one open PR and one coherent change per run.
- Every Codex workflow has a hard model-turn ceiling: 30 turns for maintenance,
  radar, and review work, and 40 for the issue investigator. The prompt must
  reserve the final turn for its terminal safe output; the runner enforces the
  ceiling even when the model ignores the prose evidence budget.
- Investigation and network reads are bounded in the shared contract (for the
  radar, in its own workflow body).
- All Codex workflows install the shared provenance-aware timeout filter before
  execution. Only bare harness lifecycle records may report a process signal;
  repository files, test fixtures, and command output are untrusted transcript
  content and cannot manufacture a timeout.
- Product behavior changes require focused regression tests and the validation
  described in `docs/testing.md`.
- Protected or out-of-scope files require human review instead of an automated
  workaround.
- Generated `.lock.yml` files are changed only by `gh aw compile` during
  workflow development.
- `package-update-radar` permits one open tracking issue and no code changes.
  Its upstream probes are capped per run; a truncated survey must say so in the
  report's `Coverage` section rather than silently narrowing.

## Further Candidates

Two additional checks would be useful if the weekly PR volume remains
manageable:

- **Security-exception expiry:** verify that Trivy allowlist and ignore entries
  still apply to present dependencies, then remove one expired suppression with
  a clean scan.
- **Image/startup budget:** compare built-image size and measured startup phases
  against a recorded baseline, then propose one evidence-backed reduction when
  a persistent regression appears.

These should not be enabled until their baseline artifacts and reproducible
validators exist; without those, they would produce speculative cleanup PRs.
