---
title: Subscription-based agentic workflow redesign
description: Replace report-oriented gh-aw jobs with conventional Actions that use the existing Codex subscription and produce validated draft PRs
parent: index.md
status: implemented
last-reviewed: "2026-09-05"
---

# Subscription-based agentic workflow redesign

This record describes the September 2026 repository redesign. Installation of
the dedicated runner login and a live canary remain deployment work. Current
operation is documented in
[Agentic maintenance workflows](../agentic-maintenance.md) and
[Agentic CI workflows](../architecture/agentic-workflows.md).

## Problems in the previous setup

The previous design used gh-aw's Codex engine with the Neurodesk inference
gateway and an ordered GLM, Kimi, and MiniMax fallback. Selecting the Codex
engine selected the CLI, not OpenAI-hosted model inference. The maintainer
reported inadequate results from those models and requires the existing Codex
subscription because separate API access is unavailable.

Issue diagnosis and implementation were separate workflows. The automatic
investigator could comment but could not open a PR. A human had to dispatch
the fixer after accepting the diagnosis. That was intentional in the earlier
[reliability remediation](agentic-workflow-reliability-remediation.md), but it
did not meet the required issue-to-proposed-fix behavior.

The weekly rotation selected one of eight members. Each category therefore
ran about once every eight weeks. The separate package radar could publish
findings but could not apply updates. There was no dedicated scheduled
security-improvement category. Reporting and scheduling needed redesign along
with model access.

## Decision

Use conventional GitHub Actions and direct `codex exec` on the existing
self-hosted runner. The worker uses a dedicated local ChatGPT login persisted
on that runner. There are no OpenAI API keys, transferred account-auth secrets,
or open-weight inference fallback.

Remove the gh-aw Markdown sources, generated locks, and custom gh-aw timeout
detector. The generated execution code and detector patches no longer serve
the selected authentication method. Repository-owned Python now owns the
small set of task preparation, execution, validation, and publication steps.

The initial model is `gpt-5.6-sol`, configurable through
`AGENTIC_CODEX_MODEL`. The worker image pins Codex CLI `0.153.4`. This keeps the
model explicit and the executable reproducible while allowing deliberate
updates after a canary.

The [authentication research](agentic-codex-auth-research.md) verifies the
technical distinction. Local subscription login and `codex exec` saved-auth
reuse are documented. OpenAI's account-authentication CI guide explicitly
excludes public/open-source repositories. This public repository's custom
integration meets the maintainer's requirement outside that recommendation;
self-hosting does not change the guidance's scope.

## Alternatives and synthesis

We compared preserving separate diagnosis and fix workers with one worker
that owns investigation through a draft proposal. The single-worker design
removes the handoff that previously stopped at a diagnosis comment. Its
hosted publisher retains deterministic writes and retry recovery from the
phase-preserving alternative.

Keeping gh-aw with direct OpenAI API access failed the subscription-only
requirement. A private polling service could reuse local authentication, but
would add a service and queue outside the existing Actions runner. The
maintainer selected the existing runner, so Actions owns scheduling and job
records while the CLI provides subscription execution. The public-repository
authentication caveat remains documented rather than presented as solved by
that runner choice.

## Event and execution contract

A conventional completed-workflow reporter creates or updates issues for
failed CI, deployment, and agent workflows. Product failures enter normal
issue repair. Operational failures carry `agentic-operations` and do not
recursively trigger another repair agent. `agentic-ignore` provides an explicit
issue-level opt-out.

New issues run investigation and implementation through the same reusable
worker. A feasible repository fix proceeds to tests and a draft PR without a
manual diagnosis-to-fixer handoff. When no code change is justified, the run
reports evidence or a blocker instead of creating an empty PR.

The maintenance workflow runs at 04:23 UTC on weekdays, selecting `updates`,
`security`, `dead-code`, `test-coverage`, and `refactoring` in weekday order.
Each category runs weekly and is manually dispatchable. Each task proposes
one coherent improvement and checks for an existing category PR.

CodeRabbit follow-up uses the same worker and existing PR branch. Each finding
must survive inspection of the current head. At most three automatic
follow-up commits are permitted, counted through a commit marker. Human
review owns readiness and merging.

Hosted preparation gathers a task and exact revision. The self-hosted runner
builds the trusted worker image using its Docker cache and UID/GID build
arguments matching the runner, then starts the agent
container with the local Codex authentication directory. Global Actions
concurrency and a host file lock serialize use of the account state. Jobs
have finite time bounds.

Validation reconstructs the exact proposed patch in a clean checkout and
runs the unit suite in a second container with no account authentication or
network. Its record binds the result to the patch hash and preserves bounded
failure logs. An unconditional cleanup step removes recorded containers after
failure or cancellation. Hosted publication reads only `task.json`,
`result.json`, `change.patch`, and `validation.txt`. It checks patch paths and
the current PR state before writing. Raw Codex stdout, transcripts, and login
state are not published as artifacts.

