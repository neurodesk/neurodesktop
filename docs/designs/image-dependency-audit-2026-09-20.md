---
title: Image dependency audit, 20 September 2026
description: Live release audit of root image declarations, compatibility blockers, and source pins
parent: index.md
status: assessment
last-reviewed: "2026-09-20"
---

# Image dependency audit, 20 September 2026

This audit checked the root [Dockerfile](../../Dockerfile) at commit
`e3c31ec0fe7219bb73cd5873fff433e982b867f5` against live upstream registries.
No image pins changed. See the [audit design](image-dependency-upgrade.md),
[build behavior](../architecture/build.md), and [testing guide](../testing.md).

The existing auditor completed all 57 declarations with zero lookup errors.
It reported 15 update candidates, 36 current declarations, five compatibility
holds, and one manual check. Follow-up metadata checks found that two of the
15 candidates conflict with Jupyter AI, one needs a Codex adapter compatibility
exception, and one is an already-permitted LiteLLM update. That leaves 11
pinned candidates for build and runtime validation. These are available releases,
not a claim that the resulting image has passed integration tests.

## Candidates for build validation

| Dependency | Current declaration | Candidate | Validation needed |
| --- | --- | --- | --- |
| Jupyter base-notebook | 2026-09-07 | 2026-09-18 | Both image architectures, installed inventory, dependency solve, Jupyter extensions, container suite |
| Tomcat | 11.0.25 | 11.0.26 | Migrated Guacamole WAR, VNC and RDP sessions |
| code-server | 4.136.2 | 4.138.0 | Anchored build patches, proxy, editor launch |
| Apptainer gRPC override | 1.83.2 | 1.84.0 | Apptainer source build and runtime checks with pinned Go toolchain |
| OpenCode | 1.18.30 | 1.18.31 | CLI, web proxy, ACP initialization |
| Claude Agent ACP | 0.76.0 | 0.79.0 | SDK 0.3.257 becomes 0.3.274. Node requirement remains >=22. Test the external Claude executable through ACP and T3 |
| MyST pnpm | 11.26.0 | 11.27.0 | Retain pnpm <12 policy. Rebuild MyST with the frozen lockfile |
| jupyterlab-chat | 0.25.0 | 0.25.1 | Within Jupyter AI's <0.26 range. Check chat, collaboration, and widgets |
| Pydra | 1.0a10 | 1.0a11 | Intentional alpha track. Exercise workflow behavior |
| Snakemake | 9.26.1 | 9.27.0 | Upgrade main and Lightcone environments together. Keep packaging 25.0 |
| uv | 0.12.12 | 0.12.17 | Lightcone isolated tool installation and launch |

The source links and complete results appear below. LiteLLM 1.102.0 is also
available. The Dockerfile's `litellm>=1.100.1` already permits it when the pip
layer is rebuilt and the resolver allows it. There is no installed inventory
here to establish whether an existing deployed image is behind.

## Blocked or conditional updates

