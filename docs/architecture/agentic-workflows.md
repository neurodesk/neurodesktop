---
title: Agentic CI workflows
description: Failure reporting, issue repair, scheduled maintenance, and subscription-authenticated Codex execution through conventional Actions
parent: ../architecture.md
status: current
last-reviewed: "2026-09-07"
---

# Agentic CI workflows

Part of [Architecture](../architecture.md). See
[Agentic maintenance workflows](../agentic-maintenance.md) for installation,
repository variables, schedules, and recovery. The
[redesign record](../designs/agentic-subscription-redesign.md) explains the
previous gaps and the subscription requirement.

## Events lead to a reviewable result

A completed-workflow reporter observes CI, deployment, and agent failures and
creates or updates a linked issue. Product failures and user-created issues
enter [`agentic-issue.yml`](../../.github/workflows/agentic-issue.yml). External
reporters need a maintainer's `agentic-approved` label or manual dispatch before
subscription execution; owners, members, and collaborators enter automatically. Agent
operational failures are classified separately so they do not recursively
launch agents to repair their own unavailable authentication or runner.

The issue workflow delegates investigation, implementation, and validation to
[`agentic-worker.yml`](../../.github/workflows/agentic-worker.yml). There is
no separate manual handoff between diagnosis and fixing. A confirmed defect
with a feasible fix leads to a draft PR. A task with no justified code change
reports its evidence or blocker.

[`agentic-maintenance.yml`](../../.github/workflows/agentic-maintenance.yml)
uses the same worker for five weekly categories: updates, security, dead code,
test coverage, and structural refactoring. The 04:23 UTC weekday schedule selects those categories in the listed order. Each task creates at most one coherent improvement and
skips a category with an existing owned open PR.

[`agentic-review.yml`](../../.github/workflows/agentic-review.yml) handles
CodeRabbit feedback on generated PRs. It validates findings against the latest
head and uses the worker to update that branch. A PR permits at most three
automatic follow-up commits. Human review owns readiness and merging.

## Preparation, execution, validation, and publication

Hosted preparation collects the task context and a known repository revision.
Preparation runs only when `AGENTIC_ENABLED=true`; the manual runner-readiness
workflow checks deployment prerequisites before activation.
Issue text, review comments, and failure logs remain data. The worker's trusted
task markers must occupy the first line; quoted markers cannot suppress other
issues. Context sections have explicit size budgets and truncation notices,
so unrelated PR descriptions cannot exhaust the prompt. The worker's trusted
control code is separate from the checkout the model can edit.

The Codex job runs on the configured existing self-hosted runner. It invokes
`codex exec` in a disposable Docker container using a persistent dedicated
ChatGPT login. Global Actions concurrency queues worker jobs, and a host lock
serializes access to the authentication directory. The CLI refreshes that
state in place. Jobs have finite execution bounds.

The agent container receives a checkout, task context, and Codex authentication.
It receives no GitHub publication token or Docker socket. The named Codex
`worker` permissions profile denies model-tool access to `/codex`, `/proc`,
and `/output`, denies tool networking, and makes Git metadata read-only.
Hooks are disabled. Before each model run, a credential-free Docker probe
checks those restrictions using the actual image. The controller reconstructs
the candidate from its exact patch in a clean checkout. A second container
validates that checkout with no account credentials and no network. Its
base test suite was snapshotted before the model ran and is mounted read-only.
It exercises the patched sources before the candidate suite runs, using
trusted pytest configuration. Captured output is limited to 8 MiB per stream;
overflow and timeouts terminate the command. The
validation record includes the patch hash and retains bounded failure logs.
This separates the agent's own test claims from the checks that authorize
publication.