Issue branches use `agentic/issue-N-RUNID`. Maintenance branches use
`agentic/maintenance-category-YYYY-Www`. Review work reuses the existing
branch. Preparation checks existing PRs, and reruns reconcile their task's
branch instead of blindly opening another PR.

The PR API uses `GITHUB_TOKEN`, preserving `github-actions[bot]` as PR author.
Optional `AGENTIC_PUSH_TOKEN` handles initial and review pushes with repository
Contents and Workflows write permissions. Optional `AGENTIC_CI_TOKEN` can push
a later empty commit to trigger ordinary CI. These GitHub credentials stay in
the hosted publisher and do not grant OpenAI model API access.

For proposed workflow changes, the publisher seeds the branch at the trusted
base with `GITHUB_TOKEN`, then pushes the candidate using the push token with
`[skip ci]`. The CI helper checks the cumulative workflow diff and refuses an
automatic empty commit when those changes remain. A maintainer reviews the
workflow before running its checks. For ordinary code changes without a CI
triggering credential, token-created PR events can await maintainer approval.
[GitHub workflow triggering](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)

## Trust and verification

The agent has no GitHub publishing token or Docker socket. The hosted
publisher does not execute the proposed checkout. The independent validator
has neither account authentication nor network access. Candidate workflow
edits do not replace the current run's trusted controller artifact. Patch
checks restrict size, file count, credential and Git-metadata paths, and new
symlinks or submodules.

The named Codex `worker` permissions profile denies model-tool reads from
`/codex`, `/proc`, and `/output`, forbids tool network access, and disables
hooks. A Docker probe with dummy credentials checks those restrictions before
every model run. The outer container permits unconfined seccomp and AppArmor
to allow the nested Bubblewrap sandbox, while dropping all capabilities and
retaining `no-new-privileges`. Docker alone does not hide the auth mount.

Before upload, the controller rejects known token values in the patch or
result. This exact-value scan is an additional check, not proof against every
possible disclosure. Codex itself and the trusted controller need access to
the local account state. The production ARC runner must expose checkout,
`RUNNER_TEMP`, and persistent authentication paths at the same absolute
locations to its Docker-in-Docker sidecar. An administrator supplies the PVC
and shared mounts.

Checkout tests must pass before publication. Image-dependent checks may
remain pending on the draft, with their exact commands and limitations stated.
The draft must not imply that container behavior was verified by unit tests.
A failed check or invalid proposal produces a visible failure rather than a
false successful fix.

The repository verification targets are:

```bash
pytest tests/unit/test_agentic_worker.py \
  tests/unit/test_report_workflow_failure.py \
  tests/unit/test_agentic_maintenance_workflows.py
```

The final worker image passed all 519 checkout unit tests in 44 seconds.
`python3 .github/scripts/check_agentic_sandbox.py` passed against that Docker
image with dummy credentials and no model call. Actionlint passed every
changed workflow. Documentation frontmatter and relative links also passed
validation.

These checks do not prove production account entitlement, persistent storage,
or model access. Administrator setup and a live issue-to-draft-PR canary
remain required for deployment.

Failed publication is retried with **Re-run failed jobs**, preserving the
prepared task and candidate artifacts. Rerunning all jobs skips an existing
owned PR during preparation and cannot finish its incomplete publication.
The ordinary `GITHUB_TOKEN` can reject workflow-file patch pushes. A
repository-scoped `AGENTIC_PUSH_TOKEN` with Contents and Workflows write
permissions enables those proposals. `AGENTIC_CI_TOKEN` alone does not grant
that initial-push capability.

## Adversarial review on 2026-09-07

One review round used three independent reviewers: the parent model,
GPT-5.6 Sol, and GPT-5.6 Terra. The accepted findings were:

- Quoted task markers could suppress another issue or select its PR. Match
  only the controller's first-line marker.
- Unrelated PR bodies and long review histories could exceed the task budget.
  Bound each context section and disclose truncation.
- Review follow-ups omitted their new validation and pending image checks.
  Publish evidence tied to the updated revision.
- Public issue creation could consume the subscription without admission
  checks. Require `agentic-approved` for external reporters; preserve automatic
  maintainer reports and CI failure dispatch.
- Candidate pytest configuration could omit existing tests. Preserve the base
  test suite separately and run it against candidate sources before the
  candidate's own suite.
- Unbounded subprocess output could exhaust host memory. Limit captured output
  and terminate commands that exceed the limit.
- Missing labels could prevent operational reporting. Provision labels in the
  hosted readiness job before the self-hosted probe and activation.
- Disabled execution could still consume a reporter dispatch marker. Defer
  dispatch and its marker until activation; older issues remain available for
  manual repair dispatch.

A skipped preparation job was also claimed to launch its dependent candidate.
GitHub's implicit success condition already skips a dependency that did not
succeed. The condition now explicitly requires successful preparation and
`skip=false`, making the intended behavior visible without relying on that
implicit condition.

The deployment now starts inactive. A manual readiness workflow checks the
real runner's mounts and authentication-file permissions without using a
model; `AGENTIC_ENABLED=true` then permits the issue-to-PR canary and unattended
work. This does not change OpenAI's public-repository account-auth guidance.
