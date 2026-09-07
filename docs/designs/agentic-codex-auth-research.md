---
title: Codex authentication for agentic workflows
description: Official authentication support, subscription constraints, and runner choices for the September 2026 workflow redesign
parent: index.md
status: research
last-reviewed: "2026-09-05"
---

# Codex authentication for agentic workflows

This record supports the September 2026 redesign. It describes the checkout
before that redesign and the upstream documentation retrieved on 2026-09-05.
See [Design records](index.md) and the earlier
[workflow reliability remediation](agentic-workflow-reliability-remediation.md)
for the repository's previous decisions.

## Subscription requirement and selected approach

The maintainer requires the existing Codex subscription and has no API access.
The selected design runs `codex exec` using a dedicated local ChatGPT login
on the existing self-hosted Actions runner. Conventional Actions jobs prepare
tasks, schedule maintenance, and publish validated changes as pull requests.
The reusable worker isolates Codex in Docker and serializes access to its
persistent authentication directory.

This is our integration built on documented CLI behavior. It requires no
OpenAI API key and places no Codex account credentials in GitHub secrets or
artifacts. It replaces gh-aw for agent execution because the built-in gh-aw
Codex engine does not adopt a runner's local subscription login.

The recommended initial model is `gpt-5.6-sol`, with high reasoning effort for
repository repairs. OpenAI lists Sol among the recommended Codex models for
complex coding. Its API reference confirms the explicit identifier, Responses
support, function calling, and `high` effort. This is our workload choice,
not a claim that every account already has access.
[Codex models](https://learn.chatgpt.com/docs/models) and
[GPT-5.6 Sol API reference](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

OpenAI documents ChatGPT sign-in as subscription access for the local CLI,
including `codex login --device-auth` on headless machines. The CLI caches
credentials and refreshes sessions during use.
[OpenAI authentication documentation](https://learn.chatgpt.com/docs/auth)

The non-interactive documentation states that `codex exec` reuses saved CLI
authentication. It supports JSON events and a schema for the final response,
which a controller can consume. Together these establish the technical path
from local subscription login to a scripted worker.
[OpenAI non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)

OpenAI also documents unattended tasks against local Git projects and worktrees
in the desktop app, including tasks that find and fix bugs. That is an official
local automation option, but the app's scheduler does not itself provide the
repository's required Actions event handling and deterministic PR publication.
[OpenAI scheduled tasks](https://learn.chatgpt.com/docs/automations)

## Exact limit of the official guidance

OpenAI documents an advanced account-authentication workflow for trusted private
automation. Its explicit restriction is: "Do not use this workflow for public
or open-source repositories." Neurodesktop is
[a public repository](https://github.com/neurodesk/neurodesktop). Running the
worker on a private machine does not remove that explicit repository scope.
[OpenAI account authentication in CI](https://learn.chatgpt.com/docs/auth/ci-cd-auth)

The same guide explains why credential state needs one serialized owner and
must retain refreshes. Replacing a refreshed `auth.json` from an old seed breaks
that state. Revoked or expired sessions can still require fresh login.
[OpenAI account authentication in CI](https://learn.chatgpt.com/docs/auth/ci-cd-auth)

This implementation meets the maintainer's subscription requirement through
an integration outside that documented recommendation. It uses supported CLI
authentication primitives, but is not an officially endorsed account-auth CI
setup for this public repository. Keeping the credentials on the self-hosted
machine reduces exposure without changing the scope of the guidance. The
restriction is not evidence that the CLI technically refuses public source.

OpenAI recommends API keys for programmatic CI and advises against exposing
Codex execution in public or untrusted environments. That recommendation does
not meet the maintainer's subscription-only requirement. It remains relevant
to the trust boundary of the private worker.
[OpenAI authentication documentation](https://learn.chatgpt.com/docs/auth)

Subscription capacity is also finite. Local and cloud work share the plan's
allowance, and weekly limits may apply. Automation would compete with the
maintainer's interactive work. This is a capacity tradeoff, not a guarantee of
free unlimited inference. [OpenAI pricing and usage limits](https://learn.chatgpt.com/docs/pricing)

## What the existing compiler actually does

The inspected lock files identify gh-aw `v0.80.9` and Codex CLI `0.141.0`.
The original [shared model map](https://github.com/neurodesk/neurodesktop/blob/a362550cf9cc50958952e1d936e66441b39b04ff/.github/workflows/shared/agentic-models.md)
selects GLM 5.2, Kimi 2.7, and MiniMax through the `neurodesk` alias. The
original [issue fixer](https://github.com/neurodesk/neurodesktop/blob/a362550cf9cc50958952e1d936e66441b39b04ff/.github/workflows/issue-fixer.md) sets
`OPENAI_BASE_URL` to `https://llm.neurodesk.org/openai`.

The pinned compiler's `GetSecretValidationStep` validates `CODEX_API_KEY` or
`OPENAI_API_KEY`. Its agent environment gives `CODEX_API_KEY` precedence and
sets `CODEX_HOME` to the temporary MCP configuration directory. The compiled
workflow writes an `openai-proxy` provider using `env_key = "OPENAI_API_KEY"`.
It does not automatically load the runner account's usual Codex home.
[Pinned Codex engine source](https://github.com/github/gh-aw/blob/v0.80.9/pkg/workflow/codex_engine.go)
and [compiled issue fixer](https://github.com/neurodesk/neurodesktop/blob/a362550cf9cc50958952e1d936e66441b39b04ff/.github/workflows/issue-fixer.lock.yml).

Therefore, changing only `runs-on` to `self-hosted` cannot switch these workflows
to subscription authentication. That conclusion follows from the compiler's
credential validation, temporary home, and generated provider configuration.
A custom authentication adapter would be a separate integration to maintain.
The reusable Actions worker instead invokes Codex directly. Separate hosted
jobs own preparation and publication.

Current shared-import documentation supports common `pre-agent-steps` and
network settings, but only `engine.mcp` within engine settings. The importing
workflow owns the engine identity. Common wrapper installation can therefore
move into a shared import while each workflow declares its engine and model.
Compilation against the pinned release must verify the final merge behavior.
[gh-aw imports](https://github.github.com/gh-aw/reference/imports/)

Current gh-aw authentication documentation still lists the two API-key secrets
for Codex. The Codex guide's phrase "OpenAI subscription" must be read alongside
that explicit API-key requirement. It does not establish support for a ChatGPT
subscription credential. [gh-aw authentication](https://github.github.com/gh-aw/reference/auth/)
and [gh-aw Codex engine](https://github.github.com/gh-aw/engines/codex/).

## Direct CLI worker design

Hosted preparation gathers issue, review, or maintenance context. The
self-hosted job runs the agent in a disposable container against a clean
checkout. Issue bodies, comments, and logs are untrusted task text. The
controller passes them as JSON evidence in the prompt rather than interpolating
them into shell commands or configuration. The prompt instructs Codex to treat
that text as evidence, and sandbox permissions restrict its tools. These
controls reduce prompt-injection risk; they do not guarantee that the model
will ignore malicious instructions. The agent gets local Codex authentication,
but no GitHub publication token, SSH keys, Docker socket, or unrelated host
files.

The controller reconstructs the exact patch in a clean checkout. A second
container validates it without Codex credentials or network access. Hosted
publication checks that validation records the same patch hash, then creates
or updates a task branch and draft pull request. The authenticated
agent's claim that tests passed is not sufficient to publish.

Container isolation protects unrelated host files and separates the GitHub
publisher credential. A named Codex permissions profile denies model tools
access to `/codex`, `/proc`, and `/output`, as well as tool network access.
Hooks are disabled. The outer container permits the nested Bubblewrap sandbox
through unconfined seccomp and AppArmor settings while dropping capabilities
and preventing privilege escalation. Docker alone does not hide the auth
mount. A credential-free probe checks the actual sandbox before each run.
The controller also rejects exact credential values in proposed artifacts.
Codex itself and trusted controller code retain access to account state.

One worker at a time owns the persistent authentication directory, enforced
by Actions concurrency and a host file lock. The maintainer signs in directly
on that machine. Normal jobs never export the login file to GitHub or replace
refreshed credentials. Failed jobs remain visible in Actions and operational
issues. Failed publication retries preserve the task artifact and reconcile
the existing branch. A complete rerun skips an existing owned PR at preparation.
An unconditional cleanup step removes recorded containers after cancellation.
Optional GitHub push and CI credentials remain in hosted publication; they
are separate from model authentication and do not require OpenAI API access.

The worker's schedule covers updates, security, dead code, coverage, and
structural refactoring. A maintenance job can produce a PR, an evidenced
no-change result, or a visible blocker. Human review remains the merge step.
These are repository design requirements, not behaviors provided by Codex
authentication itself.

## Alternatives outside the selected requirement

Current gh-aw documentation also describes Codex using GitHub Copilot inference:
`engine: codex` with a `copilot/` model. Authentication uses organization billing
through `copilot-requests: write` or a `COPILOT_GITHUB_TOKEN` with Copilot Requests
access. This uses GitHub billing, not the maintainer's ChatGPT subscription.
The existing `v0.80.9` compiler must be checked before adopting this newer
configuration. [gh-aw Codex engine](https://github.github.com/gh-aw/engines/codex/)

OpenAI's own `openai/codex-action` installs Codex and runs `codex exec`. Its
documented CI setup uses an API key and provides runner privilege controls.
Replacing gh-aw with this action would make Neurodesktop responsible for the
event routing, publication controls, and isolation now provided by gh-aw.
Its documented API-key setup does not satisfy this subscription requirement.
[OpenAI GitHub Action](https://learn.chatgpt.com/docs/github-action)

Enterprise workspaces have additional automation access-token and workload
identity options. Their availability depends on workspace administration and
entitlements. They are not evidence that a personal subscription can replace
the existing gh-aw API-key secret.
[OpenAI authentication documentation](https://learn.chatgpt.com/docs/auth)

## Existing self-hosted Actions runner

gh-aw supports Linux self-hosted runners with Docker and Node.js. Its agent,
gateway, and proxies still run in containers. `runs-on` selects the main agent
runner. Framework jobs have a separate `runs-on-slim` setting, and safe outputs
can select their own runner. Container tests may benefit from a dedicated host
with enough disk and cached image layers, independently of model billing.
[gh-aw self-hosted runner reference](https://github.github.com/gh-aw/reference/self-hosted-runners/)

Those gh-aw settings select infrastructure, not subscription authentication.
The chosen conventional workflow runs direct Codex on the runner selected by
`AGENTIC_RUNNER`, defaulting to `neurodesk-syd-arcrunner-neurodesktop`. Its
persistent `AGENTIC_CODEX_HOME` defaults to `/var/lib/neurodesktop-codex`.
The ARC Docker-in-Docker sidecar must see the same absolute checkout,
temporary, and authentication mount paths as the runner. An administrator
must provision the persistent volume and mounts.
For installation and trust requirements, see
[Agentic maintenance workflows](../agentic-maintenance.md).

## Evidence limits

This investigation read the repository's generated workflows, the pinned gh-aw
Codex engine source through GitHub's API, and the linked official pages. The
resulting worker image was built and its Docker sandbox probe passed locally
with dummy credentials. No authentication secrets were inspected, no model
request was made, and the production runner was not provisioned. Subscription
entitlement, persistent mounts, and live model access require a canary after
administrator configuration.
