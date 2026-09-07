---
title: Agentic maintenance workflows
description: Run subscription-authenticated Codex on the existing self-hosted runner and review its proposed fixes
parent: index.md
status: current
last-reviewed: "2026-09-07"
---

# Agentic maintenance workflows

Neurodesktop uses the existing self-hosted Actions runner to run Codex with a
dedicated ChatGPT subscription login. New issues lead to investigation and a
proposed fix in a draft pull request. Scheduled jobs cover five maintenance
categories each week. GitHub-hosted jobs prepare tasks and publish changes.

This page covers setup and operation. See
[Agentic CI workflows](architecture/agentic-workflows.md) for the trust
boundaries and [the redesign record](designs/agentic-subscription-redesign.md)
for the reasons behind the change.

## Configure the worker

The repository contains the workflow implementation. The runner still needs
a persistent authentication directory and a working Docker daemon before the
first live run. Repository changes do not provision these resources.

| Repository variable | Default | Purpose |
| --- | --- | --- |
| `AGENTIC_ENABLED` | unset | Set to `true` after runner readiness checks to enable issue, maintenance, and review execution |
| `AGENTIC_RUNNER` | `neurodesk-syd-arcrunner-neurodesktop` | Runner label for the Codex job |
| `AGENTIC_CODEX_HOME` | `/var/lib/neurodesktop-codex` | Persistent dedicated Codex authentication directory on that runner |
| `AGENTIC_CODEX_MODEL` | `gpt-5.6-sol` | Explicit model for the agent |

The directory must survive replacement of the runner process or pod. The
existing Actions Runner Controller setup uses a Docker-in-Docker sidecar.
Bind-mount sources must exist at identical absolute paths in both the runner
container and the Docker daemon's container. That includes the checkout,
`RUNNER_TEMP`, and the persistent authentication directory. A path visible
only in the runner container cannot be mounted by the sidecar's Docker daemon.

An administrator must provide the persistent volume claim and mount the
credential directory into both containers. Match ownership to the account
that launches the worker. Do not use a personal home directory containing
unrelated credentials. The repository does not configure the runner's PVC or
Kubernetes mounts.

Each worker run builds `neurodesktop-agentic:codex-0.153.4` from the trusted
[`config/agentic/Dockerfile`](../config/agentic/Dockerfile), reusing the runner's
Docker cache. The image pins Codex CLI `0.153.4`. Verify Docker can build and
run that image on the selected runner. The first build needs outbound access
to its package and base-image sources.

On the trusted runner, build the image and create the private directory as
an administrator. Run these commands as the account that runs Actions jobs:

```bash
docker build --tag neurodesktop-agentic:codex-0.153.4 \
  --build-arg WORKER_UID="$(id -u)" --build-arg WORKER_GID="$(id -g)" \
  config/agentic
sudo install -d -m 700 -o "$(id -u)" -g "$(id -g)" /var/lib/neurodesktop-codex
docker run --rm -it --user "$(id -u):$(id -g)" \
  --env CODEX_HOME=/codex \
  --mount type=bind,src=/var/lib/neurodesktop-codex,dst=/codex \
  neurodesktop-agentic:codex-0.153.4 \
  codex -c 'cli_auth_credentials_store="file"' login --device-auth
```

Complete the device login in your browser. Use the configured path if
`AGENTIC_CODEX_HOME` differs from the default. If the directory already exists,
check that it belongs to the runner account before changing its ownership.
Do not replace a healthy existing authentication file. The image's pinned CLI
creates file-backed credentials in the same directory used by normal jobs.
The build arguments create the runner's UID/GID entry inside the image, which
tools such as `ssh-keygen` require.
If device login is unavailable, follow OpenAI's headless-login alternatives
linked from the [authentication research](designs/agentic-codex-auth-research.md).

After building the image, run the credential-free sandbox check:

```bash
python3 .github/scripts/check_agentic_sandbox.py
```

Use `--sudo` only when your local Docker installation requires `sudo -n`.
The probe uses dummy credentials and makes no model call. It verifies stdin,
checkout writes, denied credential/process/output reads, protected writes,
and denied tool network access. It also checks that temporary bind mounts
are visible to Docker. Every worker run executes this probe before Codex.
The administrator must still verify the real persistent auth mount.

The workflows use no `OPENAI_API_KEY` or `CODEX_API_KEY`. Do not copy the login
file into GitHub secrets, caches, logs, or artifacts. Normal runs preserve
Codex's refreshed credentials. A global Actions concurrency group queues
workers, and host and container file locks prevent simultaneous use of the
authentication directory. The container retains its lock if the runner
process disappears and stops Codex after 5,350 seconds. Do not share that
login with another uncoordinated worker.

OpenAI documents subscription login and saved-auth reuse in `codex exec`.
Its account-authentication CI guide explicitly says not to use this pattern
for public/open-source repositories. This public repository's subscription
worker does not comply with that guidance; a private runner does not erase
that caveat. See the exact sources and implications in
[Codex authentication research](designs/agentic-codex-auth-research.md).

## Verify setup with one issue