Hosted publication reads `task.json`, `result.json`, `change.patch`, and
`validation.txt`, then writes a task branch and opens or updates its draft PR.
Raw Codex stdout and authentication state are excluded from artifacts. The
publisher owns GitHub write credentials. Patches are bounded and cannot add
credential paths, Git metadata, symlinks, or submodules. Proposed workflow
changes do not replace the current run's separately prepared controller.
The publisher rejects a stale target rather than overwriting a newer change.
Rerunning a failed publisher reconciles the same task branch and PR. Rerunning
preparation skips an issue with an existing owned open PR. That distinction
matters when publication failed after creating the PR. An unconditional
cleanup step removes the recorded execution container after cancellation or
failure.

The PR API retains `GITHUB_TOKEN` so generated PRs have the expected bot
author. Optional `AGENTIC_PUSH_TOKEN` authenticates initial and review pushes,
including workflow changes, with repository Contents and Workflows write
permissions. Optional `AGENTIC_CI_TOKEN` creates a later empty commit for
ordinary CI. Both tokens remain in the hosted publisher.

For workflow changes, the publisher seeds the branch at the trusted base
using `GITHUB_TOKEN`, then includes `[skip ci]` in the token-authenticated
candidate push. The CI helper refuses to trigger checks when the cumulative
PR diff changes `.github/workflows`. Those checks wait for maintainer review.

Checkout tests gate draft publication. Image-specific checks that cannot run
in the offline validator remain explicit pending work in the draft, following
[Testing](../testing.md). A green worker job is not evidence that those image
checks passed.

## Subscription authentication and remaining trust

Each worker run builds `neurodesktop-agentic:codex-0.153.4` from the trusted
[`config/agentic/Dockerfile`](../../config/agentic/Dockerfile), using Docker's
layer cache. `WORKER_UID` and `WORKER_GID` build arguments create the runner's
identity inside the image. The image pins Codex CLI `0.153.4`.
`AGENTIC_CODEX_MODEL` defaults to `gpt-5.6-sol`.
`AGENTIC_RUNNER` defaults to `neurodesk-syd-arcrunner-neurodesktop` and
`AGENTIC_CODEX_HOME` defaults to `/var/lib/neurodesktop-codex`. The persistent
directory must survive runner replacement. The runner uses a Docker-in-Docker
sidecar, so checkout, temporary, and authentication paths must be visible at
identical absolute mount locations to both runner and daemon. Administrators
provision the PVC and mounts, then complete the dedicated account's device
login.

The workflows need no OpenAI API key and never transport the Codex login as a
GitHub secret or artifact. Removing gh-aw is necessary because its built-in
Codex engine configures API-key authentication rather than adopting the local
subscription session. Conventional YAML and repository-owned scripts now own
event routing, bounds, validation, and publication.

OpenAI documents local subscription login and saved-auth reuse by `codex exec`,
but its account-auth CI recipe explicitly says not to use it for public or
open-source repositories. This setup does not comply with that guidance. The
[authentication research](../designs/agentic-codex-auth-research.md) records the
exact sources.

Docker separates agent execution from unrelated host resources and the GitHub
publisher. The Codex permissions profile supplies credential read denial for
model tools. The outer auth container uses unconfined seccomp and AppArmor
profiles to permit the inner Bubblewrap sandbox, while retaining capability
dropping and `no-new-privileges`. The automatic probe verifies the inner
boundary rather than assuming that an auth mount is inherently private.

The controller scans the patch and result for exact token values before
artifact publication. Codex's own process still needs the account session,
and the trusted runner and controller can read it. The probe and scan reduce
specific exposures; they do not prove arbitrary code or transformed output
cannot disclose credentials.

The final image passed the checkout unit suite and the sandbox probe with
dummy credentials and no model request. Changed workflows passed Actionlint.
Production runner mounts, login, inference, and live PR publication still
need an administrator's canary.

The Codex container also holds a file lock on its persistent authentication
volume and an internal 5,350-second timeout. Those controls survive loss of
the runner process; the Actions cleanup step removes any remaining task
container when the job can still finish its steps.
