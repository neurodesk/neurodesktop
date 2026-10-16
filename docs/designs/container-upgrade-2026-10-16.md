---
title: Container package and Lightcone upgrade, 16 October 2026
description: Release audit, Ubuntu compatibility changes, and the Lightcone 0.5 execution contract
parent: index.md
status: implemented
last-reviewed: "2026-10-16"
---

# Container package and Lightcone upgrade, 16 October 2026

The request covers the root Neurodesktop image, especially T3 Code, an explicit
Lightcone 0.5 migration, and Ollama chat through T3 using the local Docker-host
endpoint. The root [Dockerfile](../../Dockerfile) owns current versions.
The [release audit](../../scripts/audit_image_versions.py) records package
authorities and compatibility policy. This record captures the migration.

## Release decisions

The live audit on 16 October checked 61 direct declarations. After the upgrade,
it reported no available compatible updates and no registry errors. The CVMFS
repository bootstrap and Slurm compatibility source require manual review.
Bootstrap release 4.9 supports the new Ubuntu repository, which supplies the
selected CVMFS client package. The Slurm source must match the installed ABI.

| Component | Previous | Selected |
| --- | --- | --- |
| Jupyter base image | 2026-09-18 | 2026-10-12 |
| CVMFS Ubuntu package | 2.14.1+ubuntu24.04 | 2.14.1+ubuntu26.04 |
| code-server | 4.138.0 | 4.141.0 |
| T3 Code | 0.0.45 | 0.1.0 |
| Lightcone | 0.4.2 | 0.5.0 |
| Notebook Intelligence | 5.3.1 | 6.2.0 |
| Codex | 0.155.1 | 0.163.0 |
| Claude Code | 2.1.278 | 2.1.302 |
| OpenCode | 1.18.31 | 1.19.3 |
| Codex ACP | 1.12.0 | 2.2.2 |
| Claude ACP | 0.79.0 | 0.92.0 |
| Tailscale | 1.102.4 | 1.104.1 |
| cloudflared | 2026.9.1 | 2026.10.1 |
| Tomcat | 11.0.26 | 11.0.27 |
| npm | 12.2.0 | 12.2.1 |
| uv | 0.13.0 | 0.13.1 |

The upgrade also refreshes Jupyter Server Proxy, ACP chat, persona management,
JupyterLab chat, LiteLLM, the collaboration builder, and the MyST pnpm 11
toolchain. The Python cache revision changes so unpinned direct requests are
resolved again.

The remaining holds retain Jupyter Collaboration 4, the Jupyter AI-compatible
server MCP and commands toolkit, ipykernel 6, MCP 1, packaging 25 for Snakemake,
and pnpm 11 for MyST. These policies remain in the audit catalog. Lightcone has
no version hold. Codex 0.163 exceeds Codex ACP 2.2.2's declared `^0.160.1`
dependency range. The image deliberately removes the adapter's bundled agent
and selects the global executable; real ACP and T3 initialization are required
checks for this exception.

The final audit detected Claude Code 2.1.302 after the initial image build.
Both native architecture binaries matched the official manifest. The updated
amd64 binary passed the installed ACP and T3 checks; fresh and legacy T3
defaults reported the new version and the same expected authentication-required
state as 2.1.301 in an empty credential store.

## Base image and Notebook Intelligence

The new base contains Ubuntu 26.04, Python 3.13, and JupyterLab 4.6. The desktop
build uses FreeRDP 3 and the corresponding runtime libraries. Guacamole 1.6
needs C17 mode, const-aware libc source changes, and removal of its conflicting
feature-test macro. The builder verifies that the RDP plugin exists.
See [build-time behavior](../architecture/build.md) for the current contract.

