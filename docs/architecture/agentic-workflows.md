---
title: Agentic CI workflows
description: Failure reporting, issue repair, scheduled maintenance, and subscription-authenticated Codex execution through conventional Actions
parent: ../architecture.md
status: current
last-reviewed: "2026-10-23"
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
subscription execution; owners, members, and collaborators enter automatically.
The reporter dispatches automatic repair only when a failed run belongs to the
default branch. It still records feature-branch failures and related pull
requests, but the issue worker must not edit the default branch for those
failures. Agent operational failures are classified separately so they do not
recursively launch agents to repair their own unavailable authentication or runner.

The reporter groups unresolved failures only when the workflow ID, head
repository, exact branch or PR scope, source SHA, and complete failure bundle
match. Matrix job names and failed step names remain part of the identity.
Complete Trivy finding tables and pytest failure sections supply diagnostic
identity. Successful setup output, runner IDs, and run-specific image tags do
not define a failure. Missing, oversized, incomplete, or unrecognized diagnostics
remain specific to the run and attempt. A different SHA or failure bundle can
create a separate incident even when a human later identifies a shared cause.

Bot-authored first-line run markers and reserved versioned records establish
ownership. Each episode retains its origin and failure observations, with
per-attempt comments linking every affected run. Existing run, attempt, and
dispatch receipts remain valid. A particular legacy report can be adopted when
its exact attempts reconstruct the same complete identity. Manually closed
reports do not establish recovery and are not automatically reopened. Exact
run ownership, including legacy reports, takes precedence over a matching open
failure signature. Delivery order cannot move a recorded attempt to a newer
episode or invalidate its dispatch receipt or existing owned proposal.

A successful completion closes an incident only when one exact attempt proves
that every failed job and step passed. Scope must match, execution must start
after every covered failure completed, and the revision must be identical or a
verified descendant. Missing, skipped, cancelled, or ambiguous checks leave the
incident open. The reporter saves recovery proof before closing, so a retry can
finish an interrupted close. A later failure starts a new episode. Replayed
historical attempts retain their original owner.

For delayed delivery, the reporter queries one page of at most 100 completed
runs for the same workflow and branch since the relevant failure. It also
queries known run IDs because reruns retain their original creation dates.
At most ten known runs and ten exact attempts are examined. Missing pages,
metadata, or a reached bound leave an explicit incomplete-history notice and
cannot prove recovery. Later completion events and redelivery can reconsider
an open report. Reporter runs queue by workflow ID with cancellation disabled.

An owned open PR suppresses repair only with a matching first-line canonical
issue marker and, when present, the exact episode binding. User-authored PRs,
foreign heads, quoted markers, and bindings to old episodes do not count.
The reporter records each occurrence before checking PR coverage or dispatch
receipts. Failed dispatch remains retryable. A crash after GitHub accepts a
dispatch can repeat delivery; whole-issue worker concurrency and publication
rechecks prevent duplicate proposals.

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
control code is separate from the checkout the model can edit. Actions checkout
steps always use the default branch. The controller fetches validated full
candidate SHAs as Git objects and checks out the candidate only before isolated
execution; it never executes candidate scripts in the hosted publisher.
Failure evidence samples the root and latest exact attempts, with an explicit
omitted-run count. Every failed job is queried, including jobs after an
unavailable log. Run, job, and step metadata use allowlists. The shared
`failure_evidence` budget is 40,000 ASCII-escaped JSON characters, including
metadata and notices. Each job contains separate diagnostic sections and a
tail for every failed step. Step attribution uses exact CLI step prefixes or
unique job-step time ranges. Lines without unique attribution, including
overlapping boundary seconds or missing timestamps without a known CLI prefix,
remain explicitly unattributed. Error text never determines a step name.
Known successful-step output is omitted with a line count. Unavailable logs
remain named on the job and each failed step.

