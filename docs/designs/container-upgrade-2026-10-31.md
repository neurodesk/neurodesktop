---
title: Container package upgrade, 31 October 2026
description: Release audit and Renovate backlog applied ahead of the weekly schedule
parent: index.md
status: implemented
last-reviewed: "2026-10-31"
---

# Container package upgrade, 31 October 2026

This upgrade applies the root image's pending Renovate updates before their
Monday schedule, plus the remaining direct pins from the
[release audit](../../scripts/audit_image_versions.py). The root
[Dockerfile](../../Dockerfile) owns current versions.

## Release decisions

| Component | Previous | Selected |
| --- | --- | --- |
| Jupyter base image | 2026-10-18 | 2026-10-28 |
| Dockerfile frontend | 1.27 | 1.28 |
| code-server | 4.141.0 | 4.144.0 |
| Codex | 0.163.0 | 0.166.0 |
| Claude Code | 2.1.302 | 2.1.316 |
| OpenCode | 1.19.3 | 1.19.5 |
| Codex ACP | 2.2.2 | 2.5.0 |
| Claude ACP | 0.92.0 | 0.99.1 |
| Tailscale | 1.104.1 | 1.104.4 |
| cloudflared | 2026.10.1 | 2026.10.7 |
| Apptainer grpc module | 1.84.0 | 1.85.0 |
| Notebook Intelligence | 6.2.0 | 6.3.0 |
| Jupyter AI ACP client | 0.3.3 | 0.3.4 |
| JupyterLab chat | 0.25.2 | 0.25.3 |
| Jupyter AI persona manager | 0.2.2 | 0.2.3 |
| Pydra | 1.0a11 | 1.0a12 |
| certifi | 2026.10.19 | 2026.10.23 |
| uv | 0.13.4 | 0.13.6 |
| Snakemake | 9.27.0 | 9.27.1 |
| MyST pnpm | 11.28.8 | 11.28.10 |
| Hatchling | 1.32.4 | 1.33.0 |
| Launcher JupyterLab packages | 4.6.4 | 4.6.5 |

The Python cache revision changes so unpinned direct requests resolve again.
The Tailscale and cloudflared checksums match both the published values and
downloaded artifacts for each architecture.

Codex 0.166 exceeds Codex ACP 2.5.0's declared `^0.165.0` range. As in the
previous upgrade, the image removes the adapter's bundled agent and drives the
global executable; installed-image ACP and T3 initialization probes validate
that exception. Claude ACP 0.99.1 bundles Agent SDK 0.3.312 (Claude Code
2.1.312) and drives the image's 2.1.316 binary the same way.

Notebook Intelligence 6.3.0 translates its command labels, which moved the
settings command anchor. The patch now accepts the translator call and
keeps it intact. The ACP client 0.3.4 change is limited to telemetry, so its
logging seams are unchanged.

## Base image solver regression

Native arm64 acceptance failed the local-channel Conda checks with
`CondaMemoryError: std::bad_alloc`. The new base moves libmamba from 2.8.1 to
2.10.0. The failure reproduced on amd64 with the unmodified base: any
`file://` channel fails, including an empty one, and the `mamba` binary fails
the same way. Conda's classic solver and `conda-forge` solves still work.
libmamba 2.9.0 on the same base passes all six installed checks, so the image
pins that line and disallows exactly 2.10.0.

## Holds

- `jupyter-ai-persona-manager` moves to 0.2.3; 0.3.0 is excluded because Jupyter AI 3.2.0
  requires `<0.3.0`. Renovate's Jupyter AI group had proposed it.
- OpenCode 2 is held. Its 2.2 releases change the plugin SDK that the image's
  Ollama and Lightcone plugins target, and T3 0.1.5's OpenCode start contract
  has not been validated against it. It needs its own migration.
- `@jupyter/builder` remains at 1.2.3 because that pin mirrors the
  devDependency range declared by the pinned `jupyterlab-slurm` revision.

Existing holds for Jupyter Collaboration 4, the Jupyter AI server MCP and
commands toolkit, ipykernel 6, MCP 1, packaging 25 and pnpm 11 are unchanged.