After configuring the runner and completing login, manually run **Agentic
runner readiness**. It provisions the four agent labels, then checks the real Docker mounts, sandbox, private file
permissions, and matching authentication storage in the runner and daemon.
It does not call a model or prove the saved session is still valid.

Set the repository variable `AGENTIC_ENABLED=true`, then manually dispatch
the issue workflow for a small existing bug. Verify that
preparation completes on a hosted runner, the Codex job acquires the configured
self-hosted runner, and validation runs in a separate container.

A successful fix produces a draft PR with the issue link, the concrete change,
and checkout test evidence. Required image validation that could not run must
remain explicit in the PR. Confirm the resulting branch and PR before enabling
unattended operation. A completed agent process without a published PR is not
proof that publication works.

The final worker image passed all 568 checkout unit tests and the Docker
sandbox probe passed locally with dummy credentials. Actionlint passed the
changed workflows. No model request or live subscription login was made.
The production runner's persistent mounts, account access, and complete
issue-to-PR canary still require administrator verification.

Until that variable is set, failure reporting still creates issues but does
not dispatch repair or record a dispatch marker. After activation, manually
dispatch repair for any earlier failure issue that still needs attention.
Clear the variable to stop new work while repairing runner
configuration. Existing running jobs must be canceled separately.

## Issue and failure handling

[`agentic-issue.yml`](../.github/workflows/agentic-issue.yml) handles incoming
issues and delegates to the reusable worker. It skips issues labeled
`agentic-operations` or `agentic-ignore`. Manual dispatch accepts an issue
number. Issues from repository owners, organization members, and collaborators
start automatically. For an external reporter's issue, a maintainer must add
`agentic-approved` or manually dispatch repair. Adding other labels does not
start work. This admission check limits public consumption of the subscription;
issue contents remain untrusted even after approval. Failure reports from fork branches
also need approval or manual dispatch, so fork CI cannot bypass issue admission.
A confirmed repository defect leads to implementation and validation in the
same task. The agent must not
stop at a diagnosis when it can produce a fix. Insufficient evidence,
external-service failures, and requests with no safe code change produce an
explicit blocker or no-change result instead of an empty PR.

The completed-workflow reporter watches CI, deployment, and agent jobs. It
creates or updates a failure issue with a link to the run. Product failures
enter normal issue handling. Agent authentication, runner, and other
operational failures are marked as operational so their reports do not launch
a recursive chain of repair jobs.

Issue branches use `agentic/issue-N-RUNID`; maintenance branches use
`agentic/maintenance-category-YYYY-Www`. Preparation skips an issue or category that already has an owned open PR.
Retrying a failed publisher reuses its original prepared task and artifacts. It must not create another PR for
the same issue. Publication verifies the current branch state before writing
and never merges changes.

## Weekly maintenance

[`agentic-maintenance.yml`](../.github/workflows/agentic-maintenance.yml)
runs at 04:23 UTC on weekdays, selecting one category by UTC weekday. Each
category therefore runs weekly,
rather than once every several weeks. Manual dispatch supports a focused
canary or a rerun.

| UTC weekday | Category | Expected work |
| --- | --- | --- |
| Monday | `updates` | Check upstream releases and implement one justified compatible update |
| Tuesday | `security` | Investigate one concrete vulnerability, stale exception, or hardening opportunity |
| Wednesday | `dead-code` | Prove code, configuration, or a dependency has no remaining callers before removing it |
| Thursday | `test-coverage` | Add a behavioral test for an important unprotected contract |
| Friday | `refactoring` | Improve one confusing structure or domain concept with demonstrated benefit |

Each run makes one coherent change. It checks for an existing PR in its
category before starting another. Findings are implemented when validation
is feasible. A useful no-change result states what was examined and why no
change is justified. A blocked result names the missing evidence or check.
There is no separate report-only package radar that stops before an update PR.

## Review and validation

CodeRabbit can review generated drafts. Its review activity triggers
[`agentic-review.yml`](../.github/workflows/agentic-review.yml), which collects
feedback for the existing PR. The worker checks each finding against the
current head, applies accepted fixes together, validates them, and updates the
same branch. A completed CodeRabbit review must name the current head SHA.
The publisher records each processed head, including no-change and blocked
outcomes, so repeated summary edits do not start another run for that head.
At most three automatic follow-up commits are allowed per PR.
Each follow-up comment identifies the published revision and includes its
independent validation evidence and pending checks. Later feedback remains for
human review. The workflow never marks a draft
ready or merges it.

All Actions checkouts disable persisted Git credentials. The hosted publisher
passes authentication to Git through process environment configuration.

The agent container has subscription credentials but no GitHub publishing
token. Validation runs in another container with neither authentication nor
network access. Hosted publication consumes `task.json`, `result.json`, `change.patch`, and
`validation.txt`. Raw Codex stdout and account state are not uploaded. Container
isolation separates the host from the agent, while the named Codex `worker`
permissions profile blocks model tools from reading `/codex`, `/proc`, and
`/output`. Tools can edit the checkout, but cannot write its Git metadata or
use the network. Codex hooks and multi-agent execution are disabled. The
pre-run sandbox probe checks these restrictions against the actual image.