Job and failed-step identities receive space before long logs. The packer
shares the remaining budget across step slots, then across separate diagnostic
sections within each slot. Large sections preserve their beginning and end,
alongside each step's tail. Pytest assertions, failed-test summaries, Trivy
finding rows, and substantive errors precede cleanup tails. Omitted diagnostic
sections and shortened tails have explicit counts or notices. If the manifest
itself cannot fit,
the record names the exact omitted job and step counts and links the source
attempt. Generic context trimming does not trim packed failure evidence again.

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
default-branch test suite was snapshotted before the model ran and is mounted
read-only. Review tasks preserve that trusted validation revision separately
from the PR head they edit.
It exercises the patched sources before the candidate suite runs, using
trusted pytest configuration. Both suites run with the patched checkout as
the working directory, so relative source paths resolve there while frozen
tests and their configuration remain independent of candidate pytest controls.
Captured output is limited to 8 MiB per stream;
overflow and timeouts terminate the command. The
validation record includes the patch hash and retains bounded failure logs.
Both the frozen and candidate suites retain their own exit status, elapsed
time, and bounded output. A failure in the first suite does not prevent the
second suite from running or disappear behind its output. Missing tools,
test failures, and interrupted validation remain failures; a failed unchanged
checkout is not automatically classified as an environment problem.
Each suite has an 840-second deadline, an 8 MiB output limit, and an 8 KiB
retained output tail. These limits leave time for both reports beneath the
controller's 1,800-second validation deadline. A timeout or output overflow
terminates that suite's process group and still fails validation.
When a suite's leader exits, the validator kills remaining processes in its
group and drains buffered output for at most one second. It retains the leader's
exit status so a child holding the output pipe cannot turn a completed suite
into a timeout.
This separates the agent's own test claims from the checks that authorize
publication.

Hosted publication reads `task.json`, `result.json`, `change.patch`, and
`validation.txt`, then writes a task branch and opens or updates its draft PR.
Raw Codex stdout and authentication state are excluded from artifacts. The
publisher owns GitHub write credentials. Patches are bounded and cannot add
credential paths, Git metadata, symlinks, or submodules. Proposed workflow
changes do not replace the current run's separately prepared controller.
The publisher rejects a stale target rather than overwriting a newer change.
A review whose PR branch moved during the run ends without pushing or
commenting; CodeRabbit's summary of the newer push starts its review.
Rerunning a failed publisher reconciles the same task branch and PR. Rerunning
preparation skips an issue with an existing owned open PR. That distinction
matters when publication failed after creating the PR. An unconditional
cleanup step removes the recorded execution container after cancellation or
failure.

Workflow-incident proposals use `Tracks` links. The publisher rechecks the
trusted incident binding, open state, eligibility labels, and owned PRs before
publication. It neutralizes generated closing references in the title, body,
and commit message, including review follow-ups. Each closing keyword must
directly precede a reference to that incident, with an optional colon.
References to other issues or repositories retain their own closing keywords,
including mixed lists in one sentence. Diagnostic improvements do
not close the original incident. Reused proposals and review follow-ups also
reconcile the saved PR title and body through PATCH, preserving task markers,
episode bindings, and other content. The publisher reads current ownership and
incident eligibility before changing the saved text. Retries after an accepted
PATCH read the saved result and do not repeat an already completed change.
Pending validation names the original checks;
only the reporter's verified recovery establishes success. Ordinary human
issue proposals retain their `Fixes` footer.

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

Each worker run builds `neurodesktop-agentic:codex-0.162.1` from the trusted
[`config/agentic/Dockerfile`](../../config/agentic/Dockerfile), using Docker's
layer cache. The controller's `stage` command assembles a narrow build context
from that trusted checkout, including the JavaScript lint package manifest and
lockfile. The preparation artifact carries these inputs separately from the
candidate checkout. `WORKER_UID` and `WORKER_GID` build arguments create the runner's
identity inside the image. The image pins Codex CLI `0.162.1`.

The trusted image build also installs the unit-test workflow's Python, system,
and pinned DOM dependencies, including JupyterHub, NIfTI support, and ACL tools.
It includes the C compiler, C and X11 development headers, Xvfb, and `xrandr`
needed by the native helper tests. Locked ESLint dependencies are installed
with lifecycle scripts disabled under `/opt/javascript-lint`. The trusted
validator's `--prepare-workspace` command links that image-owned dependency tree
at the checkout's expected `tools/javascript-lint/node_modules` path before
Codex starts. After `git clean` and patch reconstruction, the offline validator
recreates the ignored link for both test suites. Neither setup installs packages
selected by candidate manifests or follows redirected checkout directories.
The worker uses Python 3.15 and also exposes it at `/usr/bin/python3` for launcher
shebangs. Checkout CI uses Python 3.15; the offline canary validates the worker's
interpreter separately.
`NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES` points both validation suites to the
image-owned DOM packages. Dependency downloads happen during the image build;
validation remains offline and credential-free.

The `canary` command validates a disposable unchanged clone through the same
credential-free Docker path and frozen-suite runner used for candidates. It
does not modify the caller's checkout or make a model request. Both the manual
readiness workflow and the unit-test workflow build the staged worker and run
this check. The required `pytest tests/unit` context gates both the
checkout unit job and the offline canary, so a failing canary blocks merge.
The canary result applies to the tested source revision and worker image;
a successful login-storage check alone does not establish test readiness.
Both workflows run unconditional cleanup through the staged controller before
uploading canary evidence, including after cancellation or failure.

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
