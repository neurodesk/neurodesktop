# Agentic Maintenance Workflows

Neurodesktop runs seven agentic maintenance checks every day. Each source
workflow uses gh-aw's fuzzy `daily` schedule, which gives it a stable scattered
run time instead of launching the whole suite at once. Every workflow also
supports manual dispatch.

| Workflow | Focus | Non-negotiable guardrail |
| --- | --- | --- |
| `maintenance-test-pruning` | Redundant or obsolete tests | A remaining test must protect the same observable contract. |
| `maintenance-test-coverage` | Important untested behavior | Add behavioral, not line-coverage-only, protection. |
| `maintenance-updates` | Dependencies, tools, actions, and base images | One pinned update with upstream compatibility evidence. |
| `maintenance-abstraction-police` | Duplicate domain abstractions | Consolidate semantic duplication, not similar-looking code. |
| `maintenance-dead-code` | Unused code, configuration, assets, or dependencies | Check dynamic, build, workflow, and runtime callers before deletion. |
| `maintenance-docs-drift` | Documentation that disagrees with current behavior | Change documentation only when the implementation is authoritative. |
| `maintenance-flaky-tests` | Recurrent nondeterministic failures | Require repeated evidence and fix the cause without retries or weaker tests. |

## Pull Request and Review Loop

The workflows import the shared
`.github/workflows/shared/maintenance-base.md` contract. A run first checks for
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
- Investigation and network reads are bounded in the shared contract.
- Product behavior changes require focused regression tests and the validation
  described in `docs/testing.md`.
- Protected or out-of-scope files require human review instead of an automated
  workaround.
- Generated `.lock.yml` files are changed only by `gh aw compile` during
  workflow development.

## Further Candidates

Two additional checks would be useful if the daily PR volume remains
manageable:

- **Security-exception expiry:** verify that Trivy allowlist and ignore entries
  still apply to present dependencies, then remove one expired suppression with
  a clean scan.
- **Image/startup budget:** compare built-image size and measured startup phases
  against a recorded baseline, then propose one evidence-backed reduction when
  a persistent regression appears.

These should not be enabled until their baseline artifacts and reproducible
validators exist; without those, they would produce speculative cleanup PRs.
