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

The live audit on 16 October checked 60 direct declarations. After the upgrade,
it reported no available compatible updates and no registry errors. The CVMFS
repository bootstrap remains a manual review entry. Its release 4.9 supports
the new Ubuntu repository, which supplies the selected CVMFS client package.

| Component | Previous | Selected |
| --- | --- | --- |
| Jupyter base image | 2026-09-18 | 2026-10-12 |
| CVMFS Ubuntu package | 2.14.1+ubuntu24.04 | 2.14.1+ubuntu26.04 |
| code-server | 4.138.0 | 4.141.0 |
| T3 Code | 0.0.45 | 0.1.0 |
| Lightcone | 0.4.2 | 0.5.0 |
| Notebook Intelligence | 5.3.1 | 6.2.0 |
| Codex | 0.155.1 | 0.163.0 |
| Claude Code | 2.1.278 | 2.1.301 |
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

## Base image and Notebook Intelligence

The new base contains Ubuntu 26.04, Python 3.13, and JupyterLab 4.6. The desktop
build uses FreeRDP 3 and the corresponding runtime libraries. Guacamole 1.6
needs C17 mode, const-aware libc source changes, and removal of its conflicting
feature-test macro. The builder verifies that the RDP plugin exists.
See [build-time behavior](../architecture/build.md) for the current contract.

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

## Verification record

Before integration, the full unit suite passed 1,688 tests with one expected
root-boundary skip. The complete Python request set resolved 425 distributions
against the new base interpreter, and all 135 apt requests passed a simulated
install with the Ubuntu 26 CVMFS repository. Actual upgraded ACP adapters
initialized Codex, Claude Code, and OpenCode without model credentials.

The Guacamole builder completed. A real library probe exercised six bounded
string-search cases and loaded the RDP plugin. Both official T3 platform
archives passed integrity verification, and the x64 executable reported 0.1.0.

The integrated full image, installed Slurm and CVMFS science checks,
final unit and quality checks, and native arm64 PR image
acceptance remain delivery gates. Passing local-worker fixtures does not
establish those results. The PR records their final outcomes.
