---
title: Design records
description: Assessments, implementation plans, and audits kept as the record
  of why the tree is shaped the way it is
parent: ../index.md
status: current
last-reviewed: "2026-10-08"
---

# Design records

These documents are records, not living reference: each was accurate at its
snapshot date and is kept to explain why things are the way they are. For
current behavior see [Architecture](../architecture.md),
[Testing](../testing.md), and
[Environment variables](../environment-variables.md).

| Record | Status | What it decided |
| --- | --- | --- |
| [GPU tool integration checklist](gpu-tool-integration.md) | proposed | Install VirtualGL in the FreeSurfer image, preserve injected driver libraries, and explicitly forward rendering controls through Neurocommand's clean environment |
| [GPU desktop session assessment](gpu-desktop-sessions.md) | proposed | Assess VirtualGL EGL with existing VNC/Guacamole, nested Apptainer launch requirements, GPU allocation, and hardware acceptance gates |
| [Kasm workspace assessment](kasm-workspace-assessment.md) | research | Compare a native Neurodesktop Kasm image with an external VNC connection, and identify startup, profile, CVMFS, and Apptainer validation needs |
| [Dependency dashboard upgrades, 8 October 2026](dependency-dashboard-upgrades-2026-10-08.md) | implemented | Select Claude-reviewed build and utility upgrades, preserve compatibility holds, and defer coupled runtime migrations |
| [Image dependency audit, 20 September 2026](image-dependency-audit-2026-09-20.md) | assessment | Live release results, update candidates, dependency conflicts, and coverage limits |
| [Image security audit](image-security-audit.md) | assessed | Identify credential, shared-host isolation, privilege, and release-gating improvements |
| [Image packaging and Lightcone environment audit](image-packaging-and-lightcone.md) | implemented | Remove payloads before layer commits, update selected releases, and test whether Lightcone can share the main dependency set |
| [Image dependency upgrade audit](image-dependency-upgrade.md) | implemented | Derive current image pins from the root Dockerfile, keep only authority and compatibility policy in the catalog, and report upstream and compatible releases separately |
| [T3 Code remote desktop assessment](t3-code-remote-assessment.md) | implemented | Let Jupyter own an optional headless T3 server and connect through T3 Connect or private networking |
| [Subscription-based agentic workflow redesign](agentic-subscription-redesign.md) | implemented | Use the existing runner and Codex subscription for issue repairs, five weekly maintenance categories, independent validation, and draft PR publication |
| [Codex authentication research](agentic-codex-auth-research.md) | research | Verify subscription CLI behavior, gh-aw authentication limits, and the explicit public-repository caveat in official CI guidance |
| [Agentic workflow reliability remediation](agentic-workflow-reliability-remediation.md) | implemented | Size hard invocation ceilings from transcripts, split diagnosis from fixing, rotate maintenance, and stop conflating scheduled infrastructure failures with agent failures |
| [ASTRA and Lightcone integration](astra-lightcone-integration.md) | implemented | Adopt the ASTRA specification layer and build the read-only provenance viewer; defer Lightcone execution behind explicit upstream blockers |
| [OpenCode web interface plan](opencode-integration-plan.md) | implemented | Ship the official OpenCode web UI behind a rewriting reverse proxy with browser-based key setup |
| [Test behavior audit, 3 October 2026](test-behavior-audit-2026-10-03.md) | applied | Replace circular and source-only tests with HTTP, DOM, CLI, and installed workflow checks; record remaining application acceptance gaps |
| [Test suite audit](test-suite-audit.md) | applied | Split the suite into checkout-runnable `tests/unit/` and image-only `tests/container/` |
| [Distributed compute broker design](distributed-compute-broker.md) | proposed | Production design for JupyterHub → Forgejo Actions → site-local dispatchers → SLURM/Kubernetes with DataLad-managed data |
