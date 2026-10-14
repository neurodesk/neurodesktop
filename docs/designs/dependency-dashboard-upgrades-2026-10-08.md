---
title: Dependency dashboard upgrades, 8 October 2026
description: Claude-reviewed upgrade selection, compatibility holds, and validation gates for issue 1042
parent: index.md
status: implemented
last-reviewed: "2026-10-08"
---

# Dependency dashboard upgrades, 8 October 2026

[Issue 1042](https://github.com/neurodesk/neurodesktop/issues/1042) is a rolling
Renovate dashboard. This record explains the upgrade selection from its
8 October snapshot. It does not close the dashboard or approve every pending
Renovate branch. The [Dockerfile](../../Dockerfile) owns current image versions.

Claude Opus 5.5 reviewed the repository and proposed selection before edits.
The review recommended small build and utility updates, with installed-image
validation, and separate work for major migrations and coupled agent runtimes.
It made npm and Apptainer conditional on dependency checks and runtime tests.
The module requirements and node-tar range checks passed before those edits.
The 62 focused checkout checks also passed, including the Apptainer crypto check.
The final Claude review approved the code subject to the remaining image and CI gates.
The final selection preserves every hold in
[Renovate](../../renovate.json) and the
[version audit catalog](../../scripts/audit_image_versions.py).

## Selected updates

| Dependency | Previous | Selected | Required checks |
| --- | --- | --- | --- |
| Apptainer | 1.5.3 | 1.5.4 | Source build, unchanged Go/module overrides, nested scientific containers on amd64 and native arm64 runtime coverage |
| uv | 0.12.17 | 0.12.23 | Lightcone installation and installed ASTRA/Lightcone checks |
| MyST pnpm | 11.27.0 | 11.28.4 | Existing frozen-lockfile rebuild and standalone RISE rendering |
| Hatchling | 1.32.3 | 1.32.4 | Shared launcher/build constraints and fresh isolated launcher and Slurm wheel builds |
| Jakarta EE migration tool | 1.0.12 | 1.0.13 | Guacamole migration, desktop startup, and RDP/VNC checks |
| PyJWT | 2.14.0 | 2.15.1 | Checkout and installed asymmetric/HMAC key-confusion regression tests |
| npm | 12.0.2 | 12.2.0 | Node 24 compatibility, source builds, and installed node-tar checks |
| Dockerfile frontend | 1.25 | 1.27 | Buildx build checks and both image architectures |
| actions/checkout | 6.0.2 | 6.1.0 | Preserve trusted agentic checkouts and run-SHA image checkouts |
| actions/upload-artifact | 7.0.1 | 7.0.2 | Preserve current inputs and artifact contracts |
| actions/download-artifact | 8.0.1 | 8.0.2 | Preserve cross-job artifact IDs and authentication |
| docker/setup-buildx-action | 4.0.0 | 4.4.1 | PR candidate builds on both architectures |
| docker/build-push-action | 7.1.0 | 7.4.0 | Run-specific candidates and unchanged promotion gates |
| docker/login-action | 4.1.0 | 4.6.0 | DockerHub, Quay, and backfill login steps in scheduled or manual workflows |
| cvmfs-contrib/github-action-cvmfs | 5.5 | 5.6 | Node 24 cache action and amd64 scientific acceptance |

The [Apptainer 1.5.4 release](https://github.com/apptainer/apptainer/releases/tag/v1.5.4)
fixes a high-severity local privilege escalation in setuid mode.
The image builds with `--with-suid` but sets `allow setuid = no` by default.
That default already prevents use of the affected setuid mode. The update
protects deployments that explicitly enable it while preserving the default.
Its [go.mod](https://github.com/apptainer/apptainer/blob/v1.5.4/go.mod)
requests Go 1.25.7, crypto 0.51.0, and gRPC 1.81.1.
The existing image overrides, Go 1.27.1, crypto 0.57.0, and gRPC 1.84.0,
remain newer than those requirements.
The source build produced Apptainer 1.5.4 with those overrides.
A harmless non-root probe enabled setuid only inside disposable old and new
image containers, using an empty CDI directory and a nonexistent device.
The published 1.5.3 image reached device lookup. The final rebuilt 1.5.4 image
instead exited with code 255 and rejected `--cdi-dirs` before device lookup.
Both probes used the same setuid configuration. The earlier comparison of
the published image and source-build stage used different defaults and is
superseded by this final-image comparison. With its normal setuid-disabled
configuration, the new image still permits the directory override.
Nested scientific-container acceptance remains a separate required check.

[npm 12.2.0 metadata](https://registry.npmjs.org/npm/12.2.0)
requires Node `^22.22.2 || ^24.15.0 || >=26.0.0` and node-tar `^7.5.22`.
The image retains Node 24 and its patched node-tar 7.5.22.
The rebuild must establish the installed Node version satisfies that range.
The completed candidate contains Node 24.21.0 and npm 12.2.0.

All updated Actions retain immutable commit pins.
[checkout 6.1.0](https://github.com/actions/checkout/releases/tag/v6.1.0)
changes defaults for unsafe `pull_request_target` and `workflow_run` checkouts.
The failure-reporting `workflow_run` and agentic workflows explicitly check out
the trusted default branch. Image workflows retain explicit run-SHA checkouts.
[CVMFS 5.6](https://github.com/cvmfs-contrib/github-action-cvmfs/releases/tag/v5.6)
updates its transitive cache action, whose entry point still uses Node 24.
The existing upload-artifact 4.6.2 and setup actions stay at their current
major versions.

## Deferred dashboard groups

| Group | Reason for separate work |
| --- | --- |
| ASTRA tools 0.2.18 | Synchronize the viewer, both images, Lightcone, and the pinned agent-skills `astra-pins.sh`. A matching agent-skills source commit has not been established. Keep schema 0.0.14 and tools 0.2.17 together. |
| Jupyter AI ACP client 0.3.2 and persona manager 0.2.2 | Metadata and the released ACP logging patch are compatible, but streamed chat and persona restoration still require dedicated installed tests. |
| Jupyter Server Proxy 4.6.0 | The released response-limit patch applies, but T3 subclasses its proxy handler. Authentication and proxy behavior need a dedicated upgrade check. |
| Coding agents and adapters | Codex ACP 1.13.1 requires `@openai/codex ^0.156.1`, which excludes Codex 0.160.0. Retain existing versions until the required T3 and ACP probes justify and document an exception, or a matching adapter is available. |
| T3 0.0.45 | Recheck minified-client patch anchors, disabled provider envelopes, provider initialization, and authenticated desktop pairing against the new release. |
| code-server 4.140.0 | Inventory every nested shell-quote, proxy-addr, and tar package to ensure fixed-path replacement still removes vulnerable copies. Verify editor and private socket behavior. |
| Jupyter base image 2026-10-05 | Compare Python and JupyterLab minors, dependency inventories, extension rebuilds, widgets, and RISE before changing the date tag. |
| Launcher and DOM test dependencies | Keep the current JupyterLab patch versions aligned with the unchanged base image. Evaluate TypeScript 5.9 together with a launcher rebuild. TypeScript 7, jsdom 30, and rimraf 6 are separate major migrations. |
| Tailscale 1.102.5 and cloudflared 2026.10.0 | Refresh both architecture checksums and validate binaries and connection behavior separately. Later upstream releases do not override Renovate's release-age policy. |
| Kasm donor digest | Build and run the Kasm-specific workspace/persistence checks. The unchanged 1.19.0 tag does not require release-tag changes. |
| Agentic worker image | Keep Python 3.12 and pytest 8 parity. Worker Codex/runtime upgrades need the real sandbox probe and frozen-baseline validator in a freshly built worker image. |
| Workflow Python and Ubuntu runners | Python 3.14 and Ubuntu 26 change runtime, FUSE, AppArmor, and cgroup behavior. Preserve the current tested platforms. |
| Major Actions | Verify hosted and self-hosted runner compatibility before changing major versions. |
| Workflow crane and Alpine tools | Registry promotion and runner diagnostic paths need live checks beyond PR unit CI. |
| Published Neurodesktop release examples | Confirm the intended image exists and validate the TinyRange/QEMU consumer before updating a recommended release. |

[Jupyter AI 3.2.0 metadata](https://pypi.org/pypi/jupyter-ai/3.2.0/json)
permits both proposed AI package updates.
[ACP client 0.3.2](https://pypi.org/pypi/jupyter-ai-acp-client/0.3.2/json)
requires persona-manager `>=0.2.2a0` and agent-client-protocol `>=0.11.0,<0.12.0`.
The existing logging patch applied to its released wheel and was idempotent.
The proxy-limit patch also applied and was idempotent on the
[Server Proxy 4.6.0 wheel](https://pypi.org/pypi/jupyter-server-proxy/4.6.0/json).
These observations remove patch-anchor uncertainty, but do not prove end-user
chat or authenticated proxy behavior.

The [Codex ACP dependency range](https://registry.npmjs.org/@agentclientprotocol%2Fcodex-acp/1.13.1)
comes from the published npm package. A pre-1.0 caret range limits the minor
version. The image deliberately uses its own CLI rather than the adapter's
bundled binary, so initialization tests must justify any range exception.

## Unpinned requirements in the rebuilt image

The installed inventory also found seven newer packages resolved by existing,
unchanged requirements. Compared with the immutable 7 October image, these are:

| Package | Previous | Rebuilt |
| --- | --- | --- |
| [anthropic](https://pypi.org/pypi/anthropic/1.12.0/json) | 1.11.0 | 1.12.0 |
| [boto3](https://pypi.org/pypi/boto3/1.43.109/json) | 1.43.108 | 1.43.109 |
| [botocore](https://pypi.org/pypi/botocore/1.43.109/json) | 1.43.108 | 1.43.109 |
| [jupyterlab-git](https://pypi.org/pypi/jupyterlab-git/0.55.0/json) | 0.54.1 | 0.55.0 |
| [jupyterlab-git-core](https://pypi.org/pypi/jupyterlab-git-core/0.55.0/json) | 0.54.1 | 0.55.0 |
| [litellm](https://pypi.org/pypi/litellm/1.104.1/json) | 1.104.0 | 1.104.1 |
| [tenacity](https://pypi.org/pypi/tenacity/9.2.1/json) | 9.1.4 | 9.2.1 |

An independent Codex review recommended retaining these resolutions. Published
requirements support the unchanged Python 3.13 and JupyterLab 4 environment;
the boto3/botocore pair satisfies its version range, and both images retain
httpx2 2.13.1. The rebuilt image passes `pip check`, and its frontend and server
extensions report enabled and healthy.

The same local probes passed in both images: repository initialization, status,
staging, commits and history through JupyterLab Git's exported core; Anthropic
request serialization and response parsing with a mock transport; modeled S3
requests with botocore's Stubber; Tenacity retry results and exception
propagation; and LiteLLM completion structure with a mock response. These do
not establish browser Git interaction, remote authentication, live provider
requests, streaming, or compatibility of unused optional extras. In particular,
LiteLLM's optional `proxy-runtime` extra requires Anthropic below 1; that extra
is not approved by this assessment.

Claude's plan, code and correction reviews completed before this inventory.
An additional Claude review of these resolutions could not run because the
account reached its weekly usage limit. The independent assessment and
installed checks above are separate evidence, not a completed Claude review.

## Validation and remaining limits

The [testing guide](../testing.md) defines the required full checkout suite and
installed subsystem checks. Checkout validation uses Python 3.12, Node 24,
released ASTRA dependencies, and the mandatory DOM packages.
The first host-Python 3.10 attempt failed during collection and is not a baseline.
The complete before and after suites each passed all 1,308 tests.
The live root version audits each checked 60 declarations with zero errors.
The focused workflow suite passed 117 tests, and Buildx's Dockerfile check
completed with no warnings.

Root pins require a live version audit, the audit unit tests, a built-image
inventory, and relevant runtime checks. Both native image architectures must
pass PR acceptance before merge. Pending, absent, or cancelled checks do not
establish compatibility.

PR acceptance exercises checkout, Buildx, local image builds on both
architectures, and CVMFS on amd64 Ubuntu 24.04. It does not exercise ARC Buildx with `network=host`,
image pushes, CVMFS/cache on Blacksmith or Ubuntu 22.04, agentic artifact
upload/download, DockerHub/Quay/backfill login, failure-reporting `workflow_run`,
or Kasm release jobs. CVMFS 5.6 updates its transitive cache Action to major 6,
even though its Node runtime remains 24.

Repository contract tests cover those workflow definitions, but do not replace
live runs of the unexercised paths. Their normal scheduled or manual workflows
remain the integration checks for those paths. The local GHCR login retry
wrapper invokes `docker login` directly and does not test `docker/login-action`.