- **Jupyter Server MCP 0.4.0 and commands toolkit 0.3.0:**
  [Jupyter AI 3.2 metadata](https://pypi.org/pypi/jupyter-ai/3.2.0/json)
  requires `jupyter-server-mcp>=0.3.0,<0.4.0` and
  `jupyterlab-commands-toolkit>=0.2.0,<0.3.0`. Retain 0.3.0 and 0.2.0.
  The audit catalog does not encode these two holds and overstates their
  readiness to update.
- **Codex ACP 1.12.0:** its
  [published dependency metadata](https://registry.npmjs.org/@agentclientprotocol%2Fcodex-acp/1.12.0)
  requires `@openai/codex ^0.154.0`, which excludes the image's 0.155.1.
  The current adapter 1.11.0 declares `^0.153.4`, so the mismatch already
  exists. The image removes the adapter's bundled binary and uses its own
  CLI. An upgrade requires installed-image ACP and T3 initialization probes
  and a recorded deliberate exception, or an adapter release whose range
  covers the installed CLI.
- **Notebook Intelligence 5.4.0:**
  [its metadata](https://pypi.org/pypi/notebook-intelligence/5.4.0/json)
  pins `agent-client-protocol==0.10.1`, while
  [jupyter-ai-acp-client 0.3.0](https://pypi.org/pypi/jupyter-ai-acp-client/0.3.0/json)
  requires `>=0.11.0,<0.12.0`. Retain 5.3.1 and its source rebuild and patches.
- **MCP 2.2.0:** retain MCP 1.30.0 under the existing `<2` requirement.
  Notebook Intelligence still needs MCP v1.
- **packaging 26.3:**
  [Snakemake 9.27.0](https://pypi.org/pypi/snakemake/9.27.0/json)
  still requires `packaging>=24.0,<26`. Its update does not remove this hold.
- **ipykernel 7.3.0:** retain 6.31.0 under the project's widget comm policy
  until the documented widget and subshell checks justify changing it.
- **jupyter-collaboration 5.0.3:** retain the project's 4.x source rebuild
  policy. Jupyter AI's optional `rtc` extra also requires `<5`; this bound is
  extra-specific, rather than an unconditional requirement of the base
  `jupyter-ai` install.
- **pnpm 12.5.1:** retain the MyST pnpm 11 toolchain. The allowed 11.27.0
  update is listed above.

## Checks beyond the audit catalog

| Dependency | Finding | Evidence or next check |
| --- | --- | --- |
| CVMFS repository package | Current at 4.9 | Downloaded the [latest repository package](https://cvmrepo.s3.cern.ch/cvmrepo/apt/cvmfs-release-latest_all.deb) and read its Version field with `dpkg-deb -f`, without installing it |
| nf-test | 0.9.5 is current | [Latest upstream release](https://github.com/askimed/nf-test/releases/tag/v0.9.5) matches the inline installer argument |
| strace Julia binary | 6.7.0+1 is current in the selected binary repository | [Binary release](https://github.com/JuliaBinaryWrappers/strace_jll.jl/releases/tag/strace-v6.7.0%2B1). This does not establish freshness against upstream strace itself |
| hatchling | 1.27.0 held, 1.32.3 available | [Metadata](https://pypi.org/pypi/hatchling/1.32.3/json). Keep the build hold until fresh isolated builds pass |
| hatch-jupyter-builder | 0.9.1 held, 0.10.0 available | [Metadata](https://pypi.org/pypi/hatch-jupyter-builder/0.10.0/json). Test together with Hatchling on both Slurm and launcher wheel builds |
| jupyterlab-slurm source | Pinned revision is one commit behind upstream HEAD | [Comparison](https://github.com/NERSC/jupyterlab-slurm/compare/c34354f0aaa1b12f6243224bed631cf07c858409...HEAD). Review the change and builder patch before advancing |
| Lightcone agent-skills source | Pinned revision is 18 commits behind upstream HEAD | [Comparison](https://github.com/LightconeResearch/agent-skills/compare/4ded682be8487d8aa05831678ef84ef12068d50d...HEAD). Review skill behavior and installation checks before advancing |

The source comparisons are observations at audit time. Their HEAD links move.
The Jupyter collaboration source revision is tied to its pinned release and
local rebuild. Advancing it independently is not treated as a routine update.

## Complete declaration results

These are the auditor's raw classifications. “Allowed latest” means allowed
by the audit catalog, which is not a full dependency solver. Apply the blockers
above before choosing upgrades. Ranged declarations do not identify an installed
version. Pydra's stable upstream release is 0.25, while this image deliberately
uses the newer 1.0 prerelease line.

| Dependency and authority | Current declaration | Allowed latest | Latest stable upstream | Raw result |
| --- | --- | --- | --- | --- |
| [apache:tomcat/tomcat-11](https://downloads.apache.org/tomcat/tomcat-11/) | `11.0.25` | 11.0.26 | 11.0.26 | compatible-update-available |
| [github:anomalyco/opencode](https://github.com/anomalyco/opencode/releases) | `1.18.30` | 1.18.31 | 1.18.31 | update-available |
| [github:apache/guacamole-server](https://github.com/apache/guacamole-server/releases) | `1.6.0` | 1.6.0 | 1.6.0 | current |
| [github:apache/tomcat-jakartaee-migration](https://github.com/apache/tomcat-jakartaee-migration/releases) | `1.0.12` | 1.0.12 | 1.0.12 | current |
| [github:apptainer/apptainer](https://github.com/apptainer/apptainer/releases) | `1.5.3` | 1.5.3 | 1.5.3 | current |
| [github:cloudflare/cloudflared](https://github.com/cloudflare/cloudflared/releases) | `2026.9.1` | 2026.9.1 | 2026.9.1 | current |
| [github:coder/code-server](https://github.com/coder/code-server/releases) | `4.136.2` | 4.138.0 | 4.138.0 | update-available |
| [github:cvmfs/cvmfs](https://github.com/cvmfs/cvmfs/releases) | `2.14.1+ubuntu24.04` | 2.14.1 | 2.14.1 | current |
| [github:tailscale/tailscale](https://github.com/tailscale/tailscale/releases) | `1.102.4` | 1.102.4 | 1.102.4 | current |
| [go:golang.org/x/crypto](https://proxy.golang.org/golang.org/x/crypto/@v/list) | `0.57.0` | 0.57.0 | 0.57.0 | current |
| [go:google.golang.org/grpc](https://proxy.golang.org/google.golang.org/grpc/@v/list) | `1.83.2` | 1.84.0 | 1.84.0 | update-available |
| [golang:go](https://go.dev/dl/) | `1.27.1` | 1.27.1 | 1.27.1 | current |
| [manual:cvmfs-release](https://cvmrepo.s3.cern.ch/cvmrepo/apt/cvmfs-release-latest_all.deb) | `4.9` | Manual | Manual | manual-review |
| [npm:@agentclientprotocol/claude-agent-acp](https://registry.npmjs.org/@agentclientprotocol%2Fclaude-agent-acp) | `0.76.0` | 0.79.0 | 0.79.0 | update-available |
| [npm:@agentclientprotocol/codex-acp](https://registry.npmjs.org/@agentclientprotocol%2Fcodex-acp) | `1.11.0` | 1.12.0 | 1.12.0 | update-available |
| [npm:@anthropic-ai/claude-code](https://registry.npmjs.org/@anthropic-ai%2Fclaude-code) | `2.1.278` | 2.1.278 | 2.1.278 | current |
| [npm:@jupyter/builder](https://registry.npmjs.org/@jupyter%2Fbuilder) | `1.2.3` | 1.2.3 | 1.2.3 | current |
| [npm:@jupyter/ydoc](https://registry.npmjs.org/@jupyter%2Fydoc) | `4.1.1` | 4.1.1 | 4.1.1 | current |
| [npm:@jupyterlab/builder](https://registry.npmjs.org/@jupyterlab%2Fbuilder) | `4.5.10` | 4.5.10 | 4.5.10 | current |
| [npm:@openai/codex](https://registry.npmjs.org/@openai%2Fcodex) | `0.155.1` | 0.155.1 | 0.155.1 | current |
| [npm:npm](https://registry.npmjs.org/npm) | `12.0.2` | 12.0.2 | 12.0.2 | current |
| [npm:pnpm](https://registry.npmjs.org/pnpm) | `11.26.0` | 11.27.0 | 12.5.1 | compatible-update-available |
| [npm:t3](https://registry.npmjs.org/t3) | `0.0.42` | 0.0.42 | 0.0.42 | current |
| [npm:tar](https://registry.npmjs.org/tar) | `7.5.22` | 7.5.22 | 7.5.22 | current |
| [oci:quay.io/jupyter/base-notebook](https://quay.io/repository/jupyter/base-notebook?tab=tags) | `2026-09-07` | 2026-09-18 | 2026-09-18 | update-available |
| [pypi:anywidget](https://pypi.org/pypi/anywidget/json) | `0.11.0` | 0.11.0 | 0.11.0 | current |
| [pypi:astra-spec](https://pypi.org/pypi/astra-spec/json) | `0.0.14` | 0.0.14 | 0.0.14 | current |
| [pypi:astra-tools](https://pypi.org/pypi/astra-tools/json) | `0.2.17` | 0.2.17 | 0.2.17 | current |
| [pypi:chardet](https://pypi.org/pypi/chardet/json) | `<8` | 7.6.0 | 7.6.0 | current |
| [pypi:ipykernel](https://pypi.org/pypi/ipykernel/json) | `==6.31.0` | 6.31.0 | 7.3.0 | held |
| [pypi:ipyniivue](https://pypi.org/pypi/ipyniivue/json) | `2.4.4` | 2.4.4 | 2.4.4 | current |
| [pypi:ipywidgets](https://pypi.org/pypi/ipywidgets/json) | `==8.1.9` | 8.1.9 | 8.1.9 | current |
| [pypi:jupyter-ai](https://pypi.org/pypi/jupyter-ai/json) | `3.2.0` | 3.2.0 | 3.2.0 | current |
| [pypi:jupyter-ai-acp-client](https://pypi.org/pypi/jupyter-ai-acp-client/json) | `==0.3.0` | 0.3.0 | 0.3.0 | current |
| [pypi:jupyter-ai-chat-commands](https://pypi.org/pypi/jupyter-ai-chat-commands/json) | `==0.0.4` | 0.0.4 | 0.0.4 | current |
| [pypi:jupyter-ai-persona-manager](https://pypi.org/pypi/jupyter-ai-persona-manager/json) | `==0.2.0` | 0.2.0 | 0.2.0 | current |
| [pypi:jupyter-ai-router](https://pypi.org/pypi/jupyter-ai-router/json) | `==0.1.1` | 0.1.1 | 0.1.1 | current |
| [pypi:jupyter-ai-tools](https://pypi.org/pypi/jupyter-ai-tools/json) | `==0.7.0` | 0.7.0 | 0.7.0 | current |
| [pypi:jupyter-collaboration](https://pypi.org/pypi/jupyter-collaboration/json) | `4.4.2` | 4.4.2 | 5.0.3 | held |
| [pypi:jupyter-server-documents](https://pypi.org/pypi/jupyter-server-documents/json) | `==0.3.3` | 0.3.3 | 0.3.3 | current |
| [pypi:jupyter-server-mcp](https://pypi.org/pypi/jupyter-server-mcp/json) | `==0.3.0` | 0.4.0 | 0.4.0 | update-available |
| [pypi:jupyter-server-proxy](https://pypi.org/pypi/jupyter-server-proxy/json) | `==4.5.0` | 4.5.0 | 4.5.0 | current |
| [pypi:jupyterlab-chat](https://pypi.org/pypi/jupyterlab-chat/json) | `==0.25.0` | 0.25.1 | 0.25.1 | update-available |
| [pypi:jupyterlab-commands-toolkit](https://pypi.org/pypi/jupyterlab-commands-toolkit/json) | `==0.2.0` | 0.3.0 | 0.3.0 | update-available |
| [pypi:jupyterlab-myst](https://pypi.org/pypi/jupyterlab-myst/json) | `==2.7.0` | 2.7.0 | 2.7.0 | current |
| [pypi:jupyterlab-niivue](https://pypi.org/pypi/jupyterlab-niivue/json) | `==0.2.7` | 0.2.7 | 0.2.7 | current |
| [pypi:jupyterlab-notebook-awareness](https://pypi.org/pypi/jupyterlab-notebook-awareness/json) | `==0.2.0` | 0.2.0 | 0.2.0 | current |
| [pypi:jupyterlab-widgets](https://pypi.org/pypi/jupyterlab-widgets/json) | `==3.0.17` | 3.0.17 | 3.0.17 | current |
| [pypi:lightcone-cli](https://pypi.org/pypi/lightcone-cli/json) | `0.4.2` | 0.4.2 | 0.4.2 | current |
| [pypi:litellm](https://pypi.org/pypi/litellm/json) | `>=1.100.1` | 1.102.0 | 1.102.0 | compatible-update-available |
| [pypi:mcp](https://pypi.org/pypi/mcp/json) | `>=1.30.0,<2` | 1.30.0 | 2.2.0 | held |
| [pypi:notebook-intelligence](https://pypi.org/pypi/notebook-intelligence/json) | `==5.3.1` | 5.3.1 | 5.4.0 | held |
| [pypi:packaging](https://pypi.org/pypi/packaging/json) | `==25.0` | 25.0 | 26.3 | held |
| [pypi:pydra](https://pypi.org/pypi/pydra/json) | `==1.0a10` | 1.0a11 | 0.25 | compatible-update-available |
| [pypi:requests](https://pypi.org/pypi/requests/json) | `>=2.34.2` | 2.34.2 | 2.34.2 | current |
| [pypi:snakemake](https://pypi.org/pypi/snakemake/json) | `9.26.1` | 9.27.0 | 9.27.0 | update-available |
| [pypi:uv](https://pypi.org/pypi/uv/json) | `0.12.12` | 0.12.17 | 0.12.17 | update-available |

## Reproduction and coverage limits

Run the full declaration audit and its tests from the repository root:

```bash
python scripts/audit_image_versions.py --format json
pytest -q tests/unit/test_audit_image_versions.py
```

The live command exited 0 with no errors. All five audit tests passed.
The initial sandboxed attempt failed DNS resolution. The completed run used
network-enabled execution and queried live package registries.

Docker image discovery found no local build of the root Neurodesktop image.
The separate `neurodesktop-agentic:codex-0.153.4` worker image is not a valid
inventory baseline for it. No image build, installed-package inventory,
`pip check`, browser checks, or container tests ran as part of this assessment.

APT packages, unpinned pip and conda requirements, Nextflow, TinyTeX, Firefox,
Node 24 packages, mutable neurocommand and nf-neuro source checkouts, plugin
marketplaces, and transitive dependencies need an actual built-image inventory.
A fresh uncached install may select newer versions without a Dockerfile change.
Cached layers can retain older versions. The source audit cannot distinguish
those cases or prove that all deployed packages are current.

Before shipping upgrades, build both supported architectures, record resolved
versions, run `pip check` and Jupyter extension validation, and run the
[container checks](../testing.md) selected by the changed subsystems. CLI and
adapter updates require initialization probes using the image-owned executable
paths. Base image upgrades require the broader container suite.

## Upgrade follow-up

The subsequent upgrade changes the 11 candidates above, Codex ACP to 1.12.0,
and the LiteLLM lower bound to 1.102.0. It also selects Slurm source revision
`8dccb39808f8a1b77712a9a5773a7d2601a56683`, Hatchling 1.32.3, and
hatch-jupyter-builder 0.10.0 for build validation. The newer Slurm source
uses the current Jupyter builder itself, so the package-rename patch is removed.

The Lightcone skills pin remains held. Upstream HEAD removes the reproduction
plugin and its four-skill layout in favor of a Lightcone prerelease workflow.
Changing it would replace installed user functionality, rather than refresh
the existing compatible plugin.

The audit catalog now includes Jupyter AI's MCP-server and commands-toolkit
bounds. The post-edit live audit completed without lookup errors and reported
no remaining available updates within its compatibility policy. Dockerfile
validation passed without warnings, and the new base tag publishes both
amd64 and arm64 manifests. The local Docker builder supports amd64 only.

The full checkout suite passed all 773 tests in the Python 3.12 worker image
as an unprivileged user. The inline `shell-quote` security override was checked
separately and remains current at 1.10.0.

The final amd64 image built successfully as
`neurodesktop:dependency-upgrade-20260920`, image ID
`sha256:d9b3b9a5906b96e6272ef2ff8921aa02eb65aed9fa6c52ac0d9ee5b693fb53f1`.
Both fresh isolated wheel builds passed. The first image attempt failed on
missing Go compiler-cache objects; the retry completed without a source change.

The installed inventory confirmed JupyterLab 4.6.3, Notebook 7.6.2, Slurm 4.1.0,
jupyterlab-chat 0.25.1, Pydra 1.0a11, Snakemake 9.27.0, uv 0.12.17, and LiteLLM
1.102.0. Node resolved to 24.21.0 and Tomcat reported 11.0.26. The held packages
resolved to packaging 25.0 and ipykernel 6.31.0. Python, conda, dpkg, and global
npm inventories were captured. `pip check` found no broken requirements, and
Jupyter server and frontend extension validation reported no errors or
incompatibilities.

The full installed-image suite passed **171 tests**, with **16 skips** and one
WebGL warning, in a disposable desktop container with CVMFS disabled and sudo
enabled. The suite ran as `jovyan`. The stronger ACP probes passed for Codex,
Claude, and OpenCode, and the T3 provider probes passed for fresh defaults and
the exact legacy-default migration. A final test-only refinement also requires
the authentication-required message, rather than accepting its error code alone.
After rebuilding that test layer, all 12 focused coding-agent and T3 image tests
passed again. The full suite result precedes this stricter assertion.

The Codex adapter exception is accepted for this tested combination: adapter
1.12.0 declares `^0.154.0`, but the image uses CLI 0.155.1 after removing the
bundled executable. The ACP test records invocation of the image binary,
initializes the protocol, and requests a session without credentials. Its
expected authentication-required response confirms local bootstrap without a
model prompt. This evidence covers initialization, not authenticated inference.

Validation limits remain: no arm64 builder was available, the CVMFS-enabled
and HPC startup matrix was not run, and headless Firefox had no WebGL2 context.
The widget browser test therefore checked stream output and delayed-widget
replay without NiiVue rendering. No registry image was published.

The final image and desktop checks can be reproduced with:

```bash
docker buildx build --load -t neurodesktop:dependency-upgrade-20260920 .
docker run -d --shm-size=1gb --privileged --user=root \
  --name neurodesktop-dependency-upgrade-test \
  -e CVMFS_DISABLE=true -e GRANT_SUDO=yes \
  -e NEURODESKTOP_CVMFS_STARTUP_MODE=eager \
  neurodesktop:dependency-upgrade-20260920
# Wait for Jupyter on port 8888 inside the container before running the suite.
docker exec -u jovyan -e NEURODESKTOP_TEST_ALLOW_GLOBAL_DESKTOP_SERVICES=1 \
  neurodesktop-dependency-upgrade-test pytest /opt/tests/ -q
docker exec -u jovyan neurodesktop-dependency-upgrade-test pip check
docker exec -u jovyan neurodesktop-dependency-upgrade-test jupyter server extension list
docker exec -u jovyan neurodesktop-dependency-upgrade-test jupyter labextension list --verbose
docker rm -f neurodesktop-dependency-upgrade-test
```