Native amd64 and arm64 acceptance then exposed a FreeRDP 3.32.1 crash before
the RDP session could draw. The build backports
[GUACAMOLE-2328](https://github.com/apache/guacamole-server/commit/ab36756b596520ae2a94cd4d25e406e0b5aa820b)
to initialize GDI and its dependent handlers after the connection cache exists.
The adapted patch preserves Guacamole 1.6.0's channel-loading order. The next
RDP probe exposed an xrdp package configuration mismatch: its daemon runs as
`xrdp`, but sesman assigned the private session socket directory to `root`.
Setting `SessionSockdirGroup=xrdp` restored transport without granting other
users access. A thin image overlay passed actual desktop pixels, a command
launched through Guacamole keyboard input, and private socket checks.

TigerVNC 1.15 requires an RFB endpoint even for local X11 display tests. The
shared test driver now supplies a private Unix socket while keeping TCP
disabled. Guacamole startup also creates the config parent before TigerVNC
migrates legacy homes. Installed tests cover fresh homes, saved settings,
absolute and relative XDG overrides, and localhost-only listener bindings.
The strict WebGL journey passed with the repaired display driver.

Lmod 8.7.60 moved its user cache while startup still invalidated the legacy
directory. The helper now queries Lmod's actual configuration and creates
new cache files with a private umask. Real tests with two notebook users and
a site override verified cache reuse, discovery after module changes,
ownership, and separation from root's environment. The installed provenance
fixture also now edits YAML declarations instead of relying on an obsolete
resource-line substitution. Independent review approved both fixes.

The first installed-image `pip check` found a conflict that the standalone
Python solve missed. Conda 26.9.2 requires `packaging>=26.3`, whereas
Snakemake and its interface packages require `<26`. Snakemake now uses the
existing isolated-tool mechanism with its private `packaging` 25.0. The main
environment retains Conda's compatible release. A disposable image probe
passed `pip check` and executed a real Snakemake workflow after this split.
Installed-image dependency consistency is now a required container test.

The science probe also found that the Debian-built Apptainer required
`libsubid.so.4`, whereas the Ubuntu 26 runtime supplies `libsubid.so.5`.
Apptainer now compiles on the same Ubuntu base as the runtime, using the
pinned official Go SDK. The final runtime layer starts the executable to
reject an incompatible library set during the build.
The matching builder and runtime passed a CVMFS FSL arithmetic probe with
every output voxel checked. Native Lightcone science acceptance remains a
separate gate below.

Slurm 25.11 ignores the old login-environment timeout setting and terminated
its worker when ordinary Docker permissions denied namespace creation. A
source-anchored compatibility library routes that denial to upstream's existing
per-user fork fallback. Only a private local worker links it; package hashes
guard its ABI, and host-mode clients retain their original executables.
The configuration also stores job comments so Lightcone can verify ownership
after an allocation ends. Default-permission tests cover export rules,
two users, reconfiguration, retrieval failures, and completed/cancelled tokens.
See [the local Slurm contract](../../config/slurm/README.md).

Notebook Intelligence 6.2 accepts the ACP protocol range used by Jupyter AI's
current client. Its published launcher bundle supports this JupyterLab, so the
obsolete source rebuild and large checked-in lockfile are removed. One package
bundle and an application symlink remain. The settings refresh and Ollama
context patches are ported to the published release and tested against actual
bundle and Python source seams.

## Lightcone execution and evidence

Lightcone 0.5 owns its compute lifecycle. The image helper launches a native
allocation, waits for readiness, materializes requested targets, and releases
only the full allocation ID returned to that invocation. It rejects invocation
inside an existing Slurm allocation. The old batch wrapper and its callback
patch are removed.

Stock 0.5 sandbox execution resets the search path and cannot expose Neurodesk's
CVMFS module tools. Scientific projects therefore declare ordered exact module
pins in committed project metadata. The worker reconstructs that environment.
The pins contribute to Lightcone's environment identity, and the execution
manifest records the actual open filesystem boundary. Projects without that
explicit profile retain stock Lightcone sandbox selection. This choice follows
three independent design candidates and a separate judge; a blanket sandbox
disable would hide the boundary for ordinary projects.

The viewer reads real 0.5 status snapshots, canonical manifests, and RO-Crate
relationships. It distinguishes historical execution from current freshness
and successful verification. Missing selected-universe evidence stays
spec-only. It validates artifact confinement and available content hashes and
does not infer verification from a completed action. Captured real-worker
artifacts live in [the 0.5 fixture](../../tests/fixtures/lightcone-0.5/README.md).
See [ASTRA architecture](../architecture/astra.md) for usage.

Independent review rejected invented nested-output evidence. Lightcone 0.5
rejects ASTRA's dotted child IDs itself, so the viewer enforces that producer
boundary and leaves child analyses unexecuted rather than translating IDs.
Native image testing also exposed incompatible default connection storage
under the image's group-writable home. The image uses a private per-user
temporary root while preserving upstream ownership and ancestor checks.
Committed module profiles are read relative to their project directory,
including projects nested inside a larger Git repository. A real nested
producer, worker, and DataLad rerun passed independent review.

## T3 and local Ollama

The interactive OpenCode wrapper and T3's quiet launcher share bounded Ollama
tags and context discovery. Quiet `serve` and ACP launches pass a bounded
observation to a native config hook that runs separately for each project.
The hook replaces shared provider maps so opening a later project preserves
its custom endpoint, model limits, and selected models. It leaves inline
configuration unchanged and never sends resolved values through interpolation
again. Discovered names and endpoints containing config references are rejected.
Neither path writes the user's configuration during a chat or prints discovery
responses.

A real T3 0.1.0 chat completed through OpenCode 1.19.3 and the official Ollama
0.40.5 Docker image. It discovered `qwen3:0.6b` using the default
`http://host.docker.internal:11434` address and returned `OLLAMA_OK` in one
attempt. This workspace used a temporary Docker-network alias for that default
hostname. The live fixture disabled auxiliary title generation to keep the
CPU-backed test focused on chat completion. A controlled backend separately
proved completion while another quiet server starts, without changing the
persistent configuration or restarting the active attempt.

The immutable integrated image later completed another live T3 turn with
assistant text `HELLO!`, one completed run, and one completed attempt. It used
the same default Docker hostname and unchanged official backend configuration.
The backend was warm from a direct probe. Earlier final-image attempts timed
out, and the successful run still logged prompt truncation; the timeout's
cause remains unproved. Model-advertised context and Ollama's effective
OpenAI-compatible context are separate settings. This result establishes a
completed live integration, not cold-start or large-context performance.

## Verification record

Before integration, the full unit suite passed 1,688 tests with one expected
root-boundary skip. The complete Python request set resolved 425 distributions
against the new base interpreter, and all 135 apt requests passed a simulated
install with the Ubuntu 26 CVMFS repository. Actual upgraded ACP adapters
initialized Codex, Claude Code, and OpenCode without model credentials.

The Guacamole builder completed. A real library probe exercised six bounded
string-search cases and loaded the RDP plugin. Both official T3 platform
archives passed integrity verification, and the x64 executable reported 0.1.0.

The first integrated image built successfully. Its 1,767-unit suite and all
quality gates passed. Installed inventory verified 450 main Python packages,
515 Conda records, 909 OS packages, five global npm packages, and the isolated
Lightcone and Snakemake environments. All three Python dependency checks passed.

Native regular acceptance failed the same 14 tests on local amd64 and both CI
architectures. Those failures produced the desktop, cache, and fixture fixes
above. The repaired thin overlays passed their relevant runtime checks; a full
rebuild, integrated unit and quality checks, all native profiles, and current
arm64 CI remain delivery gates. Passing overlay checks does not establish
those results. The PR records their final outcomes.