The outer authentication container permits unconfined seccomp and AppArmor
profiles so Codex can create the nested Bubblewrap sandbox. It still drops
all Linux capabilities and forbids privilege escalation. These exceptions
make the tested inner sandbox necessary; Docker alone does not conceal auth.
Codex itself can access the login to authenticate and refresh it. An exact
credential-value scan rejects the proposed patch or result if either contains
known pre-run or refreshed token values. That scan does not detect every
possible transformed disclosure and is not a replacement for the sandbox.

Before independent testing, the controller resets the checkout and reapplies
exactly `change.patch`, removing ignored or untracked state left by the agent.
Before the model starts, the controller snapshots the trusted default-branch
revision's tests, including for review tasks whose candidate is a PR head.
The validator mounts that snapshot read-only and runs it against the patched
source using trusted pytest settings, then runs the candidate's own suite.
A candidate `conftest.py` cannot silence the preserved suite. Imported candidate
code still runs inside pytest, so these results are not a security proof.
Subprocess stdout and stderr are each capped at 8 MiB; overflow or timeout
terminates the command instead of retaining unbounded output on the host.
The first line of `validation.txt` records whether validation passed and the
patch's SHA-256. The publisher checks that proof against the submitted patch.
Failed validation retains a bounded test-log tail for diagnosis.

The full checkout unit suite must pass before a draft PR is published. Container-dependent
checks follow [Testing](testing.md). A draft can state that image checks are
pending, but must not claim they passed. Failing checkout tests, invalid
patches, credential or Git-metadata paths, and stale PR heads stop publication.
Patches are bounded to 50 files and 2 MiB, and new symlinks or submodules need
manual changes. Proposed workflow edits cannot replace the trusted controller
artifact used by their current run.

For changes to these workflows or their controller scripts, run:

```bash
pytest tests/unit/test_agentic_*.py tests/unit/test_report_workflow_failure.py
```

The PR API uses the normal `GITHUB_TOKEN`, so PRs remain authored by
`github-actions[bot]`. Two optional GitHub repository secrets have distinct
roles. They are GitHub credentials, not OpenAI model API keys.

| Secret | Repository permissions | Hosted publisher use |
| --- | --- | --- |
| `AGENTIC_PUSH_TOKEN` | Contents: write; Workflows: write | Push initial and review patches, including proposed workflow changes |
| `AGENTIC_CI_TOKEN` | Contents: write | Push an empty commit after publication to trigger ordinary PR CI |

Scope a fine-grained token to this repository and the listed permissions.
Never pass either token into the Codex or validation container. The default
push uses `GITHUB_TOKEN` when no push token is configured.

When a proposal changes `.github/workflows`, the publisher first creates its
branch at the trusted base with `GITHUB_TOKEN`. It then pushes the candidate
with `AGENTIC_PUSH_TOKEN` and a `[skip ci]` commit message. The CI helper checks
the cumulative workflow diff, including earlier review commits, and refuses
the automatic empty commit while workflow changes remain. A maintainer must
review those changes before running their checks. This prevents a proposed
workflow from immediately executing through the more capable push token.

For ordinary code changes, `AGENTIC_CI_TOKEN` triggers CI without changing the
PR author. With only `GITHUB_TOKEN`, PR CI can wait for a maintainer to select
**Approve workflows to run**. GitHub suppresses other token-generated events
such as ordinary pushes.
[GitHub workflow triggering](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)

The workflows are conventional YAML. There are no gh-aw source Markdown files,
compiled locks, or custom gh-aw timeout detector to regenerate.

## Recover a stopped run

For authentication failures, sign in again on the runner using its dedicated
Codex home. Do not reset that directory during a healthy run. Subscription
limits are shared with other work on the account, so exhausted capacity needs
the allowance to reset or the account's available credits to change.

For a queued job, check the runner label, runner availability, and the job
currently holding the global worker slot. Check persistent storage and the
host lock if the runner is available but cannot acquire authentication state.

For publication failures, choose **Re-run failed jobs** after correcting the
cause. This preserves the prepared task and candidate artifacts for the
publisher's reconciliation. **Re-run all jobs** prepares a new task and skips
an issue that already has an open agent PR, so it cannot repair an incomplete
publication after that PR was created. Artifacts expire after seven days.

If GitHub rejects a workflow-file push, configure `AGENTIC_PUSH_TOKEN` with
repository Contents and Workflows write permissions, then rerun the failed
publisher. `AGENTIC_CI_TOKEN` does not authenticate that initial patch push.
Keep the candidate artifacts while resolving permissions. Workflow-change
PRs still require maintainer review before their checks run.

For validation failures, read the result or issue comment and correct the
candidate before submitting it again. A blocked result does not publish the
patch. Keep existing task branches and PRs so retries can reconcile them.
An unconditional cleanup step removes the recorded agent or validator
container after failure or cancellation. If the runner itself disappeared,
check its Docker daemon for the recorded container before restarting work.
Do not bypass validation or start a competing worker to clear the queue.
